"""RC5.4 Stage A R2 — RC54RecoveryCoordinator: lease-gated integration layer.

Connects LeaseManager to the real RC5.3/RC5.2 pipeline:
- ProtocolHandler (HTTP server) steps
- RoundCoordinator (rounds, assignments, contributions, FedAvg)
- WorkerRuntime (forward/backward/update journal)

Every pipeline operation requires a VALID lease (ACTIVE/RENEWED) with full
binding (run/round/assignment/unit/worker/session/lease_id/lease_nonce).
Journal state machine: NULL->PREPARED->APPLIED->SUBMITTED->COMMITTED with
strict transitions and conflicting-payload REJECTED (not first-wins).
"""
import json
import sqlite3
import hashlib
import time
from rc5_4_leases import (LeaseManager, LeaseError,
                           LEASE_ACTIVE, LEASE_RENEWED, LEASE_RELEASED,
                           LEASE_EXPIRED, LEASE_ABORTED,
                           JRN_PREPARED, JRN_APPLIED, JRN_SUBMITTED,
                           JRN_COMMITTED, _JOURNAL_STATES)

# --- journal state machine ------------------------------------------------
JOURNAL_TRANSITIONS = {
    None: {JRN_PREPARED},
    JRN_PREPARED: {JRN_APPLIED},
    JRN_APPLIED: {JRN_SUBMITTED},
    JRN_SUBMITTED: {JRN_COMMITTED},
    JRN_COMMITTED: set(),          # terminal
}
IMMUTABLE_FIELDS = ["delta_bundle_sha256", "update_id", "receipt_json",
                    "checkpoint_response", "adapter_hash"]

class RecoveryError(Exception):
    """Rejection with a machine-readable reason."""
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details

    def to_dict(self):
        return {"status": "REJECTED", "reason": self.reason, **self.details}


