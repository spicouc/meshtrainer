"""
rc3_train_state.py — RC3.3 Training run state machine and SQLite schema.

Extends RC3.1 state machine with training run and batch-level states.

Run-level states (TrainingRunState):
  WAITING      → creada, espera primera assignació
  TRAINING     → batches actius assignats
  PAUSED       → pausada (batches en curs continuen)
  AGGREGATING  → agregant resultats finals
  COMPLETED    → tots els batches completats + checkpoint final
  FAILED       → batch ha excedit max_attempts
  CANCELLED    → cancel·lada per admin

Batch-level states (BatchState):
  WAITING      → disponible per assignar
  ASSIGNED     → assignada a worker (lease actiu)
  TRAINING     → worker ha reportat progrés (lease extendit)
  UPLOADING    → worker ha completat upload
  COMPLETED    → resultat acceptat
  FAILED       → excedit max_attempts
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple


# ── Constants ──────────────────────────────────────────────────────────

TRAIN_RC3_PROTOCOL_VERSION = "rc3-protocol-v1"
TRAIN_RC3_EXTENSION_VERSION = "RC3.3"

# Lease defaults per RC3.3 SPEC §7.2
TRAIN_LEASE_DEFAULT_SECONDS = 120
TRAIN_HEARTBEAT_INTERVAL = 30
TRAIN_HEARTBEAT_TIMEOUT = 90
TRAIN_SUBMIT_TIMEOUT_SECONDS = 60
TRAIN_MAX_ATTEMPTS_PER_BATCH = 3
TRAIN_DEFAULT_MAX_CONCURRENT_BATCHES = 5

# Error codes (RC3.3 SPEC §3)
ERR_INVALID_PARAMS = -32602
ERR_METHOD_NOT_FOUND = -32601
ERR_INTERNAL = -32603
ERR_GENERAL = -32000
ERR_NOT_FOUND = -32001
ERR_IDEMPOTENCY_COLLISION = -32002
ERR_RUN_NOT_TRAINING = -32003
ERR_WORKER_NOT_ASSIGNED = -32004
ERR_DELTA_CONFLICT = -32005
ERR_STALE_GENERATION = -32006
ERR_NO_BATCHES = -32007


# ── State enums ────────────────────────────────────────────────────────

class TrainingRunState(str, Enum):
    WAITING = "WAITING"
    TRAINING = "TRAINING"
    PAUSED = "PAUSED"
    AGGREGATING = "AGGREGATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @classmethod
    def valid_transitions(cls) -> Dict[str, List[str]]:
        return {
            "WAITING": ["TRAINING", "FAILED", "CANCELLED"],
            "TRAINING": ["PAUSED", "AGGREGATING", "FAILED", "CANCELLED"],
            "PAUSED": ["TRAINING", "CANCELLED", "FAILED"],
            "AGGREGATING": ["COMPLETED", "FAILED", "CANCELLED"],
            "COMPLETED": [],  # terminal
            "FAILED": ["WAITING"],  # via retry_run
            "CANCELLED": [],  # terminal
        }

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        return state in ("COMPLETED", "CANCELLED")

    @classmethod
    def validate_transition(cls, from_state: str, to_state: str) -> bool:
        return to_state in cls.valid_transitions().get(from_state, [])


class BatchState(str, Enum):
    WAITING = "WAITING"
    ASSIGNED = "ASSIGNED"
    TRAINING = "TRAINING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @classmethod
    def valid_transitions(cls) -> Dict[str, List[str]]:
        return {
            "WAITING": ["ASSIGNED", "FAILED"],
            "ASSIGNED": ["TRAINING", "WAITING", "FAILED"],
            "TRAINING": ["UPLOADING", "WAITING", "FAILED"],
            "UPLOADING": ["COMPLETED", "WAITING", "FAILED"],
            "COMPLETED": [],  # terminal
            "FAILED": ["WAITING"],  # via reassignment/retry
        }

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        return state in ("COMPLETED",)

    @classmethod
    def validate_transition(cls, from_state: str, to_state: str) -> bool:
        return to_state in cls.valid_transitions().get(from_state, [])


# ── JSON helpers ───────────────────────────────────────────────────────

def jsonrpc_error(code: int, message: str, request_id: Any = None,
                  data: Optional[dict] = None) -> str:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return json.dumps({
        "jsonrpc": "2.0",
        "error": err,
        "id": request_id,
    })


def jsonrpc_result(result: Any, request_id: Any = None) -> str:
    return json.dumps({
        "jsonrpc": "2.0",
        "result": result,
        "id": request_id,
    })


# ── Training run extension tables ─────────────────────────────────────

TRAIN_SCHEMA_SQL = """
-- Audit log (shared with main state)
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    task_id     TEXT,
    run_id      TEXT,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id);

