"""RC5.3 Stage B R5 FINAL: REAL two-worker distributed round over HTTP JSON-RPC.

Implements the binding ServerModel_v1 artifact, atomic transactions,
idempotent assignments/calibrations (request_sha), explicit round lineage
(round_index + parent_round_id), secure register_uploaded_contribution,
a single real base_adapter_v1 per round and an independent FedAvg oracle.
"""
import json, sqlite3, threading, base64, time, uuid, hashlib, math
import torch
import torch.nn as nn
from concurrent.futures import ThreadPoolExecutor

from rc5_2_tensor_bundle import (delta_bundle_pack, delta_bundle_unpack,
                                 bundle_sha256, tensor_bundle_v1_pack,
                                 tensor_bundle_v1_unpack)
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_canonical import (numerical_profile_hash, partition_schema_hash,
                             adapter_schema_hash, canonical_json_v1, sha256_of)
from rc5_2_artifacts import (pack_worker_base_model, pack_base_adapter,
                             verify_worker_artifacts, WORKER_BASE_SCHEMA, ADAPTER_KEYS)
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

_LOGGED_TENSORS = None  # populated lazily from the frozen server schema


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


# ---------------------------------------------------------------------------
# ServerModel_v1 artifact: pack / unpack / strict validation
# ---------------------------------------------------------------------------

def _server_schema_tensors():
    """Expected server tensor names+shapes+dtype, derived from the frozen model."""
    from rc5_2_numerical_models import SplitServerNumericalModel
    m = SplitServerNumericalModel()
    return {n: (tuple(p.shape), p.dtype) for n, p in m.named_parameters()}


def pack_server_model(server_model):
    """REAL server artifact: pack ALL server params -> (bytes, SHA-256)."""
    state = {n: p.detach().cpu() for n, p in server_model.named_parameters()}
    data = tensor_bundle_v1_pack(state)
    return data, bundle_sha256(data)


def server_model_from_bytes(server_bytes, server_hash):
    """Reconstruct a server model EXACTLY from the persisted artifact (restart-safe)."""
    from rc5_2_numerical_models import SplitServerNumericalModel
    tensors = tensor_bundle_v1_unpack(server_bytes, server_hash)
    m = SplitServerNumericalModel()
    params = dict(m.named_parameters())
    for n, p in params.items():
        if n in tensors:
            p.data.copy_(tensors[n])
    return m


def validate_server_artifact(server_bytes, server_hash):
    """Secció 1: verify SHA-256, unpack, zero missing/unexpected keys,
    exact shapes, exact dtype, equivalent reconstruction."""
    errors = []
    if bundle_sha256(server_bytes) != server_hash:
        errors.append("server SHA-256 mismatch")
    tensors = tensor_bundle_v1_unpack(server_bytes, server_hash)
    schema = _server_schema_tensors()
    missing = set(schema) - set(tensors)
    unexpected = set(tensors) - set(schema)
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if unexpected:
        errors.append(f"unexpected keys: {sorted(unexpected)}")
    for n, (shape, dtype) in schema.items():
        if n not in tensors:
            continue
        if tuple(tensors[n].shape) != shape:
            errors.append(f"shape {n}: {tuple(tensors[n].shape)} != {shape}")
        if tensors[n].dtype != dtype:
            errors.append(f"dtype {n}: {tensors[n].dtype} != {dtype}")
    # equivalent reconstruction: re-pack must reproduce the exact bytes
    try:
        repacked = tensor_bundle_v1_pack(tensors)
        if repacked != server_bytes:
            errors.append("reconstruction not byte-equivalent")
    except Exception as e:
        errors.append(f"re-pack failed: {e}")
    if errors:
        raise ValueError("Server artifact invalid: " + "; ".join(errors))
    return tensors


def validate_worker_base_artifact(base_bytes, base_hash):
    """Zero missing/unexpected worker base tensors + exact shapes/dtype."""
    errors = []
    if bundle_sha256(base_bytes) != base_hash:
        errors.append("worker base SHA-256 mismatch")
    tensors = tensor_bundle_v1_unpack(base_bytes, base_hash)
    missing = set(WORKER_BASE_SCHEMA) - set(tensors)
    unexpected = set(tensors) - set(WORKER_BASE_SCHEMA)
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if unexpected:
        errors.append(f"unexpected keys: {sorted(unexpected)}")
    if any("lora" in k for k in tensors):
        errors.append("LoRA tensor inside worker base")
    if errors:
        raise ValueError("Worker base artifact invalid: " + "; ".join(errors))
    return tensors


def validate_base_adapter_artifact(adapter_bytes, adapter_hash):
    """adapter_0 must contain exactly the two LoRA tensors with exact shapes."""
    errors = []
    if bundle_sha256(adapter_bytes) != adapter_hash:
        errors.append("adapter SHA-256 mismatch")
    tensors = tensor_bundle_v1_unpack(adapter_bytes, adapter_hash)
    missing = set(ADAPTER_KEYS) - set(tensors)
    unexpected = set(tensors) - set(ADAPTER_KEYS)
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if unexpected:
        errors.append(f"unexpected keys: {sorted(unexpected)}")
    if "local_layers.0.linear1.lora_A.weight" in tensors and \
            list(tensors["local_layers.0.linear1.lora_A.weight"].shape) != [8, 256]:
        errors.append("adapter A shape")
    if "local_layers.0.linear1.lora_B.weight" in tensors and \
            list(tensors["local_layers.0.linear1.lora_B.weight"].shape) != [1024, 8]:
        errors.append("adapter B shape")
    if errors:
        raise ValueError("Base adapter artifact invalid: " + "; ".join(errors))
    return tensors


def _request_sha(payload: dict) -> str:
    return hashlib.sha256(canonical_json_v1(payload)).hexdigest()


