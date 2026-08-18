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
# R3.1: les transicions i camps immutables ara viuen a LeaseManager
# (recovery_journal_r54). Es mantenen aquí només com a documentació — el codi
# delega a LeaseManager.journal_set (una sola state machine autoritativa).
JOURNAL_TRANSITIONS_REF = {
    None: {JRN_PREPARED},
    JRN_PREPARED: {JRN_APPLIED},
    JRN_APPLIED: {JRN_SUBMITTED},
    JRN_SUBMITTED: {JRN_COMMITTED},
    JRN_COMMITTED: set(),          # terminal
}

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

    def __init__(self, db_path="recovery_r54.db", lease_db=None, signing_key=b"r53_stage_b_real_key_2026",
                 conn=None):
        self.db_path = db_path
        if conn is not None:
            self._conn = conn  # share the RoundCoordinator connection
        else:
            self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                         isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lease_db = lease_db or db_path
        self._lm = LeaseManager(self._conn, default_ttl_seconds=60.0)
        self._signing_key = signing_key
        self._init_db()
        self._pipeline = None   # set via attach_pipeline()
        self._coordinator = None  # set via attach_coordinator()

    # -- schema -------------------------------------------------------------
    def _init_db(self):
        # R3.1: journal_r54 DEPRECAT — l'únic journal autoritatiu és
        # recovery_journal_r54 (creat per LeaseManager). No es crea cap
        # segona taula de journal aquí perquè NO hi pot haver dues state
        # machines paral·leles.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_journal_r54_unit "
            "ON recovery_journal_r54(unit_id)")
        if not self._conn.in_transaction:
            self._conn.commit()

    def _tx(self):
        """BEGIN IMMEDIATE -> ... -> COMMIT; ROLLBACK on any error.
        R3.1: mateixa disciplina provada de LeaseManager — outer = state ABANS
        del BEGIN; COMMIT/ROLLBACK explícits només si no érem outer (el
        context exterior posseeix la transacció). La versió anterior no feia
        COMMIT (in_transaction era True després del BEGIN, així que el
        __exit__ no commita mai)."""

        class _Ctx:
            def __init__(self, conn):
                self.conn = conn
                self.outer = conn.in_transaction

            def __enter__(self):
                if not self.outer:
                    self.conn.execute("BEGIN IMMEDIATE")
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                if not self.outer:
                    if exc_type is None:
                        self.conn.execute("COMMIT")
                    else:
                        try:
                            self.conn.execute("ROLLBACK")
                        except sqlite3.Error:
                            pass
                return False
        return _Ctx(self._conn)

    def attach_pipeline(self, pipeline):
        """Attach the real ProtocolHandler (HTTP server state)."""
        self._pipeline = pipeline
        return self

    def attach_coordinator(self, coordinator):
        """Attach the RC5.3 RoundCoordinator for contribution registration."""
        self._coordinator = coordinator
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

    # -- journal state machine (R3.1: UNA sola font de veritat) -------------
    # journal_r54 queda DEPRECAT. Tots els mètodes deleguen a
    # recovery_journal_r54 (el journal autoritatiu de LeaseManager) perquè NO
    # hi hagi dues state machines paral·leles. El CREATE TABLE de journal_r54
    # ja no s'executa (schema només amb recovery_journal_r54).

    def journal_write(self, unit_id, run_id, round_id, assignment_id,
                      micro_unit_id, worker_id, session_id, lease_id,
                      lease_nonce, state, delta_sha=None, delta_b64=None,
                      update_id=None, receipt=None, checkpoint_response=None,
                      adapter_hash=None):
        """Strict journal write — DELEGA a LeaseManager.journal_set
        (recovery_journal_r54), l'únic journal autoritatiu."""
        return self._lm.journal_set(
            unit_id, run_id, round_id, assignment_id, micro_unit_id,
            worker_id, session_id, lease_id, lease_nonce, state,
            delta_sha=delta_sha, delta_b64=delta_b64, update_id=update_id,
            receipt=receipt, checkpoint_response=checkpoint_response,
            adapter_hash=adapter_hash)

    def journal_read(self, unit_id, lease_id):
        """Read — DELEGA a recovery_journal_r54 (LeaseManager.journal_get)."""
        return self._lm.journal_get(unit_id, lease_id)

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

    def _validate_lease(self, p):
        """LEASE VALIDATION ONLY (no execution). R3.1: el dispatcher valida
        la lease ABANS de consultar la cache d'idempotència; l'execució real
        passa UNA sola vegada."""
        self._require_lease_params(p)

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

    def register_uploaded_contribution(self, contribution_id, *,
                                       lease_id, lease_nonce, run_id, round_id,
                                       assignment_id, micro_unit_id, worker_id,
                                       session_id):
        """RC5.3 contribution registration is ALWAYS lease-gated.

        Full-binding check on the lease (8 dimensions); the contribution is
        bound to lease_id/revision/assignment/worker/unit. No path without a
        valid lease."""
        self._check_lease(lease_id, lease_nonce, worker_id, session_id, run_id, round_id, assignment_id, micro_unit_id)
        if getattr(self, "_coordinator", None) is None:
            raise RecoveryError("coordinator_not_attached")
        return self._coordinator.register_uploaded_contribution(contribution_id)