-- Training runs: distributed training lifecycle
CREATE TABLE IF NOT EXISTS training_run (
    run_id              TEXT PRIMARY KEY,
    model_id            TEXT NOT NULL,
    dataset_id          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'WAITING',
    num_batches         INTEGER NOT NULL,
    batch_size          INTEGER NOT NULL,
    learning_rate       REAL NOT NULL DEFAULT 0.0003,
    lora_r              INTEGER NOT NULL DEFAULT 8,
    lora_alpha          INTEGER NOT NULL DEFAULT 16,
    lora_dropout        REAL NOT NULL DEFAULT 0.1,
    seed                INTEGER NOT NULL DEFAULT 42,
    base_checkpoint_id  TEXT NOT NULL,
    model_revision      TEXT NOT NULL,
    adapter_config_hash TEXT NOT NULL,
    dataset_revision    TEXT NOT NULL,
    max_concurrent_batches INTEGER NOT NULL DEFAULT 5,
    aggregation_generation INTEGER NOT NULL DEFAULT 0,
    aggregated_batches  INTEGER NOT NULL DEFAULT 0,
    total_batches       INTEGER NOT NULL DEFAULT 0,
    idempotency_key     TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    started_at          TEXT,
    completed_at        TEXT,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_training_run_status ON training_run(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_training_run_idempotency
    ON training_run(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Individual batches within a run
CREATE TABLE IF NOT EXISTS batch (
    batch_id            TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES training_run(run_id),
    batch_index         INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'WAITING',
    num_samples         INTEGER NOT NULL DEFAULT 4,
    attempt_number      INTEGER NOT NULL DEFAULT 0,
    assigned_worker_id  TEXT,
    worker_session_id   TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(run_id, batch_index)
);

CREATE INDEX IF NOT EXISTS idx_batch_run ON batch(run_id);
CREATE INDEX IF NOT EXISTS idx_batch_status ON batch(status);

-- Per-assignment lease tracking
CREATE TABLE IF NOT EXISTS batch_assignment (
    assignment_id       TEXT PRIMARY KEY,
    batch_id            TEXT NOT NULL REFERENCES batch(batch_id),
    run_id              TEXT NOT NULL REFERENCES training_run(run_id),
    lease_token         TEXT NOT NULL,
    worker_session_id   TEXT NOT NULL,
    worker_id           TEXT NOT NULL,
    attempt_number      INTEGER NOT NULL DEFAULT 1,
    assigned_at         TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at          TEXT NOT NULL,
    is_active           INTEGER NOT NULL DEFAULT 1,
    result_status       TEXT,  -- 'pending', 'accepted', 'rejected'
    delta_hash          TEXT,
    loss                REAL,
    grad_norm           REAL,
    samples_processed   INTEGER,
    progress_pct        INTEGER,
    -- Generation identity (immutable at assignment time)
    aggregation_generation INTEGER NOT NULL DEFAULT 0,
    base_checkpoint_id  TEXT NOT NULL,
    model_revision      TEXT NOT NULL,
    adapter_config_hash TEXT NOT NULL,
    dataset_revision    TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_assignment_batch ON batch_assignment(batch_id);
CREATE INDEX IF NOT EXISTS idx_assignment_run ON batch_assignment(run_id);
CREATE INDEX IF NOT EXISTS idx_assignment_active
    ON batch_assignment(batch_id, is_active) WHERE is_active=1;

-- Idempotency tracking for submit_delta
CREATE TABLE IF NOT EXISTS idempotency_key (
    key_hash            TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    batch_id            TEXT NOT NULL,
    worker_session_id   TEXT NOT NULL,
    request_hash        TEXT NOT NULL,
    response_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_idempotency_composite
    ON idempotency_key(run_id, batch_id, worker_session_id);

-- Training checkpoint records
CREATE TABLE IF NOT EXISTS training_checkpoint (
    checkpoint_id       TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES training_run(run_id),
    aggregation_generation INTEGER NOT NULL DEFAULT 0,
    is_final            INTEGER NOT NULL DEFAULT 0,
    file_path           TEXT NOT NULL,
    file_hash           TEXT NOT NULL,
    file_size_bytes     INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending, valid, failed
    included_batches    TEXT NOT NULL DEFAULT '[]',  -- JSON list of batch_ids
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_checkpoint_run ON training_checkpoint(run_id);
"""


# ── TrainingRunManager ─────────────────────────────────────────────────

class TrainingRunManager:
    """Manages training run lifecycle, batch assignment, and lease tracking.

    Thread-safe (uses SQLite WAL + locking from parent StateStore).
    All methods take an explicit db connection and lock from the caller.
    """

    def __init__(self, db_conn, db_lock: threading.Lock):
        self._conn = db_conn
        self._lock = db_lock

    # ── Schema setup ─────────────────────────────────────────────────

    def ensure_schema(self):
        """Create training tables if they don't exist."""
        with self._lock:
            self._conn.executescript(TRAIN_SCHEMA_SQL)
            # RC3.3.2: Add upload_reference column to batch_assignment (idempotent)
            try:
                self._conn.execute(
                    "ALTER TABLE batch_assignment ADD COLUMN upload_reference TEXT"
                )
            except Exception:
                pass  # Column already exists
            self._conn.commit()

    # ── Training Run CRUD ────────────────────────────────────────────

    def create_run(self, params: dict) -> Tuple[Optional[str], Optional[str]]:
        """Create a new training run.

        Returns (run_id_or_None, error_response_or_None).
        If idempotency_key matches existing run, returns existing run_id.
        """
        required = ["model_id", "dataset_id", "num_batches", "batch_size"]
        for field in required:
            if field not in params:
                return (None, jsonrpc_error(
                    ERR_INVALID_PARAMS, f"Missing required parameter: {field}",
                    data={"required": field}))

        num_batches = params["num_batches"]
        if not isinstance(num_batches, int) or num_batches < 1 or num_batches > 1000:
            return (None, jsonrpc_error(
                ERR_GENERAL, "num_batches must be 1-1000",
                data={"received": num_batches}))

        idempotency_key = params.get("idempotency_key")

        with self._lock:
            # Check idempotency
            if idempotency_key:
                row = self._conn.execute(
                    "SELECT run_id FROM training_run WHERE idempotency_key=?",
                    (idempotency_key,)
                ).fetchone()
                if row:
                    return (row["run_id"], None)

            run_id = f"run-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            agg_gen = params.get("aggregation_generation", 0)

            self._conn.execute(
                """INSERT INTO training_run
                   (run_id, model_id, dataset_id, status, num_batches, batch_size,
                    learning_rate, lora_r, lora_alpha, lora_dropout, seed,
                    base_checkpoint_id, model_revision, adapter_config_hash,
                    dataset_revision, max_concurrent_batches,
                    aggregation_generation, total_batches, idempotency_key,
                    created_at, updated_at)
                   VALUES (?, ?, ?, 'WAITING', ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, params["model_id"], params["dataset_id"],
                 num_batches, params["batch_size"],
                 params.get("learning_rate", 0.0003),
                 params.get("lora_r", 8),
                 params.get("lora_alpha", 16),
                 params.get("lora_dropout", 0.1),
                 params.get("seed", 42),
                 params.get("base_checkpoint_id", "ckpt-base-001"),
                 params.get("model_revision", "sha256:base"),
                 params.get("adapter_config_hash", "sha256:adapter"),
                 params.get("dataset_revision", "sha256:dataset"),
                 params.get("max_concurrent_batches",
                           TRAIN_DEFAULT_MAX_CONCURRENT_BATCHES),
                 agg_gen, num_batches, idempotency_key, now, now),
            )

            # Create batches
            samples_per_batch = params.get("samples_per_batch", 4)
            for i in range(num_batches):
                batch_id = f"b-{run_id[-4:]}-{i:03d}"
                self._conn.execute(
                    "INSERT INTO batch (batch_id, run_id, batch_index, status, num_samples) "
                    "VALUES (?, ?, ?, 'WAITING', ?)",
                    (batch_id, run_id, i, samples_per_batch),
                )

            self._audit("train.run_created", run_id=run_id,
                        payload={"model_id": params["model_id"],
                                 "num_batches": num_batches,
                                 "aggregation_generation": agg_gen})
            self._conn.commit()

        return (run_id, None)

    def get_run(self, run_id: str) -> Optional[Dict]:
        """Get full run state with batches."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM training_run WHERE run_id=?",
                (run_id,)
            ).fetchone()
            if not row:
                return None
            run = dict(row)

            # Attach batches
            batch_rows = self._conn.execute(
                "SELECT batch_id, batch_index, status, attempt_number, "
                "assigned_worker_id FROM batch WHERE run_id=? "
                "ORDER BY batch_index",
                (run_id,)
            ).fetchall()
            run["batches"] = [dict(b) for b in batch_rows]
            return run

    def list_runs(self, status: Optional[str] = None,
                  limit: int = 10) -> List[Dict]:
        """List training runs, optionally filtered by status."""
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM training_run WHERE status=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
                total = self._conn.execute(
                    "SELECT COUNT(*) as cnt FROM training_run WHERE status=?",
                    (status,)
                ).fetchone()["cnt"]
            else:
                rows = self._conn.execute(
                    "SELECT * FROM training_run ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                total = self._conn.execute(
                    "SELECT COUNT(*) as cnt FROM training_run"
                ).fetchone()["cnt"]

            result = []
            for row in rows:
                r = dict(row)
                completed = self._conn.execute(
                    "SELECT COUNT(*) as cnt FROM batch WHERE run_id=? AND status='COMPLETED'",
                    (r["run_id"],)
                ).fetchone()["cnt"]
                r["completed_batches"] = completed
                result.append(r)

            return result, total

    # ── Run state transitions ────────────────────────────────────────

    def transition_run(self, run_id: str, to_state: str,
                       actor: str = "coordinator",
                       reason: str = "") -> bool:
        """Transition a run to a new state. Returns False if invalid."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM training_run WHERE run_id=?",
                (run_id,)
            ).fetchone()
            if not row:
                return False

            from_state = row["status"]
            if not TrainingRunState.validate_transition(from_state, to_state):
                return False

            now = datetime.now(timezone.utc).isoformat()
            updates = {"status": to_state, "updated_at": now}
            if to_state == "TRAINING" and from_state == "WAITING":
                updates["started_at"] = now
            if to_state in ("COMPLETED", "FAILED", "CANCELLED"):
                updates["completed_at"] = now

            set_clause = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [run_id]
            self._conn.execute(
                f"UPDATE training_run SET {set_clause} WHERE run_id=?",
                vals,
            )
            self._audit("train.run_transition", run_id=run_id,
                        payload={"from": from_state, "to": to_state,
                                 "actor": actor, "reason": reason})
            self._conn.commit()
        return True

    # ── Batch operations ─────────────────────────────────────────────

    def get_next_waiting_batch(self, run_id: str) -> Optional[Dict]:
        """Get the next WAITING batch ordered FIFO by batch_index."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM batch WHERE run_id=? AND status='WAITING' "
                "ORDER BY batch_index ASC LIMIT 1",
                (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_batch(self, batch_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM batch WHERE batch_id=?",
                (batch_id,)
            ).fetchone()
            return dict(row) if row else None

    def transition_batch(self, batch_id: str, to_state: str,
                         worker_id: Optional[str] = None,
                         worker_session_id: Optional[str] = None) -> bool:
        """Transition a batch state. Returns False if invalid."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM batch WHERE batch_id=?",
                (batch_id,)
            ).fetchone()
            if not row:
                return False
            from_state = row["status"]
            if not BatchState.validate_transition(from_state, to_state):
                return False

            now = datetime.now(timezone.utc).isoformat()
            updates = {"status": to_state, "updated_at": now}
            if worker_id is not None:
                updates["assigned_worker_id"] = worker_id
            if worker_session_id is not None:
                updates["worker_session_id"] = worker_session_id
            if to_state == "FAILED" and from_state == "WAITING":
                # Increment attempt_number on failure
                cur = self._conn.execute(
                    "SELECT attempt_number FROM batch WHERE batch_id=?",
                    (batch_id,)
                ).fetchone()
                updates["attempt_number"] = cur["attempt_number"] + 1

            set_clause = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [batch_id]
            self._conn.execute(
                f"UPDATE batch SET {set_clause} WHERE batch_id=?",
                vals,
            )
            self._audit("batch.transition", payload={
                "batch_id": batch_id, "from": from_state, "to": to_state})
            self._conn.commit()
        return True

    def get_batch_count_by_status(self, run_id: str,
                                  status: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM batch WHERE run_id=? AND status=?",
                (run_id, status)
            ).fetchone()
            return row["cnt"]

    # ── Batch assignment ─────────────────────────────────────────────

    def assign_batch(self, run_id: str, batch_id: str, worker_id: str,
                     worker_session_id: str,
                     attempt_number: int = 1) -> Optional[Dict]:
        """Assign a batch to a worker. Returns assignment dict or None."""
        with self._lock:
            # Verify batch is WAITING
            batch = self._conn.execute(
                "SELECT * FROM batch WHERE batch_id=? AND status='WAITING'",
                (batch_id,)
            ).fetchone()
            if not batch:
                return None

            # Get run generation context
            run = self._conn.execute(
                "SELECT * FROM training_run WHERE run_id=?",
                (run_id,)
            ).fetchone()
            if not run:
                return None

            assignment_id = f"asgn-{uuid.uuid4().hex[:8]}"
            lease_token = f"lt-{uuid.uuid4().hex}"
            now = datetime.now(timezone.utc)
            expires_at = datetime.fromtimestamp(
                now.timestamp() + TRAIN_LEASE_DEFAULT_SECONDS,
                tz=timezone.utc,
            ).isoformat()

            # Create assignment record
            self._conn.execute(
                """INSERT INTO batch_assignment
                   (assignment_id, batch_id, run_id, lease_token,
                    worker_session_id, worker_id, attempt_number,
                    assigned_at, expires_at, is_active,
                    aggregation_generation, base_checkpoint_id,
                    model_revision, adapter_config_hash, dataset_revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (assignment_id, batch_id, run_id, lease_token,
                 worker_session_id, worker_id, attempt_number,
                 now.isoformat(), expires_at,
                 run["aggregation_generation"], run["base_checkpoint_id"],
                 run["model_revision"], run["adapter_config_hash"],
                 run["dataset_revision"]),
            )

            # Update batch state
            self._conn.execute(
                "UPDATE batch SET status='ASSIGNED', assigned_worker_id=?, "
                "worker_session_id=?, attempt_number=?, updated_at=? "
                "WHERE batch_id=?",
                (worker_id, worker_session_id, attempt_number, now.isoformat(), batch_id),
            )

            # If this is the first batch assigned, transition run to TRAINING
            cur_status = run["status"]
            if cur_status == "WAITING":
                self._conn.execute(
                    "UPDATE training_run SET status='TRAINING', started_at=?, "
                    "updated_at=? WHERE run_id=?",
                    (now.isoformat(), now.isoformat(), run_id),
                )

            self._audit("train.batch_assigned", run_id=run_id,
                        payload={"batch_id": batch_id, "worker_id": worker_id,
                                 "assignment_id": assignment_id,
                                 "attempt_number": attempt_number,
                                 "expires_at": expires_at})
            self._conn.commit()

            # Build response
            run_data = dict(run)
            return {
                "run_id": run_id,
                "assignment_id": assignment_id,
                "batch_id": batch_id,
                "lease_token": lease_token,
                "worker_session_id": worker_session_id,
                "expires_at": expires_at,
                "attempt_number": attempt_number,
                "aggregation_generation": run_data["aggregation_generation"],
                "base_checkpoint_id": run_data["base_checkpoint_id"],
                "model_revision": run_data["model_revision"],
                "adapter_config_hash": run_data["adapter_config_hash"],
                "batch": {
                    "batch_index": batch["batch_index"],
                    "dataset_revision": run_data["dataset_revision"],
                    "num_samples": batch["num_samples"],
                },
            }

    def get_active_assignment(self, batch_id: str) -> Optional[Dict]:
        """Get the currently active assignment for a batch."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM batch_assignment "
                "WHERE batch_id=? AND is_active=1 ORDER BY created_at DESC LIMIT 1",
                (batch_id,)
            ).fetchone()
            return dict(row) if row else None

    def extend_lease(self, assignment_id: str,
                     extra_seconds: int = TRAIN_HEARTBEAT_INTERVAL) -> Optional[str]:
        """Extend a lease. Returns new expires_at or None if assignment invalid."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM batch_assignment WHERE assignment_id=? AND is_active=1",
                (assignment_id,)
            ).fetchone()
            if not row:
                return None

            now = datetime.now(timezone.utc)
            current_expiry = datetime.fromisoformat(row["expires_at"])
            new_expiry = datetime.fromtimestamp(
                max(now.timestamp(), current_expiry.timestamp()) + extra_seconds,
                tz=timezone.utc,
            ).isoformat()

            self._conn.execute(
                "UPDATE batch_assignment SET expires_at=? WHERE assignment_id=?",
                (new_expiry, assignment_id),
            )
            self._conn.commit()
        return new_expiry

    def _expire_stale_assignments(self) -> List[Dict]:
        """Alias for expire_leases() for test compatibility."""
        return self.expire_leases()

    def expire_leases(self) -> List[Dict]:
        """Mark all expired leases as inactive. Return expired assignments."""
        # Use consistent timezone-aware comparison
        now_aware = datetime.now(timezone.utc)
        now_str = now_aware.isoformat()
        with self._lock:
            # Find expired leases: compare stored string with now
            rows = self._conn.execute(
                "SELECT ba.*, b.status as batch_status FROM batch_assignment ba "
                "JOIN batch b ON ba.batch_id=b.batch_id "
                "WHERE ba.is_active=1",
            ).fetchall()

            expired = []
            for row in rows:
                expires_at_dt = datetime.fromisoformat(row["expires_at"])
                if expires_at_dt.tzinfo is None:
                    expires_at_dt = expires_at_dt.replace(tzinfo=timezone.utc)
                if now_aware <= expires_at_dt:
                    continue  # not yet expired

                self._conn.execute(
                    "UPDATE batch_assignment SET is_active=0 WHERE assignment_id=?",
                    (row["assignment_id"],)
                )
                if row["batch_status"] in ("ASSIGNED", "TRAINING", "UPLOADING"):
                    self._conn.execute(
                        "UPDATE batch SET status='WAITING', assigned_worker_id=NULL, "
                        "worker_session_id=NULL, updated_at=? WHERE batch_id=?",
                        (now_str, row["batch_id"]),
                    )
                    self._audit("batch.lease_expired",
                                payload={"batch_id": row["batch_id"],
                                         "assignment_id": row["assignment_id"]})
                expired.append(dict(row))

            self._conn.commit()
        return expired

    # ── Delta submission ─────────────────────────────────────────────

    def submit_delta(self, run_id: str, batch_id: str, params: dict,
                     worker_id: str) -> Dict:
        """Submit a training delta. Returns normalized result dict.

        Validates all 15 checks from SPEC §7.4 before accepting.
        Idempotency check occurs first so replays get fast path.
        """
        # 0. Idempotency check FIRST (fast path for replays)
        idem_key = params.get("idempotency_key", "")
        if idem_key:
            composite = f"{run_id}:{batch_id}:{params.get('worker_session_id', '')}:{idem_key}"
            key_hash = hashlib_hex(composite)
            existing = self._conn.execute(
                "SELECT * FROM idempotency_key WHERE key_hash=?",
                (key_hash,)
            ).fetchone()
            if existing:
                req_hash = hashlib_hex(json.dumps(params, sort_keys=True))
                if existing["request_hash"] == req_hash:
                    return json.loads(existing["response_json"])
                else:
                    return {"accepted": False, "status": "rejected",
                            "error_code": ERR_IDEMPOTENCY_COLLISION,
                            "reason": "idempotency_key_collision"}

        # 1. Get active assignment (or check if batch already completed)
        assignment = self.get_active_assignment(batch_id)
        if not assignment:
            # Maybe batch already COMPLETED — allow idempotent replay
            batch = self.get_batch(batch_id)
            if batch and batch["status"] == "COMPLETED":
                prev_assign = self._conn.execute(
                    "SELECT * FROM batch_assignment WHERE batch_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (batch_id,)
                ).fetchone()
                if prev_assign and params.get("delta_sha256") == prev_assign["delta_hash"]:
                    return {"accepted": True, "batch_id": batch_id,
                            "status": "COMPLETED", "reason": "idempotent_replay"}
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_WORKER_NOT_ASSIGNED,
                    "reason": "No active assignment for this batch"}

        # 2. Validate lease_token
        if params.get("lease_token") != assignment["lease_token"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_WORKER_NOT_ASSIGNED,
                    "reason": "lease_token mismatch"}

        # 3. Validate worker_session_id
        if params.get("worker_session_id") != assignment["worker_session_id"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_WORKER_NOT_ASSIGNED,
                    "reason": "worker_session_id mismatch"}

        # 4. Validate batch_id
        if params.get("batch_id") != assignment["batch_id"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_WORKER_NOT_ASSIGNED,
                    "reason": "batch_id mismatch"}

        # 5. Validate run_id
        if params.get("run_id") != assignment["run_id"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_WORKER_NOT_ASSIGNED,
                    "reason": "run_id mismatch"}

        # 6. Check lease not expired
        expires_at_dt = datetime.fromisoformat(assignment["expires_at"])
        if expires_at_dt.tzinfo is None:
            expires_at_dt = expires_at_dt.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at_dt:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_WORKER_NOT_ASSIGNED,
                    "reason": "lease_expired"}

        # 7. Validate aggregation_generation
        run = self.get_run_raw(run_id)
        if not run:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_NOT_FOUND, "reason": "run_not_found"}
        if params.get("aggregation_generation", 0) != run["aggregation_generation"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_STALE_GENERATION,
                    "reason": "aggregation_generation_mismatch"}

        # 8. Validate model_revision
        if params.get("model_revision") != assignment["model_revision"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_STALE_GENERATION,
                    "reason": "model_revision_mismatch"}

        # 9. Validate adapter_config_hash
        if params.get("adapter_config_hash") != assignment["adapter_config_hash"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_STALE_GENERATION,
                    "reason": "adapter_config_hash_mismatch"}

        # 10. Validate base_checkpoint_id
        if params.get("base_checkpoint_id") != assignment["base_checkpoint_id"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_STALE_GENERATION,
                    "reason": "base_checkpoint_id_mismatch"}

        # 11. Validate dataset_revision
        if params.get("dataset_revision") != assignment["dataset_revision"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_STALE_GENERATION,
                    "reason": "dataset_revision_mismatch"}

        # 12. Check run state
        if run["status"] not in ("TRAINING", "AGGREGATING"):
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_RUN_NOT_TRAINING,
                    "reason": f"run_status_is_{run['status']}"}

        # 13. Check batch not already COMPLETED
        batch = self.get_batch(batch_id)
        if not batch:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_NOT_FOUND, "reason": "batch_not_found"}
        if batch["status"] == "COMPLETED":
            # Idempotent replay: same delta → return success
            if params.get("delta_sha256") == assignment.get("delta_hash"):
                return {"accepted": True, "status": "completed",
                        "reason": "idempotent_replay"}
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_DELTA_CONFLICT,
                    "reason": "batch_already_completed_different_delta"}

        # 14. Idempotency check
        idem_key = params.get("idempotency_key", "")
        if idem_key:
            composite = f"{run_id}:{batch_id}:{assignment['worker_session_id']}:{idem_key}"
            key_hash = hashlib_hex(composite)
            existing = self._conn.execute(
                "SELECT * FROM idempotency_key WHERE key_hash=?",
                (key_hash,)
            ).fetchone()
            if existing:
                # Same request hash → idempotent replay
                if existing["request_hash"] == hashlib_hex(json.dumps(params, sort_keys=True)):
                    return json.loads(existing["response_json"])
                else:
                    return {"accepted": False, "status": "rejected",
                            "error_code": ERR_IDEMPOTENCY_COLLISION,
                            "reason": "idempotency_key_collision"}

        # 15. Delta conflict check (same batch, different delta_hash)
        if assignment.get("delta_hash") and params.get("delta_sha256") != assignment["delta_hash"]:
            return {"accepted": False, "status": "rejected",
                    "error_code": ERR_DELTA_CONFLICT,
                    "reason": "delta_conflict"}

        # ── Accept the delta ─────────────────────────────────────
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            delta_hash = params.get("delta_sha256", "")
            loss = params.get("loss")
            grad_norm = params.get("grad_norm")
            samples = params.get("samples_processed", 0)

            # Update assignment
            self._conn.execute(
                "UPDATE batch_assignment SET is_active=0, result_status='accepted', "
                "delta_hash=?, loss=?, grad_norm=?, samples_processed=?, "
                "upload_reference=? "
                "WHERE assignment_id=?",
                (delta_hash, loss, grad_norm, samples,
                 params.get("upload_reference", ""),
                 assignment["assignment_id"]),
            )

            # Transition batch to COMPLETED
            self._conn.execute(
                "UPDATE batch SET status='COMPLETED', updated_at=? WHERE batch_id=?",
                (now, batch_id),
            )

            # Update run aggregated counter
            self._conn.execute(
                "UPDATE training_run SET aggregated_batches=aggregated_batches+1, "
                "updated_at=? WHERE run_id=?",
                (now, run_id),
            )

            # Store idempotency key
            if idem_key:
                composite = f"{run_id}:{batch_id}:{assignment['worker_session_id']}:{idem_key}"
                key_hash = hashlib_hex(composite)
                req_hash = hashlib_hex(json.dumps(params, sort_keys=True))
                resp_json = json.dumps({
                    "accepted": True, "batch_id": batch_id,
                    "status": "COMPLETED", "reason": "accepted",
                })
                self._conn.execute(
                    "INSERT OR IGNORE INTO idempotency_key "
                    "(key_hash, run_id, batch_id, worker_session_id, "
                    "request_hash, response_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (key_hash, run_id, batch_id, assignment["worker_session_id"],
                     req_hash, resp_json),
                )

            # Audit + commit
            self._audit("train.batch_completed", run_id=run_id,
                        payload={"batch_id": batch_id,
                                 "delta_hash": delta_hash,
                                 "loss": loss,
                                 "samples": samples})
            self._conn.commit()

        # Check if all batches are COMPLETED → transition to AGGREGATING
        total = run["total_batches"]
        with self._lock:
            completed = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM batch WHERE run_id=? AND status='COMPLETED'",
                (run_id,)
            ).fetchone()["cnt"]
        if completed >= total:
            self.transition_run(run_id, "AGGREGATING",
                                reason="all_batches_completed")

        return {"accepted": True, "batch_id": batch_id,
                "status": "COMPLETED", "reason": "accepted"}

    # ── Force aggregate (partial snapshot) ──────────────────────────

    def force_aggregate(self, run_id: str) -> Dict:
        """Take a partial snapshot. No generation increment."""
        run = self.get_run_raw(run_id)
        if not run:
            return {"status": "error", "reason": "run_not_found"}

        completed = self.get_batch_count_by_status(run_id, "COMPLETED")
        if completed == 0:
            return {"status": "error", "reason": "no_completed_batches"}

        # Partial snapshot: no generation increment, no model mutation
        checkpoint_id = f"ckpt-{uuid.uuid4().hex[:8]}"
        snapshot_path = f"runs/{run_id}/checkpoints/partial-{checkpoint_id}.snap"

        with self._lock:
            self._conn.execute(
                "INSERT INTO training_checkpoint "
                "(checkpoint_id, run_id, aggregation_generation, is_final, "
                "file_path, file_hash, file_size_bytes, status, included_batches) "
                "VALUES (?, ?, ?, 0, ?, 'pending', 0, 'valid', '[]')",
                (checkpoint_id, run_id, run["aggregation_generation"], snapshot_path),
            )
            self._conn.commit()

        return {
            "run_id": run_id,
            "phase": "partial",
            "batches_included": completed,
            "status": "AGGREGATING",
            "snapshot_path": snapshot_path,
            "aggregation_generation": run["aggregation_generation"],
        }

    # ── Admin operations ────────────────────────────────────────────

    def cancel_run(self, run_id: str) -> Dict:
        """Cancel a run and all in-progress batches."""
        run = self.get_run_raw(run_id)
        if not run:
            return {"status": "error", "reason": "run_not_found"}
        if TrainingRunState.is_terminal(run["status"]):
            return {"status": "error", "reason": "run_already_terminal"}

        self.transition_run(run_id, "CANCELLED", actor="admin",
                            reason="cancelled_by_admin")

        with self._lock:
            # Cancel all ASSIGNED/TRAINING/UPLOADING batches → WAITING
            self._conn.execute(
                "UPDATE batch SET status='CANCELLED', updated_at=? "
                "WHERE run_id=? AND status IN ('WAITING','ASSIGNED','TRAINING','UPLOADING')",
                (datetime.now(timezone.utc).isoformat(), run_id),
            )
            self._conn.commit()

        completed = self.get_batch_count_by_status(run_id, "COMPLETED")
        cancelled = self.get_batch_count_by_status(run_id, "CANCELLED") or \
            self.get_batch_count_by_status(run_id, "WAITING")

        return {
            "run_id": run_id,
            "status": "CANCELLED",
            "batches_completed": completed,
            "batches_cancelled": run["total_batches"] - completed,
        }

    def pause_run(self, run_id: str) -> Dict:
        """Pause a run. In-progress batches continue, new batches not assigned."""
        run = self.get_run_raw(run_id)
        if not run:
            return {"status": "error", "reason": "run_not_found"}
        if run["status"] != "TRAINING":
            return {"status": "error", "reason": f"cannot_pause_run_in_{run['status']}"}

        self.transition_run(run_id, "PAUSED", actor="admin", reason="paused")

        completed = self.get_batch_count_by_status(run_id, "COMPLETED")
        in_progress = self.get_batch_count_by_status(run_id, "ASSIGNED") + \
            self.get_batch_count_by_status(run_id, "TRAINING") + \
            self.get_batch_count_by_status(run_id, "UPLOADING")

        return {
            "run_id": run_id,
            "status": "PAUSED",
            "batches_completed": completed,
            "batches_in_progress": in_progress,
        }

    def resume_run(self, run_id: str) -> Dict:
        """Resume a paused run."""
        run = self.get_run_raw(run_id)
        if not run:
            return {"status": "error", "reason": "run_not_found"}
        if run["status"] != "PAUSED":
            return {"status": "error", "reason": f"cannot_resume_run_in_{run['status']}"}

        self.transition_run(run_id, "TRAINING", actor="admin", reason="resumed")

        waiting = self.get_batch_count_by_status(run_id, "WAITING")
        completed = self.get_batch_count_by_status(run_id, "COMPLETED")
        in_progress = self.get_batch_count_by_status(run_id, "ASSIGNED") + \
            self.get_batch_count_by_status(run_id, "TRAINING") + \
            self.get_batch_count_by_status(run_id, "UPLOADING")

        return {
            "run_id": run_id,
            "status": "TRAINING",
            "batches_waiting": waiting,
            "batches_in_progress": in_progress,
            "batches_completed": completed,
        }

    def retry_run(self, run_id: str) -> Dict:
        """Retry a FAILED run. Moves FAILED batches back to WAITING."""
        run = self.get_run_raw(run_id)
        if not run:
            return {"status": "error", "reason": "run_not_found"}
        if run["status"] != "FAILED":
            return {"status": "error",
                    "reason": f"cannot_retry_run_in_{run['status']}"}

        with self._lock:
            failed_rows = self._conn.execute(
                "SELECT batch_id FROM batch WHERE run_id=? AND status='FAILED'",
                (run_id,)
            ).fetchall()

            preserved = self.get_batch_count_by_status(run_id, "COMPLETED")
            now = datetime.now(timezone.utc).isoformat()
            for row in failed_rows:
                self._conn.execute(
                    "UPDATE batch SET status='WAITING', assigned_worker_id=NULL, "
                    "worker_session_id=NULL, attempt_number=1, updated_at=? "
                    "WHERE batch_id=?",
                    (now, row["batch_id"]),
                )

            self._conn.execute(
                "UPDATE training_run SET status='WAITING', updated_at=? "
                "WHERE run_id=?",
                (now, run_id),
            )
            self._audit("train.run_retried", run_id=run_id,
                        payload={"batches_retried": len(failed_rows),
                                 "batches_preserved": preserved})
            self._conn.commit()

        return {
            "run_id": run_id,
            "status": "WAITING",
            "batches_retried": len(failed_rows) if 'failed_rows' in dir() else 0,
            "batches_preserved": preserved,
        }

    # ── Recovery ─────────────────────────────────────────────────────

    def recover_after_restart(self):
        """On coordinator restart, expire leases and reset in-progress batches."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            # Deactivate all active assignments (leases expired)
            self._conn.execute(
                "UPDATE batch_assignment SET is_active=0 "
                "WHERE is_active=1 AND expires_at < ?",
                (now,),
            )
            # Reset ASSIGNED/TRAINING/UPLOADING batches back to WAITING
            self._conn.execute(
                "UPDATE batch SET status='WAITING', assigned_worker_id=NULL, "
                "worker_session_id=NULL, updated_at=? "
                "WHERE run_id IN (SELECT run_id FROM training_run "
                "WHERE status IN ('TRAINING','PAUSED')) "
                "AND status IN ('ASSIGNED','TRAINING','UPLOADING')",
                (now,),
            )
            self._conn.commit()

    # ── Helpers ──────────────────────────────────────────────────────

    def get_run_raw(self, run_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM training_run WHERE run_id=?",
                (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def _audit(self, event_type: str, run_id: Optional[str] = None,
               payload: Optional[dict] = None):
        self._conn.execute(
            "INSERT INTO audit_log (event_type, task_id, run_id, payload) "
            "VALUES (?, NULL, ?, ?)",
            (event_type, run_id, json.dumps(payload or {})),
        )
        self._conn.commit()

    def increment_attempt(self, batch_id: str) -> bool:
        """Increment attempt_number. Returns False if max exceeded."""
        with self._lock:
            batch = self._conn.execute(
                "SELECT attempt_number FROM batch WHERE batch_id=?",
                (batch_id,)
            ).fetchone()
            if not batch:
                return False
            new_attempt = batch["attempt_number"] + 1
            if new_attempt > TRAIN_MAX_ATTEMPTS_PER_BATCH:
                self._conn.execute(
                    "UPDATE batch SET status='FAILED', updated_at=? WHERE batch_id=?",
                    (datetime.now(timezone.utc).isoformat(), batch_id),
                )
                # Check if all FAILED → mark run FAILED
                run_id = self._conn.execute(
                    "SELECT run_id FROM batch WHERE batch_id=?",
                    (batch_id,)
                ).fetchone()["run_id"]
                self._check_run_failed(run_id)
                self._conn.commit()
                return False
            self._conn.execute(
                "UPDATE batch SET attempt_number=? WHERE batch_id=?",
                (new_attempt, batch_id),
            )
            self._conn.commit()
        return True

    def _check_run_failed(self, run_id: str):
        """If all batches are FAILED/COMPLETED, mark run FAILED if any batch FAILED."""
        with self._lock:
            total = self._conn.execute(
                "SELECT total_batches FROM training_run WHERE run_id=?",
                (run_id,)
            ).fetchone()["total_batches"]
            failed = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM batch WHERE run_id=? AND status='FAILED'",
                (run_id,)
            ).fetchone()["cnt"]
            if failed > 0:
                self._conn.execute(
                    "UPDATE training_run SET status='FAILED', updated_at=? WHERE run_id=?",
                    (datetime.now(timezone.utc).isoformat(), run_id),
                )
                self._audit("train.run_failed", run_id=run_id,
                            payload={"failed_batches": failed, "total": total})


def hashlib_hex(data: str) -> str:
    import hashlib
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