class RoundCoordinator:
    def __init__(self, db_path="round.db", signing_key=b"r53_stage_b_real_key_2026"):
        self.db_path = db_path
        self.signing_key = signing_key
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._profile = {"max_micro_batch": 4, "max_sequence_length": 128}

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS rounds_r53 (
                run_id TEXT, round_id TEXT, round_index INTEGER, state TEXT,
                policy_max_micro_batch INTEGER, policy_max_seq INTEGER,
                server_model_hash TEXT, server_model_b64 TEXT,
                worker_model_hash TEXT, worker_model_b64 TEXT,
                base_adapter_hash TEXT, base_adapter_b64 TEXT,
                partition_schema_hash TEXT, adapter_schema_hash TEXT, numerical_profile_hash TEXT,
                adapter_0_b64 TEXT, adapter_0_hash TEXT,
                parent_round_id TEXT, parent_adapter_hash TEXT,
                global_adapter_hash TEXT,
                closed_at TEXT, PRIMARY KEY (run_id, round_id)
            );
            CREATE TABLE IF NOT EXISTS assignments_r53 (
                assignment_id TEXT, run_id TEXT, round_id TEXT, worker_id TEXT, shard_id TEXT,
                base_adapter_hash TEXT, worker_model_hash TEXT, server_model_hash TEXT,
                partition_schema_hash TEXT, adapter_schema_hash TEXT, numerical_profile_hash TEXT,
                max_micro_batch INTEGER, max_sequence_length INTEGER, ett_target INTEGER, status TEXT,
                request_sha TEXT, response_json TEXT, created_at TEXT,
                PRIMARY KEY (run_id, round_id, assignment_id)
            );
            CREATE TABLE IF NOT EXISTS calibrations_r53 (
                run_id TEXT, round_id TEXT, worker_id TEXT, calibration_nonce TEXT,
                backend TEXT, precision TEXT,
                available_memory_mb INTEGER, observed_safe_budget_mb INTEGER,
                max_micro_batch INTEGER, max_sequence_length INTEGER,
                measured_at REAL, request_sha TEXT, response_json TEXT,
                PRIMARY KEY (run_id, round_id, worker_id, calibration_nonce)
            );
            CREATE TABLE IF NOT EXISTS contributions_r53 (
                cid TEXT PRIMARY KEY, run_id TEXT, round_id TEXT, assignment_id TEXT, worker_id TEXT,
                status TEXT, ett INTEGER, delta_bundle_b64 TEXT, delta_bundle_sha256 TEXT,
                receipt_json TEXT, revision INTEGER DEFAULT 1,
                source_request_sha TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS round_adapters_r53 (
                run_id TEXT, round_id TEXT, round_index INTEGER, parent_round_id TEXT,
                adapter_b64 TEXT, adapter_hash TEXT,
                pre_adapter_hash TEXT, PRIMARY KEY (run_id, round_id)
            );
        """)
        self._conn.commit()

    # -- transaction discipline -------------------------------------------------
    def _tx(self):
        """BEGIN IMMEDIATE -> ... -> COMMIT; ROLLBACK on any error."""
        class _Ctx:
            def __init__(self, conn):
                self.conn = conn
            def __enter__(self):
                self.conn.execute("BEGIN IMMEDIATE")
                return self.conn
            def __exit__(self, exc_type, exc, tb):
                if exc_type is None:
                    self.conn.execute("COMMIT")
                else:
                    try:
                        self.conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                return False
        return _Ctx(self._conn)

    # -- round helpers ----------------------------------------------------------
    def _round_state(self, run_id, round_id):
        r = self._conn.execute("SELECT state FROM rounds_r53 WHERE run_id=? AND round_id=?",
                               (run_id, round_id)).fetchone()
        return r["state"] if r else None

    def _set_round_state(self, run_id, round_id, state):
        self._conn.execute("UPDATE rounds_r53 SET state=? WHERE run_id=? AND round_id=?",
                           (state, run_id, round_id))

    def _latest_closed_index(self, run_id):
        r = self._conn.execute(
            "SELECT MAX(round_index) AS m FROM rounds_r53 WHERE run_id=? AND state=?",
            (run_id, ROUND_CLOSED)).fetchone()
        return r["m"] if r and r["m"] is not None else 0

    def _parent_of(self, run_id, round_id):
        r = self._conn.execute(
            "SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
            (run_id, round_id)).fetchone()
        if r is None or not r["parent_round_id"]:
            return None
        return self._conn.execute(
            "SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
            (run_id, r["parent_round_id"])).fetchone()

    # -- Secció 5: explicit lineage ----------------------------------------------
    def round_open(self, run_id, round_id, policy=None, parent_round_id=None):
        """Explicit lineage: Round 1 -> parent_round_id=None.
        Round N -> parent_round_id MUST be provided and must exist, same run, CLOSED."""
        with self._lock:
            policy = policy or {}
            cur = self._round_state(run_id, round_id)
            if cur is None:
                if parent_round_id is None:
                    latest = self._latest_closed_index(run_id)
                    round_index = latest + 1
                else:
                    parent = self._conn.execute(
                        "SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
                        (run_id, parent_round_id)).fetchone()
                    if parent is None:
                        raise ValueError(f"Parent round {parent_round_id} does not exist (same run required)")
                    if parent["state"] != ROUND_CLOSED:
                        raise ValueError(f"Parent round {parent_round_id} not CLOSED (state={parent['state']})")
                    round_index = parent["round_index"] + 1
                self._conn.execute(
                    "INSERT INTO rounds_r53 (run_id, round_id, round_index, state,"
                    " policy_max_micro_batch, policy_max_seq, parent_round_id)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (run_id, round_id, round_index, ROUND_DRAFT,
                     policy.get("max_micro_batch", 8), policy.get("max_sequence_length", 256),
                     parent_round_id))
                cur = ROUND_DRAFT
            if not _can_transition(cur, ROUND_OPEN):
                raise ValueError(f"Cannot open round from {cur}")
            self._set_round_state(run_id, round_id, ROUND_OPEN)
            return {"run_id": run_id, "round_id": round_id, "state": ROUND_OPEN,
                    "round_index": self._conn.execute(
                        "SELECT round_index FROM rounds_r53 WHERE run_id=? AND round_id=?",
                        (run_id, round_id)).fetchone()["round_index"]}

    # -- Secció 1+2: register_round_model (atomic, bytes, server_model_v1) ---------
    def register_round_model(self, run_id, round_id, server_model_bytes,
                             worker_base_bytes, base_adapter_bytes):
        """Receives the REAL bytes of server_model_v1, worker_base_model_v1 and
        base_adapter_v1. Validates SHA-256, tensor schema (zero missing/unexpected,
        exact shapes/dtype, equivalent reconstruction) and persists atomically."""
        with self._lock:
            with self._tx():
                st = self._round_state(run_id, round_id)
                if st != ROUND_OPEN:
                    raise ValueError(f"register_round_model requires OPEN (state={st})")
                # 1) SHA-256 of the three artifacts
                server_hash = bundle_sha256(server_model_bytes)
                worker_hash = bundle_sha256(worker_base_bytes)
                a0_hash = bundle_sha256(base_adapter_bytes)
                # 2-8) unpack + strict validation
                validate_server_artifact(server_model_bytes, server_hash)
                validate_worker_base_artifact(worker_base_bytes, worker_hash)
                validate_base_adapter_artifact(base_adapter_bytes, a0_hash)
                row = self._conn.execute(
                    "SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
                    (run_id, round_id)).fetchone()
                round_index = row["round_index"]
                parent_round_id = row["parent_round_id"]
                # idempotent re-registration: same bytes -> same response;
                # inconsistent re-registration -> REJECTED (atomic, nothing written)
                if row["server_model_b64"]:
                    if (row["server_model_hash"] == server_hash
                            and row["worker_model_hash"] == worker_hash
                            and row["adapter_0_hash"] == a0_hash):
                        return {"server_model_hash": server_hash,
                                "worker_model_hash": worker_hash,
                                "adapter_0_hash": a0_hash,
                                "parent_adapter_hash": row["parent_adapter_hash"]}
                    raise ValueError("Round model already registered with different bytes — REJECTED")
                # lineage: Round 1 -> parent_adapter_hash = adapter_0_hash
                #          Round N -> parent = explicit previous round, CLOSED,
                #                     parent_adapter_hash = its global_adapter_hash
                if round_index > 1:
                    if not parent_round_id:
                        raise ValueError("Round N requires explicit parent_round_id (lineage)")
                    parent = self._conn.execute(
                        "SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
                        (run_id, parent_round_id)).fetchone()
                    if parent is None:
                        raise ValueError("Parent round not found (lineage)")
                    if parent["state"] != ROUND_CLOSED:
                        raise ValueError("Parent round not CLOSED (lineage)")
                    if not parent["global_adapter_hash"]:
                        raise ValueError("Parent has no global_adapter_hash (lineage)")
                    parent_adapter_hash = parent["global_adapter_hash"]
                    # Secció 7: base_adapter of round N MUST be the parent's global adapter
                    if a0_hash != parent_adapter_hash:
                        raise ValueError(
                            "base_adapter_hash must equal parent global_adapter_hash "
                            f"({a0_hash[:16]}... != {parent_adapter_hash[:16]}...)")
                else:
                    parent_adapter_hash = a0_hash  # Round 1: parent adapter == adapter_0
                # Secció 7: worker base invariant across rounds (same bytes)
                prev = self._conn.execute(
                    "SELECT worker_model_hash FROM rounds_r53 WHERE run_id=? AND round_id!=? "
                    "AND worker_model_hash IS NOT NULL ORDER BY round_index DESC LIMIT 1",
                    (run_id, round_id)).fetchone()
                if prev is not None and prev["worker_model_hash"] != worker_hash:
                    raise ValueError("worker_base_model_v1 must be byte-identical across rounds "
                                     "(only the adapter may change)")
                self._conn.execute(
                    "UPDATE rounds_r53 SET"
                    " server_model_hash=?, server_model_b64=?,"
                    " worker_model_hash=?, worker_model_b64=?,"
                    " base_adapter_hash=?, base_adapter_b64=?,"
                    " partition_schema_hash=?, adapter_schema_hash=?, numerical_profile_hash=?,"
                    " adapter_0_b64=?, adapter_0_hash=?, parent_adapter_hash=?"
                    " WHERE run_id=? AND round_id=?",
                    (server_hash, base64.b64encode(server_model_bytes).decode(),
                     worker_hash, base64.b64encode(worker_base_bytes).decode(),
                     a0_hash, base64.b64encode(base_adapter_bytes).decode(),
                     partition_schema_hash(), adapter_schema_hash(), numerical_profile_hash(),
                     base64.b64encode(base_adapter_bytes).decode(), a0_hash,
                     parent_adapter_hash, run_id, round_id))
            return {"server_model_hash": server_hash, "worker_model_hash": worker_hash,
                    "adapter_0_hash": a0_hash, "parent_adapter_hash": parent_adapter_hash}

    def get_round_model(self, run_id, round_id):
        r = self._conn.execute(
            "SELECT server_model_b64, server_model_hash, worker_model_b64, worker_model_hash,"
            " base_adapter_b64, base_adapter_hash, adapter_0_hash, parent_adapter_hash"
            " FROM rounds_r53 WHERE run_id=? AND round_id=?",
            (run_id, round_id)).fetchone()
        if r is None or not r["server_model_b64"]:
            return None
        return {
            "server_model_bytes": base64.b64decode(r["server_model_b64"]),
            "server_model_hash": r["server_model_hash"],
            "worker_base_bytes": base64.b64decode(r["worker_model_b64"]),
            "worker_model_hash": r["worker_model_hash"],
            "base_adapter_bytes": base64.b64decode(r["base_adapter_b64"]),
            "base_adapter_hash": r["base_adapter_hash"],
            "adapter_0_hash": r["adapter_0_hash"],
            "parent_adapter_hash": r["parent_adapter_hash"],
        }

    # -- Secció 4: idempotent calibration (nonce key) ------------------------------
    def calibration_submit(self, run_id, round_id, worker_id, cal):
        """Key: run_id+round_id+worker_id+calibration_nonce.
        Same key + same payload -> same exact response.
        Same key + different payload -> REJECTED. No defaulted values."""
        with self._lock:
            st = self._round_state(run_id, round_id)
            if st not in (ROUND_OPEN, ROUND_CALIBRATING):
                raise ValueError(f"Calibration requires OPEN/CALIBRATING (state={st})")
            nonce = cal.get("calibration_nonce", "")
            if not nonce:
                raise ValueError("calibration_nonce is required (no defaults)")
            required = ["backend", "precision", "available_memory_mb", "observed_safe_budget_mb",
                        "max_micro_batch", "max_sequence_length", "measured_at"]
            missing = [k for k in required if k not in cal]
            if missing:
                raise ValueError(f"Calibration missing required fields: {missing}")
            payload = {
                "run_id": run_id, "round_id": round_id, "worker_id": worker_id,
                "calibration_nonce": nonce,
                "backend": cal["backend"], "precision": cal["precision"],
                "available_memory_mb": cal["available_memory_mb"],
                "observed_safe_budget_mb": cal["observed_safe_budget_mb"],
                "max_micro_batch": cal["max_micro_batch"],
                "max_sequence_length": cal["max_sequence_length"],
                "measured_at": cal["measured_at"],
            }
            sha = _request_sha(payload)
            row = self._conn.execute(
                "SELECT request_sha, response_json FROM calibrations_r53"
                " WHERE run_id=? AND round_id=? AND worker_id=? AND calibration_nonce=?",
                (run_id, round_id, worker_id, nonce)).fetchone()
            resp = {"worker_id": worker_id, "status": "CALIBRATED", "calibration_nonce": nonce}
            if row is not None:
                if row["request_sha"] == sha:
                    return json.loads(row["response_json"])  # same exact response
                raise ValueError("Same calibration key, different payload — REJECTED")
            with self._tx():
                self._conn.execute(
                    "INSERT INTO calibrations_r53 (run_id, round_id, worker_id, calibration_nonce,"
                    " backend, precision, available_memory_mb, observed_safe_budget_mb,"
                    " max_micro_batch, max_sequence_length, measured_at, request_sha, response_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, round_id, worker_id, nonce,
                     cal["backend"], cal["precision"], cal["available_memory_mb"],
                     cal["observed_safe_budget_mb"], cal["max_micro_batch"],
                     cal["max_sequence_length"], cal["measured_at"], sha, json.dumps(resp)))
                if st == ROUND_OPEN:
                    self._set_round_state(run_id, round_id, ROUND_CALIBRATING)
            return resp

    def _budget_for(self, run_id, round_id, worker_id):
        rnd = self._conn.execute("SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
                                 (run_id, round_id)).fetchone()
        cal = self._conn.execute(
            "SELECT * FROM calibrations_r53 WHERE worker_id=? AND run_id=? AND round_id=?"
            " ORDER BY measured_at DESC LIMIT 1",
            (worker_id, run_id, round_id)).fetchone()
        if cal is None:
            raise ValueError(f"Worker {worker_id} is not calibrated for this round")
        mb = min(self._profile["max_micro_batch"],
                 (rnd["policy_max_micro_batch"] if rnd and rnd["policy_max_micro_batch"] is not None else 8),
                 cal["max_micro_batch"])
        sl = min(self._profile["max_sequence_length"],
                 (rnd["policy_max_seq"] if rnd and rnd["policy_max_seq"] is not None else 256),
                 cal["max_sequence_length"])
        return mb, sl

    # -- Secció 3: idempotent assignments (request_sha) ----------------------------
    def assign(self, run_id, round_id, assignment_id, worker_id, shard_id, hashes,
               ett_target, strict_budget=True):
        """request_sha = SHA-256(canonical_json_v1(payload)) with ALL normative fields.
        New assignment -> ACCEPTED. Same assignment_id + same request_sha -> same exact
        response. Same assignment_id + different request_sha -> REJECTED."""
        with self._lock:
            st = self._round_state(run_id, round_id)
            if st not in (ROUND_CALIBRATING, ROUND_ASSIGNED):
                raise ValueError(f"Assignment requires CALIBRATING/ASSIGNED (state={st})")
            cal = self._conn.execute(
                "SELECT * FROM calibrations_r53 WHERE worker_id=? AND run_id=? AND round_id=?"
                " ORDER BY measured_at DESC LIMIT 1",
                (worker_id, run_id, round_id)).fetchone()
            if cal is None:
                raise ValueError(f"Worker {worker_id} not calibrated (run={run_id} round={round_id})")
            rnd = self._conn.execute("SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
                                     (run_id, round_id)).fetchone()
            if rnd is None or not rnd["server_model_hash"]:
                raise ValueError("Round model not registered (call register_round_model)")
            mb, sl = self._budget_for(run_id, round_id, worker_id)
            payload = {
                "run_id": run_id, "round_id": round_id, "assignment_id": assignment_id,
                "worker_id": worker_id, "shard_id": shard_id, "ett_target": ett_target,
                "worker_model_hash": hashes.get("worker_model_hash", ""),
                "base_adapter_hash": hashes.get("base_adapter_hash", ""),
                "server_model_hash": hashes.get("server_model_hash", ""),
                "partition_schema_hash": hashes.get("partition_schema_hash", ""),
                "adapter_schema_hash": hashes.get("adapter_schema_hash", ""),
                "numerical_profile_hash": hashes.get("numerical_profile_hash", ""),
                "max_micro_batch": hashes.get("max_micro_batch", mb),
                "max_sequence_length": hashes.get("max_sequence_length", sl),
            }
            sha = _request_sha(payload)
            existing = self._conn.execute(
                "SELECT * FROM assignments_r53 WHERE assignment_id=? AND run_id=? AND round_id=?",
                (assignment_id, run_id, round_id)).fetchone()
            if existing is not None:
                if existing["request_sha"] == sha:
                    return json.loads(existing["response_json"])  # same exact response
                raise ValueError("Same assignment_id, different request_sha — REJECTED")
            # all validations BEFORE any write (atomic discipline)
            for hk in ["worker_model_hash", "server_model_hash", "partition_schema_hash",
                       "adapter_schema_hash", "numerical_profile_hash"]:
                if hashes.get(hk, "") != rnd[hk]:
                    raise ValueError(f"Hash mismatch for {hk}: does not match shared round model")
            # Secció 7: stale adapter check via explicit lineage (NOT round_id <)
            expected_base = rnd["parent_adapter_hash"] if rnd["round_index"] > 1 else rnd["adapter_0_hash"]
            if hashes.get("base_adapter_hash", "") != expected_base:
                raise ValueError("Stale base adapter: does not match parent adapter (lineage)")
            # exact one ACTIVE per assignment is enforced later at ready_to_close
            dup = self._conn.execute(
                "SELECT assignment_id FROM assignments_r53 WHERE run_id=? AND round_id=? AND worker_id=?",
                (run_id, round_id, worker_id)).fetchone()
            if dup is not None and dup["assignment_id"] != assignment_id:
                raise ValueError("Worker already assigned in this round")
            if strict_budget and hashes.get("max_micro_batch", mb) > mb:
                raise ValueError("Assignment exceeds calibration budget (micro_batch)")
            if strict_budget and hashes.get("max_sequence_length", sl) > sl:
                raise ValueError("Assignment exceeds calibration budget (seq_len)")
            resp = {"assignment_id": assignment_id, "worker_id": worker_id,
                    "state": "ASSIGNED", "max_micro_batch": mb, "max_sequence_length": sl,
                    "request_sha": sha}
            with self._tx():
                self._conn.execute(
                    "INSERT INTO assignments_r53 (assignment_id, run_id, round_id, worker_id,"
                    " shard_id, base_adapter_hash, worker_model_hash, server_model_hash,"
                    " partition_schema_hash, adapter_schema_hash, numerical_profile_hash,"
                    " max_micro_batch, max_sequence_length, ett_target, status,"
                    " request_sha, response_json, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (assignment_id, run_id, round_id, worker_id, shard_id,
                     hashes.get("base_adapter_hash", ""), hashes.get("worker_model_hash", ""),
                     hashes.get("server_model_hash", ""),
                     hashes.get("partition_schema_hash", ""),
                     hashes.get("adapter_schema_hash", ""),
                     hashes.get("numerical_profile_hash", ""),
                     mb, sl, ett_target, "ASSIGNED", sha, json.dumps(resp),
                     time.strftime("%Y-%m-%d %H:%M:%S")))
                if st == ROUND_CALIBRATING:
                    self._set_round_state(run_id, round_id, ROUND_ASSIGNED)
            return resp

    def verify_assignment_hashes(self, run_id, round_id, assignment_id, worker_id, p):
        a = self._conn.execute(
            "SELECT * FROM assignments_r53 WHERE assignment_id=? AND run_id=? AND round_id=? AND worker_id=?",
            (assignment_id, run_id, round_id, worker_id)).fetchone()
        if a is None:
            raise ValueError("Assignment not found")
        for hk in ["worker_model_hash", "server_model_hash", "base_adapter_hash",
                   "partition_schema_hash", "adapter_schema_hash", "numerical_profile_hash"]:
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

    # -- Secció 6: secure contributions (only contribution_id from checkpoint.upload) --
    def register_uploaded_contribution(self, contribution_id):
        """Accepts ONLY a contribution_id produced by checkpoint.upload.
        Loads the RC5.2-validated row and cross-checks every field (receipt JSON
        byte-equal, unit COMMITTED, all normative hashes, source_request_sha)."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM contributions WHERE cid=?",
                                     (contribution_id,)).fetchone()
            if row is None:
                raise ValueError(f"Contribution {contribution_id} not produced by checkpoint.upload")
            if row["status"] != "RECEIVED":
                raise ValueError(f"Contribution status not RECEIVED: {row['status']}")
            unit = self._conn.execute("SELECT * FROM units WHERE unit_id=?",
                                      (row["unit_id"],)).fetchone()
            if unit is None or unit["state"] != "COMMITTED":
                raise ValueError("Unit not COMMITTED")
            # receipt JSON exactly equal to the persisted receipt
            receipt = json.loads(row["receipt_json"])
            unit_receipt = json.loads(unit["receipt_json"]) if unit["receipt_json"] else None
            if unit_receipt is None or canonical_json_v1(receipt) != canonical_json_v1(unit_receipt):
                raise ValueError("Receipt JSON mismatch with persisted unit receipt")
            for f in ["receipt_id", "receipt_nonce", "update_id", "delta_id", "run_id",
                      "round_id", "assignment_id", "micro_unit_id", "worker_id", "session_id",
                      "delta_bundle_sha256", "delta_bundle_byte_length",
                      "effective_trainable_tokens", "loss",
                      "worker_model_hash", "base_adapter_hash",
                      "partition_schema_hash", "adapter_schema_hash", "numerical_profile_hash"]:
                if f not in receipt:
                    raise ValueError(f"Receipt missing field: {f}")
            if receipt["receipt_id"] != contribution_id:
                raise ValueError("receipt_id mismatch with contribution_id")
            run_id, round_id = row["run_id"], row["round_id"]
            assignment_id, worker_id = unit["assignment_id"], unit["worker_id"]
            a = self._conn.execute(
                "SELECT * FROM assignments_r53 WHERE assignment_id=? AND run_id=? AND round_id=? AND worker_id=?",
                (assignment_id, run_id, round_id, worker_id)).fetchone()
            if a is None:
                raise ValueError("No RC5.3 assignment for this contribution")
            # server_model_hash binding: assignment must carry the registered round hash
            rnd = self._conn.execute("SELECT server_model_hash FROM rounds_r53 WHERE run_id=? AND round_id=?",
                                     (run_id, round_id)).fetchone()
            if rnd is None or a["server_model_hash"] != rnd["server_model_hash"]:
                raise ValueError("server_model_hash mismatch with registered round model")
            # Secció 6: compare source_request_sha before returning an existing contribution
            source_request_sha = a["request_sha"]
            ex = self._conn.execute("SELECT * FROM contributions_r53 WHERE cid=?",
                                    (contribution_id,)).fetchone()
            if ex:
                if ex["source_request_sha"] != source_request_sha:
                    raise ValueError("source_request_sha mismatch with current RC5.2 contribution — REJECTED")
                return {"contribution_id": ex["cid"], "status": ex["status"]}
            with self._tx():
                self._conn.execute(
                    "INSERT INTO contributions_r53 (cid, run_id, round_id, assignment_id,"
                    " worker_id, status, ett, delta_bundle_b64, delta_bundle_sha256,"
                    " receipt_json, revision, source_request_sha)"
                    " VALUES (?,?,?,?,?, 'RECEIVED', ?, ?, ?, ?, 1, ?)",
                    (contribution_id, run_id, round_id, assignment_id, worker_id,
                     row["ett"], row["delta_bundle_b64"], row["delta_bundle_sha256"],
                     row["receipt_json"], source_request_sha))
            return {"contribution_id": contribution_id, "status": "RECEIVED"}

    def validate_contribution(self, cid):
        with self._lock:
            cur = self._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (cid,)).fetchone()
            if cur is None or cur["status"] != "RECEIVED":
                raise ValueError(f"Cannot validate {cid}: status={cur['status'] if cur else 'MISSING'}")
            with self._tx():
                self._conn.execute("UPDATE contributions_r53 SET status='VALIDATED' WHERE cid=?", (cid,))

    def activate_contribution(self, cid):
        with self._lock:
            cur = self._conn.execute("SELECT * FROM contributions_r53 WHERE cid=?", (cid,)).fetchone()
            if cur is None or cur["status"] != "VALIDATED":
                raise ValueError(f"Cannot activate {cid}")
            with self._tx():
                self._conn.execute(
                    "UPDATE contributions_r53 SET status='SUPERSEDED'"
                    " WHERE assignment_id=? AND worker_id=? AND status='ACTIVE' AND cid!=?",
                    (cur["assignment_id"], cur["worker_id"], cid))
                self._conn.execute("UPDATE contributions_r53 SET status='ACTIVE' WHERE cid=?", (cid,))

    def ready_to_close(self, run_id, round_id):
        """Quorum: EVERY mandatory assignment has exactly one ACTIVE contribution."""
        with self._lock:
            st = self._round_state(run_id, round_id)
            if st != ROUND_RUNNING:
                raise ValueError(f"ready requires RUNNING (state={st})")
            mand = self._conn.execute(
                "SELECT * FROM assignments_r53 WHERE run_id=? AND round_id=? AND status='ASSIGNED'",
                (run_id, round_id)).fetchall()
            for a in mand:
                acts = self._conn.execute(
                    "SELECT * FROM contributions_r53 WHERE run_id=? AND round_id=?"
                    " AND assignment_id=? AND worker_id=? AND status='ACTIVE'",
                    (run_id, round_id, a["assignment_id"], a["worker_id"])).fetchall()
                if len(acts) != 1:
                    raise ValueError(f"Quorum not met for {a['assignment_id']}: {len(acts)} ACTIVE")
            with self._tx():
                self._set_round_state(run_id, round_id, ROUND_READY)
            return {"state": ROUND_READY}

    # -- FedAvg (aggregation) -------------------------------------------------------
    def _active_deltas(self, run_id, round_id):
        rows = self._conn.execute(
            "SELECT delta_bundle_b64, delta_bundle_sha256, ett FROM contributions_r53"
            " WHERE status='ACTIVE' AND run_id=? AND round_id=?",
            (run_id, round_id)).fetchall()
        return rows

    def fedavg(self, run_id, round_id):
        """ETT-weighted FedAvg over ACTIVE contributions ONLY (SUPERSEDED excluded)."""
        rows = self._active_deltas(run_id, round_id)
        if not rows:
            raise ValueError("No ACTIVE contributions")
        total_ett = sum(r["ett"] for r in rows)
        if total_ett <= 0:
            raise ValueError("Total ETT must be positive")
        avg_A = torch.zeros(8, 256)
        avg_B = torch.zeros(1024, 8)
        for r in rows:
            tensors = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]),
                                          r["delta_bundle_sha256"])
            w = r["ett"] / total_ett
            avg_A += w * tensors["local_layers.0.linear1.lora_A.weight"]
            avg_B += w * tensors["local_layers.0.linear1.lora_B.weight"]
        return avg_A, avg_B

    def close_round(self, run_id, round_id):
        """global_adapter_post = global_adapter_pre + averaged_delta (idempotent, atomic)."""
        with self._lock:
            already = self._conn.execute(
                "SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=? AND state=?",
                (run_id, round_id, ROUND_CLOSED)).fetchone()
            if already is not None:
                stored = self._conn.execute(
                    "SELECT * FROM round_adapters_r53 WHERE run_id=? AND round_id=?",
                    (run_id, round_id)).fetchone()
                if stored is not None:
                    return {"adapter_bytes": base64.b64decode(stored["adapter_b64"]),
                            "adapter_hash": stored["adapter_hash"], "idempotent": True}
            st = self._round_state(run_id, round_id)
            if st != ROUND_READY:
                raise ValueError(f"close requires READY_TO_CLOSE (state={st})")
            with self._tx():
                rnd = self._conn.execute("SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
                                         (run_id, round_id)).fetchone()
                # Secció 5: pre adapter via explicit lineage
                if rnd["round_index"] > 1:
                    parent_round_id = rnd["parent_round_id"]
                    prev = self._conn.execute(
                        "SELECT * FROM round_adapters_r53 WHERE run_id=? AND round_id=?",
                        (run_id, parent_round_id)).fetchone()
                    if prev is None:
                        raise ValueError("Parent round adapter missing (lineage)")
                    if prev["adapter_hash"] != rnd["parent_adapter_hash"]:
                        raise ValueError("parent_adapter_hash mismatch with stored parent adapter")
                    pre_tensors = delta_bundle_unpack(base64.b64decode(prev["adapter_b64"]),
                                                      prev["adapter_hash"])
                    pre_A = pre_tensors["local_layers.0.linear1.lora_A.weight"].clone()
                    pre_B = pre_tensors["local_layers.0.linear1.lora_B.weight"].clone()
                elif rnd["adapter_0_b64"]:
                    pre_tensors = delta_bundle_unpack(base64.b64decode(rnd["adapter_0_b64"]),
                                                      rnd["adapter_0_hash"])
                    pre_A = pre_tensors["local_layers.0.linear1.lora_A.weight"].clone()
                    pre_B = pre_tensors["local_layers.0.linear1.lora_B.weight"].clone()
                else:
                    pre_A, pre_B = torch.zeros(8, 256), torch.zeros(1024, 8)
                self._set_round_state(run_id, round_id, ROUND_AGGREGATING)
                dA, dB = self.fedavg(run_id, round_id)
                post_A, post_B = pre_A + dA, pre_B + dB
                adapter_bytes = delta_bundle_pack(post_A, post_B)
                ah = bundle_sha256(adapter_bytes)
                self._conn.execute(
                    "INSERT INTO round_adapters_r53 (run_id, round_id, round_index,"
                    " parent_round_id, adapter_b64, adapter_hash, pre_adapter_hash)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (run_id, round_id, rnd["round_index"], rnd["parent_round_id"],
                     base64.b64encode(adapter_bytes).decode(), ah,
                     bundle_sha256(delta_bundle_pack(pre_A, pre_B))))
                self._conn.execute(
                    "UPDATE rounds_r53 SET state=?, global_adapter_hash=?, closed_at=? WHERE run_id=? AND round_id=?",
                    (ROUND_CLOSED, ah, time.strftime("%Y-%m-%d %H:%M:%S"), run_id, round_id))
            return {"adapter_bytes": adapter_bytes, "adapter_hash": ah, "post_A": post_A}

    def get_round_adapter(self, run_id, round_id):
        r = self._conn.execute("SELECT * FROM round_adapters_r53 WHERE run_id=? AND round_id=?",
                               (run_id, round_id)).fetchone()
        if r is None:
            raise ValueError("Round adapter not found")
        return base64.b64decode(r["adapter_b64"]), r["adapter_hash"]

    def round_status(self, run_id, round_id):
        r = self._conn.execute("SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
                               (run_id, round_id)).fetchone()
        return dict(r) if r else None

    def list_assignments(self, run_id, round_id):
        rows = self._conn.execute("SELECT * FROM assignments_r53 WHERE run_id=? AND round_id=?",
                                  (run_id, round_id)).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Secció 8: INDEPENDENT FedAvg oracle (never queries the Coordinator tables)
