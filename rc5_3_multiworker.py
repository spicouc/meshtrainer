"""RC5.3 Stage B: RoundCoordinator — two workers, assignments, calibration, FedAvg, revisions."""
import json, sqlite3, threading, base64, time, uuid, hashlib
import torch
from concurrent.futures import ThreadPoolExecutor

from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256
from rc5_2_canonical import numerical_profile_hash, partition_schema_hash, adapter_schema_hash
from rc5_2_receipt import generate_receipt, verify_receipt

ROUND_DRAFT, ROUND_OPEN, ROUND_CALIBRATING = "DRAFT", "OPEN", "CALIBRATING"
ROUND_ASSIGNED, ROUND_RUNNING, ROUND_READY, ROUND_AGGREGATING = "ASSIGNED", "RUNNING", "READY_TO_CLOSE", "AGGREGATING"
ROUND_CLOSED, ROUND_ABORTED, ROUND_EXPIRED = "CLOSED", "ABORTED", "EXPIRED"

class RoundCoordinator:
    def __init__(self, db_path="round.db", signing_key=b"r53_stage_b_key_1234567890"):
        self.db_path = db_path
        self.signing_key = signing_key
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._profile = {"max_micro_batch": 4, "max_sequence_length": 128}

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS rounds (
                run_id TEXT, round_id TEXT, state TEXT,
                policy_max_micro_batch INTEGER, policy_max_seq INTEGER,
                closed_at TEXT, PRIMARY KEY (run_id, round_id)
            );
            CREATE TABLE IF NOT EXISTS assignments (
                assignment_id TEXT PRIMARY KEY, run_id TEXT, round_id TEXT, worker_id TEXT, shard_id TEXT,
                base_adapter_hash TEXT, worker_model_hash TEXT, partition_schema_hash TEXT,
                adapter_schema_hash TEXT, numerical_profile_hash TEXT,
                max_micro_batch INTEGER, max_sequence_length INTEGER, ett_target INTEGER, status TEXT
            );
            CREATE TABLE IF NOT EXISTS calibrations (
                worker_id TEXT PRIMARY KEY, run_id TEXT, round_id TEXT, backend TEXT, precision TEXT,
                available_memory_mb INTEGER, observed_safe_budget_mb INTEGER,
                max_micro_batch INTEGER, max_sequence_length INTEGER,
                calibration_nonce TEXT, measured_at TEXT
            );
            CREATE TABLE IF NOT EXISTS contributions_r53 (
                cid TEXT PRIMARY KEY, run_id TEXT, round_id TEXT, assignment_id TEXT, worker_id TEXT,
                status TEXT, ett INTEGER, delta_bundle_b64 TEXT, delta_bundle_sha256 TEXT,
                receipt_json TEXT, revision INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS round_adapters (
                run_id TEXT, round_id TEXT, adapter_b64 TEXT, adapter_hash TEXT,
                pre_adapter_hash TEXT, PRIMARY KEY (run_id, round_id)
            );
        """)
        self._conn.commit()

    # ---- round lifecycle ----
    def round_open(self, run_id, round_id, policy=None):
        policy = policy or {}
        self._conn.execute("INSERT OR IGNORE INTO rounds (run_id, round_id, state, policy_max_micro_batch, policy_max_seq) VALUES (?,?,?,?,?)",
            (run_id, round_id, ROUND_OPEN, policy.get("max_micro_batch", 8), policy.get("max_sequence_length", 256)))
        self._conn.commit()
        return {"run_id": run_id, "round_id": round_id, "state": ROUND_OPEN}

    def calibration_submit(self, run_id, round_id, worker_id, cal):
        self._conn.execute("INSERT OR REPLACE INTO calibrations (worker_id, run_id, round_id, backend, precision, available_memory_mb, observed_safe_budget_mb, max_micro_batch, max_sequence_length, calibration_nonce, measured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (worker_id, run_id, round_id, cal.get("backend",""), cal.get("precision","f32"),
             cal.get("available_memory_mb",0), cal.get("observed_safe_budget_mb",0),
             cal.get("max_micro_batch",1), cal.get("max_sequence_length",128),
             cal.get("calibration_nonce", uuid.uuid4().hex), time.time()))
        self._conn.commit()
        r = self._conn.execute("SELECT state FROM rounds WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        if r and r["state"] == ROUND_OPEN:
            self._conn.execute("UPDATE rounds SET state=? WHERE run_id=? AND round_id=?", (ROUND_CALIBRATING, run_id, round_id))
            self._conn.commit()
        return {"worker_id": worker_id, "status": "CALIBRATED"}

    def _budget_for(self, run_id, round_id, worker_id):
        """min(profile, calibration, round policy) — never assign above."""
        rnd = self._conn.execute("SELECT * FROM rounds WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        cal = self._conn.execute("SELECT * FROM calibrations WHERE worker_id=? AND run_id=? AND round_id=?",
                                 (worker_id, run_id, round_id)).fetchone()
        mb = min(self._profile["max_micro_batch"], (rnd["policy_max_micro_batch"] if rnd and rnd["policy_max_micro_batch"] is not None else 8),
                 (cal["max_micro_batch"] if cal and cal["max_micro_batch"] is not None else 1))
        sl = min(self._profile["max_sequence_length"], (rnd["policy_max_seq"] if rnd and rnd["policy_max_seq"] is not None else 256),
                 (cal["max_sequence_length"] if cal and cal["max_sequence_length"] is not None else 128))
        return mb, sl

    def assign(self, run_id, round_id, assignment_id, worker_id, shard_id, hashes, ett_target, strict_budget=True):
        with self._lock:
            return self._assign_locked(run_id, round_id, assignment_id, worker_id, shard_id, hashes, ett_target, strict_budget)

    def _assign_locked(self, run_id, round_id, assignment_id, worker_id, shard_id, hashes, ett_target, strict_budget=True):
        # stale adapter rejection: if this round follows a closed round, base_adapter_hash must match it
        prev_round = self._conn.execute("SELECT round_id FROM rounds WHERE run_id=? AND state=? AND round_id < ? ORDER BY round_id DESC LIMIT 1",
                                        (run_id, ROUND_CLOSED, round_id)).fetchone()
        if prev_round is not None:
            prev_ad = self._conn.execute("SELECT adapter_hash FROM round_adapters WHERE run_id=? AND round_id=?",
                                         (run_id, prev_round["round_id"])).fetchone()
            if prev_ad is not None and hashes.get("base_adapter_hash", "") != prev_ad["adapter_hash"]:
                raise ValueError("Stale base adapter: does not match previous round adapter")
        mb, sl = self._budget_for(run_id, round_id, worker_id)
        if strict_budget and hashes.get("max_micro_batch", mb) > mb:
            raise ValueError("Assignment exceeds calibration budget (micro_batch)")
        if strict_budget and hashes.get("max_sequence_length", sl) > sl:
            raise ValueError("Assignment exceeds calibration budget (seq_len)")
        self._conn.execute("""
            INSERT OR REPLACE INTO assignments
            (assignment_id, run_id, round_id, worker_id, shard_id,
             base_adapter_hash, worker_model_hash, partition_schema_hash, adapter_schema_hash, numerical_profile_hash,
             max_micro_batch, max_sequence_length, ett_target, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'ASSIGNED')
        """, (assignment_id, run_id, round_id, worker_id, shard_id,
              hashes.get("base_adapter_hash",""), hashes.get("worker_model_hash",""),
              hashes.get("partition_schema_hash", partition_schema_hash()),
              hashes.get("adapter_schema_hash", adapter_schema_hash()),
              hashes.get("numerical_profile_hash", numerical_profile_hash()),
              mb, sl, ett_target))
        self._conn.commit()
        self._conn.execute("UPDATE rounds SET state=? WHERE run_id=? AND round_id=?", (ROUND_ASSIGNED, run_id, round_id))
        self._conn.commit()
        return {"assignment_id": assignment_id, "worker_id": worker_id, "state": "ASSIGNED",
                "max_micro_batch": mb, "max_sequence_length": sl}

    def verify_assignment_hashes(self, run_id, round_id, assignment_id, worker_id, p):
        a = self._conn.execute("SELECT * FROM assignments WHERE assignment_id=? AND run_id=? AND round_id=? AND worker_id=?",
                               (assignment_id, run_id, round_id, worker_id)).fetchone()
        if a is None:
            raise ValueError("Assignment not found")
        for hk in ["worker_model_hash", "base_adapter_hash", "partition_schema_hash",
                   "adapter_schema_hash", "numerical_profile_hash"]:
            if p.get(hk, "") != a[hk]:
                raise ValueError(f"Hash mismatch for {hk}")
        return dict(a)

    def list_assignments(self, run_id, round_id):
        rows = self._conn.execute("SELECT * FROM assignments WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchall()
        return [dict(r) for r in rows]

    def round_status(self, run_id, round_id):
        r = self._conn.execute("SELECT * FROM rounds WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        return dict(r) if r else None

    # ---- contributions & revisions ----
    def submit_contribution(self, run_id, round_id, assignment_id, worker_id, receipt, delta_bundle_b64, ett, revision=1):
        with self._lock:
            return self._submit_locked(run_id, round_id, assignment_id, worker_id, receipt, delta_bundle_b64, ett, revision)

    def _submit_locked(self, run_id, round_id, assignment_id, worker_id, receipt, delta_bundle_b64, ett, revision=1):
        dsha = hashlib.sha256(base64.b64decode(delta_bundle_b64)).hexdigest()
        if dsha != receipt.get("delta_bundle_sha256", ""):
            raise ValueError("Bundle SHA mismatch")
        delta_bundle_unpack(base64.b64decode(delta_bundle_b64), dsha)
        if ett <= 0:
            raise ValueError("ETT must be strictly positive")
        # idempotent retry
        ex = self._conn.execute("SELECT * FROM contributions_r53 WHERE cid=?", (receipt["receipt_id"],)).fetchone()
        if ex:
            return {"contribution_id": ex["cid"], "status": ex["status"]}
        self._conn.execute("""
            INSERT INTO contributions_r53 (cid, run_id, round_id, assignment_id, worker_id, status, ett, delta_bundle_b64, delta_bundle_sha256, receipt_json, revision)
            VALUES (?,?,?,?,?, 'RECEIVED', ?, ?, ?, ?, ?)
        """, (receipt["receipt_id"], run_id, round_id, assignment_id, worker_id, ett, delta_bundle_b64, dsha, json.dumps(receipt), revision))
        self._conn.commit()
        return {"contribution_id": receipt["receipt_id"], "status": "RECEIVED"}

    def validate_contribution(self, cid):
        with self._lock:
            cur = self._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (cid,)).fetchone()
            if cur is None or cur["status"] != "RECEIVED":
                raise ValueError(f"Cannot validate {cid}: status={cur['status'] if cur else 'MISSING'}")
            self._conn.execute("UPDATE contributions_r53 SET status='VALIDATED' WHERE cid=?", (cid,))
            self._conn.commit()

    def activate_contribution(self, cid):
        with self._lock:
            cur = self._conn.execute("SELECT * FROM contributions_r53 WHERE cid=?", (cid,)).fetchone()
            if cur is None or cur["status"] != "VALIDATED":
                raise ValueError(f"Cannot activate {cid}")
            # previous ACTIVE for same worker+assignment stays ACTIVE until this one is ACTIVE
            self._conn.execute("UPDATE contributions_r53 SET status='ACTIVE' WHERE cid=?", (cid,))
            # now supersede older ACTIVE of the same assignment
            self._conn.execute("""
                UPDATE contributions_r53 SET status='SUPERSEDED'
                WHERE assignment_id=? AND worker_id=? AND status='ACTIVE' AND cid!=?
            """, (cur["assignment_id"], cur["worker_id"], cid))
            self._conn.commit()

    # ---- FedAvg & round close ----
    def fedavg(self, run_id, round_id):
        rows = self._conn.execute(
            "SELECT delta_bundle_b64, delta_bundle_sha256, ett FROM contributions_r53 WHERE status='ACTIVE' AND run_id=? AND round_id=?",
            (run_id, round_id)).fetchall()
        if not rows:
            raise ValueError("No ACTIVE contributions")
        total_ett = sum(r["ett"] for r in rows)
        if total_ett <= 0:
            raise ValueError("Total ETT must be positive")
        avg_A = torch.zeros(8, 256)
        avg_B = torch.zeros(1024, 8)
        for r in rows:
            tensors = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]), r["delta_bundle_sha256"])
            w = r["ett"] / total_ett
            avg_A += w * tensors["local_layers.0.linear1.lora_A.weight"]
            avg_B += w * tensors["local_layers.0.linear1.lora_B.weight"]
        return avg_A, avg_B

    def close_round(self, run_id, round_id):
        """global_adapter_post = global_adapter_pre + averaged_delta (idempotent)."""
        already = self._conn.execute("SELECT * FROM rounds WHERE run_id=? AND round_id=? AND state=?",
                                     (run_id, round_id, ROUND_CLOSED)).fetchone()
        if already is not None:
            stored = self._conn.execute("SELECT * FROM round_adapters WHERE run_id=? AND round_id=?",
                                        (run_id, round_id)).fetchone()
            if stored is not None:
                return {"adapter_bytes": base64.b64decode(stored["adapter_b64"]),
                        "adapter_hash": stored["adapter_hash"], "idempotent": True}
        mandatory = self._conn.execute(
            "SELECT COUNT(*) c FROM assignments WHERE run_id=? AND round_id=? AND status='ASSIGNED'", (run_id, round_id)).fetchone()["c"]
        active = self._conn.execute(
            "SELECT COUNT(*) c FROM contributions_r53 WHERE run_id=? AND round_id=? AND status='ACTIVE'", (run_id, round_id)).fetchone()["c"]
        if active < mandatory:
            raise ValueError(f"Cannot close: {active}/{mandatory} mandatory assignments ACTIVE")
        # previous global adapter (pre) or zero
        prev = self._conn.execute("SELECT * FROM round_adapters WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        if prev is not None:
            pre_tensors = delta_bundle_unpack(base64.b64decode(prev["adapter_b64"]), prev["adapter_hash"])
            pre_A = pre_tensors["local_layers.0.linear1.lora_A.weight"].clone()
            pre_B = pre_tensors["local_layers.0.linear1.lora_B.weight"].clone()
        else:
            pre_A, pre_B = torch.zeros(8, 256), torch.zeros(1024, 8)
        dA, dB = self.fedavg(run_id, round_id)
        post_A, post_B = pre_A + dA, pre_B + dB
        adapter_bytes = delta_bundle_pack(post_A, post_B)
        ah = bundle_sha256(adapter_bytes)
        self._conn.execute("INSERT OR REPLACE INTO round_adapters (run_id, round_id, adapter_b64, adapter_hash, pre_adapter_hash) VALUES (?,?,?,?,?)",
            (run_id, round_id, base64.b64encode(adapter_bytes).decode(), ah,
             bundle_sha256(delta_bundle_pack(pre_A, pre_B)) if prev is not None else "0" * 64))
        self._conn.execute("UPDATE rounds SET state=?, closed_at=? WHERE run_id=? AND round_id=?", (ROUND_CLOSED, time.time(), run_id, round_id))
        self._conn.commit()
        return {"adapter_bytes": adapter_bytes, "adapter_hash": ah, "global_adapter_post": post_A}

    def get_round_adapter(self, run_id, round_id):
        r = self._conn.execute("SELECT * FROM round_adapters WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        if r is None: raise ValueError("Round adapter not found")
        return base64.b64decode(r["adapter_b64"]), r["adapter_hash"]

def run_two_workers_concurrent(coord, run_id, round_id, worker_specs, hashes):
    """Execute two worker flows concurrently via ThreadPoolExecutor (isolation demo)."""
    def flow(ws):
        a_id, w_id, shard, ett = ws
        asg = coord.assign(run_id, round_id, a_id, w_id, shard, hashes, ett)
        # worker builds delta (deterministic for reproducibility)
        torch.manual_seed(hash((run_id, a_id)) % (2**32))
        delta_A = torch.randn(8, 256) * 0.001
        delta_B = torch.randn(1024, 8) * 0.001
        d = delta_bundle_pack(delta_A, delta_B)
        dsha = bundle_sha256(d)
        db64 = base64.b64encode(d).decode("ascii")
        receipt = generate_receipt(run_id=run_id, round_id=round_id, unit_id=f"u_{w_id}",
                                   worker_id=w_id, assignment_id=a_id,
                                   signing_key=coord.signing_key, delta_bundle_sha256=dsha,
                                   delta_bundle_byte_length=len(d), ett=ett, loss=1.0)
        cid = coord.submit_contribution(run_id, round_id, a_id, w_id, receipt, db64, ett)
        coord.validate_contribution(cid["contribution_id"])
        coord.activate_contribution(cid["contribution_id"])
        return {"assignment_id": a_id, "worker_id": w_id, "cid": cid["contribution_id"], "ett": ett}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(flow, ws) for ws in worker_specs]
        results = [f.result() for f in futures]
    return results
