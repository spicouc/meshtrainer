"""RC5.3 Stage B R2: REAL two-worker distributed round over HTTP JSON-RPC."""
import json, sqlite3, threading, base64, time, uuid, hashlib
import torch
from concurrent.futures import ThreadPoolExecutor

from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_canonical import numerical_profile_hash, partition_schema_hash, adapter_schema_hash
from rc5_2_artifacts import pack_worker_base_model, pack_base_adapter, verify_worker_artifacts
from rc5_2_worker_runtime import WorkerRuntime
from rc5_2_numerical_adapter import make_causal_fixture, ett_from_labels
from rc5_2_coordinator import Coordinator

ROUND_DRAFT, ROUND_OPEN, ROUND_CALIBRATING = "DRAFT", "OPEN", "CALIBRATING"
ROUND_ASSIGNED, ROUND_RUNNING = "ASSIGNED", "RUNNING"
ROUND_READY, ROUND_AGGREGATING = "READY_TO_CLOSE", "AGGREGATING"
ROUND_CLOSED, ROUND_ABORTED, ROUND_EXPIRED = "CLOSED", "ABORTED", "EXPIRED"

_TRANS = {
    ROUND_DRAFT: {ROUND_OPEN},
    ROUND_OPEN: {ROUND_CALIBRATING},
    ROUND_CALIBRATING: {ROUND_ASSIGNED},
    ROUND_ASSIGNED: {ROUND_RUNNING, ROUND_ABORTED, ROUND_EXPIRED},
    ROUND_RUNNING: {ROUND_READY, ROUND_ABORTED, ROUND_EXPIRED},
    ROUND_READY: {ROUND_AGGREGATING, ROUND_ABORTED, ROUND_EXPIRED},
    ROUND_AGGREGATING: {ROUND_CLOSED, ROUND_ABORTED, ROUND_EXPIRED},
}
TERMINAL = {ROUND_CLOSED, ROUND_ABORTED, ROUND_EXPIRED}

def _can_transition(frm, to):
    return frm in _TRANS and to in _TRANS[frm]

def _stable_seed(run_id, round_id, assignment_id, worker_id):
    """Persistent seed: SHA-256 of the identifiers (no Python hash())."""
    h = hashlib.sha256(f"{run_id}|{round_id}|{assignment_id}|{worker_id}".encode()).hexdigest()
    return int(h[:8], 16)

def make_shard(shard_id, length=128, offset=0, label_keep=None):
    """Real distinct token shard (full 128-seq so the server loss applies).
    Content differs by offset (wrap-around); ETT differs by label_keep (-100 positions)."""
    tokens, labels, mask = make_causal_fixture()
    n = tokens.shape[1]
    idx = torch.arange(offset, offset + length) % n
    t = tokens[:, idx].contiguous()
    l = labels[:, idx].contiguous()
    if label_keep is not None:
        l = l.clone()
        l[:, label_keep:] = -100
    m = mask.clone().contiguous()
    return t, l, m