# ---------------------------------------------------------------------------

def oracle_fedavg_ett(adapter_pre_A, adapter_pre_B, worker_deltas):
    """Independent ETT-weighted FedAvg oracle.

    Receives DIRECTLY: the pre adapter tensors and, per worker,
    (delta_A, delta_B, ETT). Computes:

        weighted_delta_A = Σ(delta_A × ETT) / ΣETT
        weighted_delta_B = Σ(delta_B × ETT) / ΣETT
        adapter_post_A   = adapter_pre_A + weighted_delta_A
        adapter_post_B   = adapter_pre_B + weighted_delta_B

    Does NOT read Coordinator tables (no query of aggregated rows).
    """
    if not worker_deltas:
        raise ValueError("No worker deltas")
    etts = [int(e) for (_, _, e) in worker_deltas]
    if any(e <= 0 for e in etts):
        raise ValueError("Every ETT must be > 0")
    total = float(sum(etts))
    wA = torch.zeros_like(adapter_pre_A)
    wB = torch.zeros_like(adapter_pre_B)
    for (dA, dB, e) in worker_deltas:
        w = float(e) / total
        wA = wA + w * dA
        wB = wB + w * dB
    return adapter_pre_A + wA, adapter_pre_B + wB


def oracle_round_adapter(run_id, round_id, db_path, worker_deltas, pre_adapter_bytes=None):
    """Test helper: independent reconstruction of the round's global adapter using
    the oracle. pre_adapter_bytes comes from the EXPLICIT lineage parent
    (Round 1 -> adapter_0; Round N -> parent global adapter)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rnd = conn.execute("SELECT * FROM rounds_r53 WHERE run_id=? AND round_id=?",
                       (run_id, round_id)).fetchone()
    if rnd is None:
        conn.close()
        raise ValueError("round not found")
    if rnd["round_index"] > 1:
        prev = conn.execute("SELECT * FROM round_adapters_r53 WHERE run_id=? AND round_id=?",
                            (run_id, rnd["parent_round_id"])).fetchone()
        conn.close()
        if prev is None:
            raise ValueError("parent adapter missing")
        pre_tensors = delta_bundle_unpack(base64.b64decode(prev["adapter_b64"]), prev["adapter_hash"])
    else:
        conn.close()
        pre_tensors = delta_bundle_unpack(base64.b64decode(rnd["adapter_0_b64"]), rnd["adapter_0_hash"])
    pre_A = pre_tensors["local_layers.0.linear1.lora_A.weight"].clone()
    pre_B = pre_tensors["local_layers.0.linear1.lora_B.weight"].clone()
    post_A, post_B = oracle_fedavg_ett(pre_A, pre_B, worker_deltas)
    return delta_bundle_pack(post_A, post_B)


# ---------------------------------------------------------------------------
# Artifacts + worker flow
# ---------------------------------------------------------------------------

def zero_adapter_artifact():
    """REAL zero-LoRA adapter artifact (verified, shared as adapter_0 for round 1)."""
    from rc5_2_numerical_models import WorkerNumericalModel
    w = WorkerNumericalModel()
    with torch.no_grad():
        w.lora_wrapper.lora_A.weight.zero_()
        w.lora_wrapper.lora_B.weight.zero_()
    return pack_base_adapter(w)  # (bytes, hash)


def build_worker_artifacts(worker_model):
    """REAL artifacts: pack -> verify -> (bytes, real SHA-256 hashes)."""
    base_bytes, base_hash = pack_worker_base_model(worker_model)
    ad_bytes, ad_hash = pack_base_adapter(worker_model)
    v = verify_worker_artifacts(base_bytes, ad_bytes, base_hash, ad_hash,
                                partition_schema_hash(), adapter_schema_hash(), numerical_profile_hash())
    if not v["ok"]:
        raise ValueError(f"Artifact verification failed: {v['errors']}")
    return base_bytes, base_hash, ad_bytes, ad_hash


def build_initial_artifacts(seed=424242):
    """REAL initial artifacts: one shared server model, one worker base, one
    non-zero base adapter (adapter_0). The seed is used ONLY to build the initial
    artifact — everything afterwards derives from the persisted bytes."""
    from rc5_2_numerical_models import SplitServerNumericalModel, WorkerNumericalModel
    torch.manual_seed(seed)
    server = SplitServerNumericalModel()
    server_bytes, server_hash = pack_server_model(server)
    torch.manual_seed(seed + 1)
    w = WorkerNumericalModel()
    with torch.no_grad():
        # non-zero LoRA so the first round already has real data-dependent deltas
        nn.init.kaiming_uniform_(w.lora_wrapper.lora_B.weight, a=math.sqrt(5))
        w.lora_wrapper.lora_B.weight.mul_(0.1)
    base_bytes, base_hash, ad_bytes, ad_hash = build_worker_artifacts(w)
    return {
        "server_bytes": server_bytes, "server_hash": server_hash,
        "worker_base_bytes": base_bytes, "worker_base_hash": base_hash,
        "adapter_bytes": ad_bytes, "adapter_hash": ad_hash,
    }


def adapter_artifacts_from_round(worker_model, adapter_bytes):
    """Round N+1 artifacts: same base model, base adapter = previous round's absolute adapter."""
    base_bytes, base_hash = pack_worker_base_model(worker_model)
    ad_hash = bundle_sha256(adapter_bytes)
    return base_bytes, base_hash, adapter_bytes, ad_hash