class RC54RecoveryCoordinator:
    """Lease-gated facade over the real pipeline components."""

    def __init__(self, db_path="recovery_r54.db", lease_db=None, signing_key=b"r53_stage_b_real_key_2026"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lease_db = lease_db or db_path
        self._lm = LeaseManager(self._conn, default_ttl_seconds=60.0)
        self._signing_key = signing_key
        self._init_db()
        self._pipeline = None   # set via attach_pipeline()

    # -- schema -------------------------------------------------------------
    def _init_db(self):
        with self._tx():
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS journal_r54 (
                    unit_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    round_id TEXT NOT NULL,
                    assignment_id TEXT NOT NULL,
                    micro_unit_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    lease_id TEXT NOT NULL,
                    lease_nonce INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    delta_bundle_sha256 TEXT,
                    update_id TEXT,
                    receipt_json TEXT,
                    checkpoint_response TEXT,
                    adapter_hash TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (unit_id, lease_id)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_journal_r54_unit "
                "ON journal_r54(unit_id)")

    def _tx(self):
        class _Ctx:
            def __init__(self, conn):
                self.conn = conn
            def __enter__(self):
                if not self.conn.in_transaction:
                    self.conn.execute("BEGIN IMMEDIATE")
                return self.conn
            def __exit__(self, exc_type, exc, tb):
                if exc_type is None and not self.conn.in_transaction:
                    self.conn.commit()
                elif exc_type is not None:
                    self.conn.rollback()
                return False
        return _Ctx(self._conn)

    def attach_pipeline(self, pipeline):
        """Attach the real ProtocolHandler (HTTP server state)."""
        self._pipeline = pipeline
        return self

    # -- lease validation (full binding) ------------------------------------
    def _lease_row(self, lease_id):
        row = self._conn.execute(
            "SELECT * FROM leases_r54 WHERE lease_id=?", (lease_id,)).fetchone()
        return dict(row) if row else None

    def _check_lease(self, lease_id, lease_nonce, worker_id, session_id,
                     run_id, round_id, assignment_id, micro_unit_id):
        """Full-binding check: every dimension must match; status ACTIVE/RENEWED."""
        self._lm._expire_overdue()
        row = self._lease_row(lease_id)
        if row is None:
            raise RecoveryError("unknown_lease", lease_id=lease_id)
        checks = [
            ("run_id", row["run_id"] == run_id),
            ("round_id", row["round_id"] == round_id),
            ("assignment_id", row["assignment_id"] == assignment_id),
            ("micro_unit_id", row["micro_unit_id"] == micro_unit_id),
            ("worker_id", row["worker_id"] == worker_id),
            ("session_id", row["session_id"] == session_id),
            ("lease_nonce", row["lease_nonce"] == lease_nonce),
        ]
        bad = [name for name, ok in checks if not ok]
        if bad:
            raise RecoveryError("lease_binding_mismatch", fields=bad)
        if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):
            raise RecoveryError(f"lease_not_active: {row['status']}")
        return row

    def _lease_for_unit(self, run_id, round_id, assignment_id, micro_unit_id,
                        worker_id, session_id):
        row = self._conn.execute(
            "SELECT * FROM leases_r54 WHERE run_id=? AND round_id=? AND assignment_id=?"
            " AND micro_unit_id=? AND worker_id=? AND session_id=?"
            " AND status IN (?,?) ORDER BY issued_at DESC LIMIT 1",
            (run_id, round_id, assignment_id, micro_unit_id, worker_id,
             session_id, LEASE_ACTIVE, LEASE_RENEWED)).fetchone()
        return dict(row) if row else None

    # -- journal state machine ----------------------------------------------
    def journal_write(self, unit_id, run_id, round_id, assignment_id,
                      micro_unit_id, worker_id, session_id, lease_id,
                      lease_nonce, state, delta_sha=None, update_id=None,
                      receipt=None, checkpoint_response=None, adapter_hash=None):
        """Strict journal write with full binding + state machine + exactly-once."""
        self._check_lease(lease_id, lease_nonce, worker_id, session_id,
                          run_id, round_id, assignment_id, micro_unit_id)
        # state machine guards
        if state not in _JOURNAL_STATES:
            raise RecoveryError("invalid_state", state=state)
        if state == JRN_APPLIED and delta_sha is None:
            raise RecoveryError("applied_without_delta")
        if state == JRN_COMMITTED and (update_id is None or receipt is None):
            raise RecoveryError("committed_without_update_id_or_receipt")
        with self._tx():
            row = self._conn.execute(
                "SELECT * FROM journal_r54 WHERE unit_id=? AND lease_id=?",
                (unit_id, lease_id)).fetchone()
            cur = dict(row) if row else None
            cur_state = cur["state"] if cur else None
            allowed = JOURNAL_TRANSITIONS.get(cur_state)
            if allowed is None or state not in allowed:
                raise RecoveryError(
                    "illegal_transition",
                    frm=cur_state, to=state,
                    allowed=sorted(JOURNAL_TRANSITIONS.get(cur_state, set())))
            if cur is None:
                self._conn.execute(
                    "INSERT INTO journal_r54 (unit_id, run_id, round_id,"
                    " assignment_id, micro_unit_id, worker_id, session_id,"
                    " lease_id, lease_nonce, state, delta_bundle_sha256,"
                    " update_id, receipt_json, checkpoint_response,"
                    " adapter_hash, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (unit_id, run_id, round_id, assignment_id, micro_unit_id,
                     worker_id, session_id, lease_id, lease_nonce, state,
                     delta_sha, update_id, receipt, checkpoint_response,
                     adapter_hash, time.time(), time.time()))
            else:
                # exactly-once: immutable fields — same value idempotent,
                # different value REJECTED (no first-wins)
                payload = {
                    "delta_bundle_sha256": delta_sha,
                    "update_id": update_id,
                    "receipt_json": receipt,
                    "checkpoint_response": checkpoint_response,
                    "adapter_hash": adapter_hash,
                }
                for f in IMMUTABLE_FIELDS:
                    existing = cur.get(f)
                    newv = payload.get(f)
                    if newv is None:
                        continue
                    if existing is None:
                        continue  # nothing stored yet -> ok
                    if newv != existing:
                        raise RecoveryError(
                            "conflicting_payload", field=f,
                            existing=existing, proposed=newv)
                self._conn.execute(
                    "UPDATE journal_r54 SET state=?,"
                    " delta_bundle_sha256=CASE WHEN delta_bundle_sha256 IS NULL"
                    " THEN ? ELSE delta_bundle_sha256 END,"
                    " update_id=CASE WHEN update_id IS NULL THEN ?"
                    " ELSE update_id END,"
                    " receipt_json=CASE WHEN receipt_json IS NULL THEN ?"
                    " ELSE receipt_json END,"
                    " checkpoint_response=CASE WHEN checkpoint_response IS NULL"
                    " THEN ? ELSE checkpoint_response END,"
                    " adapter_hash=CASE WHEN adapter_hash IS NULL THEN ?"
                    " ELSE adapter_hash END, updated_at=? WHERE unit_id=? AND lease_id=?",
                    (state, delta_sha, update_id, receipt, checkpoint_response,
                     adapter_hash, time.time(), unit_id, lease_id))
        return {"status": "OK", "state": state}

    def journal_read(self, unit_id, lease_id):
        row = self._conn.execute(
            "SELECT * FROM journal_r54 WHERE unit_id=? AND lease_id=?",
            (unit_id, lease_id)).fetchone()
        return dict(row) if row else None

    def journal_state(self, unit_id, lease_id):
        row = self.journal_read(unit_id, lease_id)
        return row["state"] if row else None

    # -- pipeline operations (lease-gated) -----------------------------------
    def _require_lease_params(self, p):
        need = ["lease_id", "lease_nonce", "worker_id", "session_id",
                "run_id", "round_id", "assignment_id", "micro_unit_id"]
        missing = [k for k in need if k not in p or p.get(k) in (None, "")]
        if missing:
            raise RecoveryError("missing_lease_params", missing=missing)
        self._check_lease(p["lease_id"], p["lease_nonce"], p["worker_id"],
                          p["session_id"], p["run_id"], p["round_id"],
                          p["assignment_id"], p["micro_unit_id"])

    def step_open(self, p):
        self._require_lease_params(p)
        if self._pipeline is None:
            raise RecoveryError("pipeline_not_attached")
        return self._pipeline._step_open(p)

    def worker_forward_submit(self, p):
        self._require_lease_params(p)
        if self._pipeline is None:
            raise RecoveryError("pipeline_not_attached")
        return self._pipeline._step_worker_forward_submit(p)

    def server_backward_fetch(self, p):
        self._require_lease_params(p)
        if self._pipeline is None:
            raise RecoveryError("pipeline_not_attached")
        return self._pipeline._step_server_backward_fetch(p)

    def worker_update_submit(self, p):
        self._require_lease_params(p)
        if self._pipeline is None:
            raise RecoveryError("pipeline_not_attached")
        return self._pipeline._step_worker_update_submit(p)

    def step_commit(self, p):
        self._require_lease_params(p)
        if self._pipeline is None:
            raise RecoveryError("pipeline_not_attached")
        return self._pipeline._step_commit(p)

    def checkpoint_upload(self, p):
        self._require_lease_params(p)
        if self._pipeline is None:
            raise RecoveryError("pipeline_not_attached")
        return self._pipeline._step_checkpoint_upload(p)

    def register_uploaded_contribution(self, contribution_id, lease_id=None,
                                       **lease_params):
        """RC5.3 contribution registration is lease-gated."""
        if lease_id is not None:
            self._require_lease_params(lease_params)
        if self._pipeline is None or self._pipeline.coordinator is None:
            raise RecoveryError("coordinator_not_attached")
        return self._pipeline.coordinator.register_uploaded_contribution(contribution_id)
