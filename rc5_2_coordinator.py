"""RC5.2 Phase 2 R4: Coordinator — SQLite-backed, no global state."""
import json, sqlite3, hashlib, base64, time
from rc5_2_receipt import verify_receipt
from rc5_2_tensor_bundle import delta_bundle_unpack, bundle_sha256
from rc5_2_canonical import canonical_json_v1

class Coordinator:
    def __init__(self, db_path="coordinator.db"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS contributions (
                cid TEXT PRIMARY KEY, status TEXT DEFAULT 'RECEIVED',
                unit_id TEXT, run_id TEXT, round_id TEXT, worker_id TEXT,
                delta_bundle_b64 TEXT, delta_bundle_sha256 TEXT,
                receipt_json TEXT, ett INTEGER DEFAULT 0,
                nonce_id TEXT, created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS nonces (
                nonce_id TEXT PRIMARY KEY, consumed INTEGER DEFAULT 0,
                cid TEXT, created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rounds (
                run_id TEXT, round_id TEXT,
                global_adapter_b64 TEXT, global_adapter_hash TEXT,
                closed INTEGER DEFAULT 0, PRIMARY KEY (run_id, round_id)
            );
            CREATE TABLE IF NOT EXISTS units (
                unit_id TEXT PRIMARY KEY, state TEXT,
                receipt_json TEXT, run_id TEXT, round_id TEXT,
                assignment_id TEXT, micro_unit_id TEXT, worker_id TEXT, session_id TEXT,
                worker_model_hash TEXT, partition_schema_hash TEXT, base_adapter_hash TEXT,
                adapter_schema_hash TEXT, numerical_profile_hash TEXT,
                update_id TEXT, delta_id TEXT, ett INTEGER, loss REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self._conn.commit()

    def _check_nonce(self, nonce_id):
        r = self._conn.execute("SELECT consumed FROM nonces WHERE nonce_id=?", (nonce_id,)).fetchone()
        return r is None or r["consumed"] == 0

    def _consume_nonce(self, nonce_id, cid):
        self._conn.execute("INSERT OR IGNORE INTO nonces (nonce_id, consumed, cid) VALUES (?, 1, ?)", (nonce_id, cid))
        self._conn.commit()

    def checkpoint_upload(self, p: dict, signing_key: bytes) -> dict:
        receipt = p.get("receipt", {})
        # R11-6: unit must exist and be COMMITTED (authoritative state)
        uid = receipt.get("unit_id", "")
        unit = self._conn.execute("SELECT * FROM units WHERE unit_id=?", (uid,)).fetchone()
        if unit is None:
            raise ValueError("Unit not found")
        if unit["state"] != "COMMITTED":
            raise ValueError(f"Unit not COMMITTED (state={unit['state']})")
        # R11-8: idempotent retry — contribution already accepted → same response
        existing = self._conn.execute("SELECT * FROM contributions WHERE cid=?", (receipt.get("receipt_id", ""),)).fetchone()
        if existing:
            return {"contribution_id": existing["cid"], "status": existing["status"]}
        if not verify_receipt(receipt, signing_key):
            raise ValueError("Invalid receipt HMAC")
        mandatory = ["protocol_version", "receipt_version", "key_id", "receipt_id",
                      "receipt_nonce", "issued_at", "expires_at", "run_id", "round_id",
                      "unit_id", "worker_id", "delta_bundle_sha256",
                      "delta_bundle_byte_length", "delta_format"]
        for field in mandatory:
            if field not in receipt:
                raise ValueError(f"Missing receipt field: {field}")
        if receipt.get("expires_at", 0) < time.time():
            raise ValueError("Receipt expired")
        if not self._check_nonce(receipt["receipt_nonce"]):
            raise ValueError("Nonce already consumed")
        if receipt.get("delta_format") != "tensor_bundle_v1":
            raise ValueError(f"Bad delta_format: {receipt.get('delta_format')}")
        bundle_b64 = p.get("delta_bundle_b64", "")
        try:
            bundle_bytes = base64.b64decode(bundle_b64, validate=True)
        except Exception:
            raise ValueError("Invalid base64 in delta_bundle_b64")
        if len(bundle_bytes) != receipt.get("delta_bundle_byte_length", 0):
            raise ValueError("Delta bundle byte length mismatch")
        sha = hashlib.sha256(bundle_bytes).hexdigest()
        if sha != receipt["delta_bundle_sha256"]:
            raise ValueError("Delta bundle SHA mismatch")
        delta_bundle_unpack(bundle_bytes, sha)
        cid = receipt["receipt_id"]
        self._conn.execute("""
            INSERT OR IGNORE INTO contributions
            (cid, status, unit_id, run_id, round_id, worker_id,
             delta_bundle_b64, delta_bundle_sha256, receipt_json, ett, nonce_id)
            VALUES (?, 'RECEIVED', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cid, receipt.get("unit_id",""), receipt.get("run_id",""), receipt.get("round_id",""),
              receipt.get("worker_id",""), bundle_b64, sha, json.dumps(receipt),
              receipt.get("effective_trainable_tokens",0), receipt["receipt_nonce"]))
        self._conn.commit()
        self._consume_nonce(receipt["receipt_nonce"], cid)
        return {"contribution_id": cid, "status": "RECEIVED"}

    def validate(self, cid):
        self._conn.execute("UPDATE contributions SET status='VALIDATED' WHERE cid=? AND status='RECEIVED'", (cid,))
        self._conn.commit()

    def activate(self, cid):
        c = self._conn.execute("SELECT * FROM contributions WHERE cid=? AND status='VALIDATED'", (cid,)).fetchone()
        if not c: raise ValueError(f"Cannot activate {cid}")
        self._conn.execute("UPDATE contributions SET status='SUPERSEDED' WHERE status='ACTIVE' AND round_id=? AND worker_id=? AND cid!=?",
            (c["round_id"], c["worker_id"], cid))
        self._conn.execute("UPDATE contributions SET status='ACTIVE' WHERE cid=?", (cid,))
        self._conn.commit()

    def fedavg(self, run_id, round_id):
        rows = self._conn.execute(
            "SELECT delta_bundle_b64, delta_bundle_sha256, ett FROM contributions WHERE status='ACTIVE' AND run_id=? AND round_id=?",
            (run_id, round_id)).fetchall()
        if not rows: raise ValueError("No ACTIVE contributions")
        import torch
        total_ett = sum(r["ett"] or 1 for r in rows)
        avg_A = torch.zeros(8, 256)
        avg_B = torch.zeros(1024, 8)
        for r in rows:
            tensors = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]), r["delta_bundle_sha256"])
            w = (r["ett"] or 1) / total_ett
            avg_A += w * tensors["local_layers.0.linear1.lora_A.weight"]
            avg_B += w * tensors["local_layers.0.linear1.lora_B.weight"]
        return avg_A, avg_B

    def close_round(self, run_id, round_id):
        avg_A, avg_B = self.fedavg(run_id, round_id)
        import torch
        from rc5_2_tensor_bundle import tensor_bundle_v1_pack, bundle_sha256
        adapter_bytes = tensor_bundle_v1_pack({
            "local_layers.0.linear1.lora_A.weight": avg_A,
            "local_layers.0.linear1.lora_B.weight": avg_B,
        })
        ah = bundle_sha256(adapter_bytes)
        self._conn.execute("INSERT OR REPLACE INTO rounds (run_id, round_id, global_adapter_b64, global_adapter_hash, closed) VALUES (?,?,?,?,1)",
            (run_id, round_id, base64.b64encode(adapter_bytes).decode(), ah))
        self._conn.commit()
        return adapter_bytes, ah