class RoundCoordinator:
    def __init__(self, db_path="round.db", signing_key=b"r53_stage_b_real_key_2026"):
        self.db_path = db_path
        self.signing_key = signing_key
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._profile = {"max_micro_batch": 4, "max_sequence_length": 128}

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS rounds_r53 (
                run_id TEXT, round_id TEXT, state TEXT,
                policy_max_micro_batch INTEGER, policy_max_seq INTEGER,
                closed_at TEXT, PRIMARY KEY (run_id, round_id)
            );
            CREATE TABLE IF NOT EXISTS assignments_r53 (
                assignment_id TEXT PRIMARY KEY, run_id TEXT, round_id TEXT, worker_id TEXT, shard_id TEXT,
                base_adapter_hash TEXT, worker_model_hash TEXT, partition_schema_hash TEXT,
                adapter_schema_hash TEXT, numerical_profile_hash TEXT,
                max_micro_batch INTEGER, max_sequence_length INTEGER, ett_target INTEGER, status TEXT
            );
            CREATE TABLE IF NOT EXISTS calibrations_r53 (
                worker_id TEXT, run_id TEXT, round_id TEXT, backend TEXT, precision TEXT,
                available_memory_mb INTEGER, observed_safe_budget_mb INTEGER,
                max_micro_batch INTEGER, max_sequence_length INTEGER,
                calibration_nonce TEXT, measured_at TEXT,
                PRIMARY KEY (worker_id, run_id, round_id)
            );
            CREATE TABLE IF NOT EXISTS contributions_r53 (
                cid TEXT PRIMARY KEY, run_id TEXT, round_id TEXT, assignment_id TEXT, worker_id TEXT,
                status TEXT, ett INTEGER, delta_bundle_b64 TEXT, delta_bundle_sha256 TEXT,
                receipt_json TEXT, revision INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS round_adapters_r53 (
                run_id TEXT, round_id TEXT, adapter_b64 TEXT, adapter_hash TEXT,
                pre_adapter_hash TEXT, PRIMARY KEY (run_id, round_id)
            );
        """)

    def _round_state(self, run_id, round_id):
        r = self._conn.execute("SELECT state FROM rounds_r53 WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        return r["state"] if r else None

    def _set_round_state(self, run_id, round_id, state):
        self._conn.execute("UPDATE rounds_r53 SET state=? WHERE run_id=? AND round_id=?", (state, run_id, round_id))

    def round_open(self, run_id, round_id, policy=None):
        with self._lock:
            policy = policy or {}
            cur = self._round_state(run_id, round_id)
            if cur is None:
                self._conn.execute("INSERT INTO rounds_r53 (run_id, round_id, state, policy_max_micro_batch, policy_max_seq) VALUES (?,?,?,?,?)",
                    (run_id, round_id, ROUND_DRAFT, policy.get("max_micro_batch", 8), policy.get("max_sequence_length", 256)))
                cur = ROUND_DRAFT
            if not _can_transition(cur, ROUND_OPEN):
                raise ValueError(f"Cannot open round from {cur}")
            self._set_round_state(run_id, round_id, ROUND_OPEN)
            return {"run_id": run_id, "round_id": round_id, "state": ROUND_OPEN}

    def calibration_submit(self, run_id, round_id, worker_id, cal):
        with self._lock:
            st = self._round_state(run_id, round_id)
            if st not in (ROUND_OPEN, ROUND_CALIBRATING):
                raise ValueError(f"Calibration requires OPEN/CALIBRATING (state={st})")
            self._conn.execute("INSERT OR REPLACE INTO calibrations_r53 (worker_id, run_id, round_id, backend, precision, available_memory_mb, observed_safe_budget_mb, max_micro_batch, max_sequence_length, calibration_nonce, measured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (worker_id, run_id, round_id, cal.get("backend",""), cal.get("precision","f32"),
                 cal.get("available_memory_mb",0), cal.get("observed_safe_budget_mb",0),
                 cal.get("max_micro_batch",1), cal.get("max_sequence_length",128),
                 cal.get("calibration_nonce", uuid.uuid4().hex), time.time()))
            if st == ROUND_OPEN:
                self._set_round_state(run_id, round_id, ROUND_CALIBRATING)
            return {"worker_id": worker_id, "status": "CALIBRATED"}

    def _budget_for(self, run_id, round_id, worker_id):
        rnd = self._conn.execute("SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        cal = self._conn.execute("SELECT * FROM calibrations_r53 WHERE worker_id=? AND run_id=? AND round_id=?",
                                 (worker_id, run_id, round_id)).fetchone()
        mb = min(self._profile["max_micro_batch"], (rnd["policy_max_micro_batch"] if rnd and rnd["policy_max_micro_batch"] is not None else 8),
                 (cal["max_micro_batch"] if cal and cal["max_micro_batch"] is not None else 1))
        sl = min(self._profile["max_sequence_length"], (rnd["policy_max_seq"] if rnd and rnd["policy_max_seq"] is not None else 256),
                 (cal["max_sequence_length"] if cal and cal["max_sequence_length"] is not None else 128))
        return mb, sl

    def assign(self, run_id, round_id, assignment_id, worker_id, shard_id, hashes, ett_target, strict_budget=True):
        with self._lock:
            st = self._round_state(run_id, round_id)
            if st not in (ROUND_CALIBRATING, ROUND_ASSIGNED):
                raise ValueError(f"Assignment requires CALIBRATING/ASSIGNED (state={st})")
            dup = self._conn.execute("SELECT assignment_id FROM assignments_r53 WHERE run_id=? AND round_id=? AND worker_id=?",
                                     (run_id, round_id, worker_id)).fetchone()
            if dup is not None and dup["assignment_id"] != assignment_id:
                raise ValueError("Worker already assigned in this round")
            prev_ad = self._conn.execute(
                "SELECT adapter_hash FROM round_adapters_r53 WHERE run_id=? AND round_id < ? ORDER BY round_id DESC LIMIT 1",
                (run_id, round_id)).fetchone()
            if prev_ad is not None and hashes.get("base_adapter_hash", "") != prev_ad["adapter_hash"]:
                raise ValueError("Stale base adapter: does not match previous round adapter")
            for hk in ["worker_model_hash", "base_adapter_hash", "partition_schema_hash",
                       "adapter_schema_hash", "numerical_profile_hash"]:
                if not hashes.get(hk):
                    raise ValueError(f"Missing hash {hk}")
            mb, sl = self._budget_for(run_id, round_id, worker_id)
            if strict_budget and hashes.get("max_micro_batch", mb) > mb:
                raise ValueError("Assignment exceeds calibration budget (micro_batch)")
            if strict_budget and hashes.get("max_sequence_length", sl) > sl:
                raise ValueError("Assignment exceeds calibration budget (seq_len)")
            self._conn.execute("""
                INSERT OR REPLACE INTO assignments_r53
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
            if st == ROUND_CALIBRATING:
                self._set_round_state(run_id, round_id, ROUND_ASSIGNED)
            return {"assignment_id": assignment_id, "worker_id": worker_id, "state": "ASSIGNED",
                    "max_micro_batch": mb, "max_sequence_length": sl}

    def verify_assignment_hashes(self, run_id, round_id, assignment_id, worker_id, p):
        a = self._conn.execute("SELECT * FROM assignments_r53 WHERE assignment_id=? AND run_id=? AND round_id=? AND worker_id=?",
                               (assignment_id, run_id, round_id, worker_id)).fetchone()
        if a is None:
            raise ValueError("Assignment not found")
        for hk in ["worker_model_hash", "base_adapter_hash", "partition_schema_hash",
                   "adapter_schema_hash", "numerical_profile_hash"]:
            if p.get(hk, "") != a[hk]:
                raise ValueError(f"Hash mismatch for {hk}")
        return dict(a)

    def start_round(self, run_id, round_id):
        with self._lock:
            st = self._round_state(run_id, round_id)
            if st != ROUND_ASSIGNED:
                raise ValueError(f"start requires ASSIGNED (state={st})")
            self._set_round_state(run_id, round_id, ROUND_RUNNING)
            return {"state": ROUND_RUNNING}

    def submit_contribution(self, run_id, round_id, assignment_id, worker_id, receipt, delta_bundle_b64, ett, revision=1):
        with self._lock:
            st = self._round_state(run_id, round_id)
            if st != ROUND_RUNNING:
                raise ValueError(f"Contribution requires RUNNING (state={st})")
            a = self._conn.execute("SELECT * FROM assignments_r53 WHERE assignment_id=? AND run_id=? AND round_id=? AND worker_id=?",
                                   (assignment_id, run_id, round_id, worker_id)).fetchone()
            if a is None:
                raise ValueError("Assignment not found for contribution")
            dsha = hashlib.sha256(base64.b64decode(delta_bundle_b64)).hexdigest()
            if dsha != receipt.get("delta_bundle_sha256", ""):
                raise ValueError("Bundle SHA mismatch")
            delta_bundle_unpack(base64.b64decode(delta_bundle_b64), dsha)
            if ett <= 0:
                raise ValueError("ETT must be strictly positive")
            ex = self._conn.execute("SELECT * FROM contributions_r53 WHERE cid=?", (receipt["receipt_id"],)).fetchone()
            if ex:
                return {"contribution_id": ex["cid"], "status": ex["status"]}
            self._conn.execute("""
                INSERT INTO contributions_r53 (cid, run_id, round_id, assignment_id, worker_id, status, ett, delta_bundle_b64, delta_bundle_sha256, receipt_json, revision)
                VALUES (?,?,?,?,?, 'RECEIVED', ?, ?, ?, ?, ?)
            """, (receipt["receipt_id"], run_id, round_id, assignment_id, worker_id, ett, delta_bundle_b64, dsha, json.dumps(receipt), revision))
            return {"contribution_id": receipt["receipt_id"], "status": "RECEIVED"}

    def validate_contribution(self, cid):
        with self._lock:
            cur = self._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (cid,)).fetchone()
            if cur is None or cur["status"] != "RECEIVED":
                raise ValueError(f"Cannot validate {cid}: status={cur['status'] if cur else 'MISSING'}")
            self._conn.execute("UPDATE contributions_r53 SET status='VALIDATED' WHERE cid=?", (cid,))

    def activate_contribution(self, cid):
        with self._lock:
            cur = self._conn.execute("SELECT * FROM contributions_r53 WHERE cid=?", (cid,)).fetchone()
            if cur is None or cur["status"] != "VALIDATED":
                raise ValueError(f"Cannot activate {cid}")
            self._conn.execute("UPDATE contributions_r53 SET status='ACTIVE' WHERE cid=?", (cid,))
            self._conn.execute("""
                UPDATE contributions_r53 SET status='SUPERSEDED'
                WHERE assignment_id=? AND worker_id=? AND status='ACTIVE' AND cid!=?
            """, (cur["assignment_id"], cur["worker_id"], cid))

    def ready_to_close(self, run_id, round_id):
        """Quorum: EVERY mandatory assignment has exactly one ACTIVE contribution from the correct worker."""
        with self._lock:
            st = self._round_state(run_id, round_id)
            if st != ROUND_RUNNING:
                raise ValueError(f"ready requires RUNNING (state={st})")
            mand = self._conn.execute("SELECT * FROM assignments_r53 WHERE run_id=? AND round_id=? AND status='ASSIGNED'",
                                      (run_id, round_id)).fetchall()
            for a in mand:
                acts = self._conn.execute(
                    "SELECT * FROM contributions_r53 WHERE run_id=? AND round_id=? AND assignment_id=? AND worker_id=? AND status='ACTIVE'",
                    (run_id, round_id, a["assignment_id"], a["worker_id"])).fetchall()
                if len(acts) != 1:
                    raise ValueError(f"Quorum not met for {a['assignment_id']}: {len(acts)} ACTIVE")
            self._set_round_state(run_id, round_id, ROUND_READY)
            return {"state": ROUND_READY}

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
        with self._lock:
            already = self._conn.execute("SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=? AND state=?",
                                         (run_id, round_id, ROUND_CLOSED)).fetchone()
            if already is not None:
                stored = self._conn.execute("SELECT * FROM round_adapters_r53 WHERE run_id=? AND round_id=?",
                                            (run_id, round_id)).fetchone()
                if stored is not None:
                    return {"adapter_bytes": base64.b64decode(stored["adapter_b64"]),
                            "adapter_hash": stored["adapter_hash"], "idempotent": True}
            st = self._round_state(run_id, round_id)
            if st != ROUND_READY:
                raise ValueError(f"close requires READY_TO_CLOSE (state={st})")
            prev = self._conn.execute(
                "SELECT * FROM round_adapters_r53 WHERE run_id=? AND round_id < ? ORDER BY round_id DESC LIMIT 1",
                (run_id, round_id)).fetchone()
            if prev is not None:
                pre_tensors = delta_bundle_unpack(base64.b64decode(prev["adapter_b64"]), prev["adapter_hash"])
                pre_A = pre_tensors["local_layers.0.linear1.lora_A.weight"].clone()
                pre_B = pre_tensors["local_layers.0.linear1.lora_B.weight"].clone()
            else:
                pre_A, pre_B = torch.zeros(8, 256), torch.zeros(1024, 8)
            self._set_round_state(run_id, round_id, ROUND_AGGREGATING)
            dA, dB = self.fedavg(run_id, round_id)
            post_A, post_B = pre_A + dA, pre_B + dB
            adapter_bytes = delta_bundle_pack(post_A, post_B)
            ah = bundle_sha256(adapter_bytes)
            self._conn.execute("INSERT OR REPLACE INTO round_adapters_r53 (run_id, round_id, adapter_b64, adapter_hash, pre_adapter_hash) VALUES (?,?,?,?,?)",
                (run_id, round_id, base64.b64encode(adapter_bytes).decode(), ah,
                 bundle_sha256(delta_bundle_pack(pre_A, pre_B))))
            self._set_round_state(run_id, round_id, ROUND_CLOSED)
            return {"adapter_bytes": adapter_bytes, "adapter_hash": ah, "post_A": post_A}

    def get_round_adapter(self, run_id, round_id):
        r = self._conn.execute("SELECT * FROM round_adapters_r53 WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        if r is None: raise ValueError("Round adapter not found")
        return base64.b64decode(r["adapter_b64"]), r["adapter_hash"]

    def round_status(self, run_id, round_id):
        r = self._conn.execute("SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchone()
        return dict(r) if r else None

    def list_assignments(self, run_id, round_id):
        rows = self._conn.execute("SELECT * FROM assignments_r53 WHERE run_id=? AND round_id=?", (run_id, round_id)).fetchall()
        return [dict(r) for r in rows]


def build_worker_artifacts(worker_model):
    """REAL artifacts: pack → verify → (bytes, real SHA-256 hashes)."""
    base_bytes, base_hash = pack_worker_base_model(worker_model)
    ad_bytes, ad_hash = pack_base_adapter(worker_model)
    v = verify_worker_artifacts(base_bytes, ad_bytes, base_hash, ad_hash,
                                partition_schema_hash(), adapter_schema_hash(), numerical_profile_hash())
    if not v["ok"]:
        raise ValueError(f"Artifact verification failed: {v['errors']}")
    return base_bytes, base_hash, ad_bytes, ad_hash

def adapter_artifacts_from_round(worker_model, adapter_bytes):
    """Round N+1 artifacts: same base model, base adapter = previous round's absolute adapter."""
    base_bytes, base_hash = pack_worker_base_model(worker_model)
    ad_hash = bundle_sha256(adapter_bytes)
    return base_bytes, base_hash, adapter_bytes, ad_hash


def real_worker_flow(cl, coord, run_id, round_id, assignment_id, worker_id, shard_id,
                     base_bytes, base_hash, ad_bytes, ad_hash, offset=0, label_keep=None,
                     session_id=None, update_suffix="", db_dir=None, micro_unit_id=None):
    """One REAL worker: full HTTP flow with a real WorkerRuntime (no synthetic deltas)."""
    import os, tempfile
    session_id = session_id or f"sess_{worker_id}_{uuid.uuid4().hex[:6]}"
    unit_id = f"unit_{worker_id}_{uuid.uuid4().hex[:6]}"
    db_path = os.path.join(db_dir or tempfile.gettempdir(), f"journal_{worker_id}_{uuid.uuid4().hex[:8]}.db")
    tokens, labels, mask = make_shard(shard_id, offset=offset, label_keep=label_keep)
    ett = ett_from_labels(labels)

    # step.open — real hashes from real artifacts; shard data drives the server loss
    r1 = cl.call("step.open", {
        "protocol_version": "1.2.0-rc5.2", "unit_id": unit_id,
        "run_id": run_id, "round_id": round_id, "assignment_id": assignment_id,
        "micro_unit_id": micro_unit_id or f"mu_{worker_id}", "worker_id": worker_id, "session_id": session_id,
        "shard_id": shard_id, "shard_offset": offset, "label_keep": label_keep if label_keep is not None else -1,
        "worker_model_hash": base_hash, "base_adapter_hash": ad_hash,
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash(),
    })
    sa = unpack_tensor_envelope(r1["server_activation"])

    # REAL worker: load artifacts, forward local layers
    wr = WorkerRuntime(db_path=db_path)
    wr.load_from_artifacts(base_bytes, base_adapter_bytes=ad_bytes)
    cut = wr.worker_forward(sa, mask)
    env = pack_tensor_envelope(cut, "cut_activation", unit_id, "s1")

    r2 = cl.call("step.worker_forward.submit", {
        "unit_id": unit_id, "forward_id": f"fwd_{worker_id}_{update_suffix}", "step_id": "s1", "cut_activation": env})
    bw_id = r2["backward_id"]
    r3 = cl.call("step.server_backward.fetch", {"unit_id": unit_id, "backward_id": bw_id})
    cg = unpack_tensor_envelope(r3["cut_gradient"])

    # REAL delta: post - pre via exactly-once journal
    wr.prepare_update(f"upd_{worker_id}_{update_suffix}", unit_id)
    delta_b64, delta_sha = wr.apply_update_once(f"upd_{worker_id}_{update_suffix}", cut, cg)
    r4 = cl.call("step.worker_update.submit", {
        "unit_id": unit_id, "update_id": f"upd_{worker_id}_{update_suffix}",
        "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha})
    r5 = cl.call("step.commit", {"unit_id": unit_id, "delta_id": r4.get("delta_id", "")})
    receipt = r5["receipt"]
    r6 = cl.call("checkpoint.upload", {
        "receipt": receipt,
        "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha})
    cid = coord.submit_contribution(run_id, round_id, assignment_id, worker_id,
                                    receipt, delta_b64, ett)
    coord.validate_contribution(cid["contribution_id"])
    coord.activate_contribution(cid["contribution_id"])
    return {"worker_id": worker_id, "unit_id": unit_id, "assignment_id": assignment_id,
            "shard_id": shard_id, "ett": ett, "cid": cid["contribution_id"],
            "backward_id": bw_id, "receipt_id": receipt["receipt_id"], "delta_sha": delta_sha}


def run_two_real_workers(coord, cl, run_id, round_id, artifacts_by_worker, specs):
    """Two REAL workers concurrently over the same HTTP server.
    specs: [(assignment_id, worker_id, shard_id, offset, label_keep), ...]"""
    def flow(spec):
        asg, wid, shard, offset, lk = spec
        art = artifacts_by_worker[wid]
        return real_worker_flow(cl, coord, run_id, round_id, asg, wid, shard,
                                art[0], art[1], art[2], art[3], offset=offset, label_keep=lk)
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(flow, s) for s in specs]
        return [f.result() for f in futures]