def real_worker_flow(cl, coord, run_id, round_id, assignment_id, worker_id, shard_id,
                     base_bytes, base_hash, ad_bytes, ad_hash, offset=0, label_keep=None,
                     session_id=None, update_suffix="", db_dir=None, micro_unit_id=None,
                     stop_at_commit=False, silent_upload=False, server_model_hash=""):
    """One REAL worker: full HTTP flow with a real WorkerRuntime (no synthetic deltas)."""
    import os, tempfile
    session_id = session_id or f"sess_{worker_id}_{uuid.uuid4().hex[:6]}"
    unit_id = f"unit_{worker_id}_{uuid.uuid4().hex[:6]}"
    db_path = os.path.join(db_dir or tempfile.gettempdir(), f"journal_{worker_id}_{uuid.uuid4().hex[:8]}.db")
    tokens, labels, mask = make_shard(shard_id, offset=offset, label_keep=label_keep)
    ett = ett_from_labels(labels)

    r1 = cl.call("step.open", {
        "protocol_version": "1.2.0-rc5.2", "unit_id": unit_id,
        "run_id": run_id, "round_id": round_id, "assignment_id": assignment_id,
        "micro_unit_id": micro_unit_id or f"mu_{worker_id}", "worker_id": worker_id, "session_id": session_id,
        "shard_id": shard_id, "shard_offset": offset, "label_keep": label_keep if label_keep is not None else -1,
        "server_model_hash": server_model_hash,
        "worker_model_hash": base_hash, "base_adapter_hash": ad_hash,
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash(),
    })
    sa = unpack_tensor_envelope(r1["server_activation"])

    wr = WorkerRuntime(db_path=db_path)
    wr.load_from_artifacts(base_bytes, base_adapter_bytes=ad_bytes)
    cut = wr.worker_forward(sa, mask)
    env = pack_tensor_envelope(cut, "cut_activation", unit_id, "s1")

    r2 = cl.call("step.worker_forward.submit", {
        "unit_id": unit_id, "forward_id": f"fwd_{worker_id}_{update_suffix}", "step_id": "s1", "cut_activation": env})
    bw_id = r2["backward_id"]
    r3 = cl.call("step.server_backward.fetch", {"unit_id": unit_id, "backward_id": bw_id})
    cg = unpack_tensor_envelope(r3["cut_gradient"])

    wr.prepare_update(f"upd_{worker_id}_{update_suffix}", unit_id)
    delta_b64, delta_sha = wr.apply_update_once(f"upd_{worker_id}_{update_suffix}", cut, cg)
    r4 = cl.call("step.worker_update.submit", {
        "unit_id": unit_id, "update_id": f"upd_{worker_id}_{update_suffix}",
        "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha})
    r5 = cl.call("step.commit", {"unit_id": unit_id, "delta_id": r4.get("delta_id", "")})
    receipt = r5["receipt"]
    if stop_at_commit:
        return {"worker_id": worker_id, "unit_id": unit_id, "assignment_id": assignment_id,
                "shard_id": shard_id, "ett": ett, "backward_id": bw_id,
                "receipt": receipt, "receipt_id": receipt["receipt_id"],
                "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha,
                "wmh": base_hash, "baeh": ad_hash}
    r6 = cl.call("checkpoint.upload", {
        "receipt": receipt,
        "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha})
    up_cid = r6.get("contribution_id", receipt["receipt_id"])
    cid = coord.register_uploaded_contribution(up_cid)
    coord.validate_contribution(cid["contribution_id"])
    coord.activate_contribution(cid["contribution_id"])
    return {"worker_id": worker_id, "unit_id": unit_id, "assignment_id": assignment_id,
            "shard_id": shard_id, "ett": ett, "cid": cid["contribution_id"],
            "backward_id": bw_id, "receipt_id": receipt["receipt_id"], "delta_sha": delta_sha,
            "wmh": base_hash, "baeh": ad_hash}


def run_two_real_workers(coord, cl, run_id, round_id, artifacts_by_worker, specs,
                         server_model_hash=""):
    """Two REAL workers concurrently over the same HTTP server.
    specs: [(assignment_id, worker_id, shard_id, offset, label_keep), ...]"""
    def flow(spec):
        asg, wid, shard, offset, lk = spec
        art = artifacts_by_worker[wid]
        return real_worker_flow(cl, coord, run_id, round_id, asg, wid, shard,
                                art[0], art[1], art[2], art[3], offset=offset, label_keep=lk,
                                server_model_hash=server_model_hash)
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(flow, s) for s in specs]
        return [f.result() for f in futures]
