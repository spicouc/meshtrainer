"""RC5.4 Stage A — SAFE RECOVERY: persistent lease table + exactly-once recovery.

Lease lifecycle over SQLite (same DB as RoundCoordinator, shared connection
discipline via BEGIN IMMEDIATE). States:

  ACTIVE   - unit leased to a worker/session; single ACTIVE lease per unit.
  RENEWED  - lease renewed by its owner (extends expires_at).
  RELEASED - owner released the unit cleanly (available for reassignment only
             after the round's boundaries allow it).
  EXPIRED  - lease TTL passed without renewal; unit becomes EXPIRED and no
             receipt/contribution from the old worker is accepted.
  ABORTED  - explicit abort (round/unit aborted); reassignable.

Rules enforced here:
  - one unit -> at most one ACTIVE lease;
  - only the owning worker+session can renew;
  - an expired lease cannot be renewed;
  - an alien lease cannot be used;
  - no contribution accepted after EXPIRED;
  - Coordinator may reassign only after EXPIRED or ABORTED;
  - clock is deterministic for tests (injectable now()).

Journal states (recovery exactly-once):
  PREPARED  - worker downloaded artifacts; retry resumes without contribution.
  APPLIED   - optimizer step applied; retry returns the exact same delta.
  SUBMITTED - contribution uploaded; retry returns the same update_id.
  COMMITTED - receipt issued; retry returns the same receipt byte-for-byte.
"""
import json, sqlite3, time, uuid, hashlib

LEASE_ACTIVE = "ACTIVE"
LEASE_RENEWED = "RENEWED"
LEASE_RELEASED = "RELEASED"
LEASE_EXPIRED = "EXPIRED"
LEASE_ABORTED = "ABORTED"

JRN_PREPARED = "PREPARED"
JRN_APPLIED = "APPLIED"
JRN_SUBMITTED = "SUBMITTED"
JRN_COMMITTED = "COMMITTED"

_LEASE_STATES = {LEASE_ACTIVE, LEASE_RENEWED, LEASE_RELEASED, LEASE_EXPIRED, LEASE_ABORTED}
_JOURNAL_STATES = {JRN_PREPARED, JRN_APPLIED, JRN_SUBMITTED, JRN_COMMITTED}


class LeaseError(Exception):
    pass


class LeaseManager:
    """Persistent leases + recovery journal on a shared SQLite connection.

    The coordinator passes its own connection so leases live in the SAME
    database/transactions as assignments/contributions (atomicity).
    """

    def __init__(self, conn, now=None, default_ttl_seconds=30.0):
        self._conn = conn
        self._now = now if now is not None else time.time
        self._default_ttl = float(default_ttl_seconds)
        self._init_db()

    # -- deterministic clock -------------------------------------------------
    def _clock(self):
        return float(self._now())

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS leases_r54 (
                lease_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                assignment_id TEXT NOT NULL,
                micro_unit_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                renewed_at REAL,
                status TEXT NOT NULL,
                lease_nonce TEXT NOT NULL,
                revision INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_leases_unit ON leases_r54 (run_id, round_id, assignment_id, micro_unit_id);
            CREATE INDEX IF NOT EXISTS idx_leases_status ON leases_r54 (status);
            CREATE TABLE IF NOT EXISTS recovery_journal_r54 (
                unit_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                assignment_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                lease_id TEXT NOT NULL,
                lease_nonce TEXT NOT NULL,
                state TEXT NOT NULL,
                delta_bundle_sha256 TEXT,
                update_id TEXT,
                receipt_json TEXT,
                checkpoint_response TEXT,
                adapter_hash TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (unit_id, lease_id)
            );
        """)
        self._conn.commit()

    # -- tx discipline --------------------------------------------------------
    def _tx(self):
        """BEGIN IMMEDIATE -> ... -> COMMIT; ROLLBACK on any error.
        Nestable: if a transaction is already open, join it (the outermost
        context owns the COMMIT/ROLLBACK)."""

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

    # -- internal helpers ------------------------------------------------------
    def _now_f(self):
        return self._clock()

    def _expire_overdue(self):
        """Transition any ACTIVE/RENEWED lease past TTL to EXPIRED (idempotent)."""
        now = self._now_f()
        with self._tx():
            rows = self._conn.execute(
                "SELECT lease_id FROM leases_r54 WHERE status IN (?,?) AND expires_at < ?",
                (LEASE_ACTIVE, LEASE_RENEWED, now)).fetchall()
            for r in rows:
                self._conn.execute("UPDATE leases_r54 SET status=? WHERE lease_id=?",
                                   (LEASE_EXPIRED, r["lease_id"]))

    def _active_lease_for_unit(self, run_id, round_id, assignment_id, micro_unit_id):
        return self._conn.execute(
            "SELECT * FROM leases_r54 WHERE run_id=? AND round_id=? AND assignment_id=?"
            " AND micro_unit_id=? AND status IN (?,?) ORDER BY issued_at DESC LIMIT 1",
            (run_id, round_id, assignment_id, micro_unit_id,
             LEASE_ACTIVE, LEASE_RENEWED)).fetchone()

    # -- public API ------------------------------------------------------------
    def acquire(self, run_id, round_id, assignment_id, micro_unit_id,
                worker_id, session_id, ttl_seconds=None, lease_nonce=None):
        """Create a lease. One ACTIVE lease per unit; a new acquire is only
        allowed when the previous lease is EXPIRED/ABORTED/RELEASED or the unit
        was never leased (reassignment path)."""
        with self._tx():
            self._expire_overdue()
            existing = self._active_lease_for_unit(
                run_id, round_id, assignment_id, micro_unit_id)
            if existing is not None:
                raise LeaseError(
                    f"Unit {micro_unit_id} already has ACTIVE lease {existing['lease_id']}")
            ttl = float(ttl_seconds if ttl_seconds is not None else self._default_ttl)
            now = self._now_f()
            lease_id = f"ls_{uuid.uuid4().hex[:16]}"
            nonce = lease_nonce if lease_nonce is not None else hashlib.sha256(
                f"{lease_id}|{now}|{uuid.uuid4().hex}".encode()).hexdigest()[:32]
            # an old non-ACTIVE lease for the same unit gets ABORTED/kept but a
            # fresh row is inserted (history preserved)
            self._conn.execute(
                "INSERT INTO leases_r54 (lease_id, run_id, round_id, assignment_id,"
                " micro_unit_id, worker_id, session_id, issued_at, expires_at,"
                " renewed_at, status, lease_nonce, revision)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lease_id, run_id, round_id, assignment_id, micro_unit_id,
                 worker_id, session_id, now, now + ttl, None,
                 LEASE_ACTIVE, nonce, 1))
        return {"lease_id": lease_id, "status": LEASE_ACTIVE,
                "issued_at": now, "expires_at": now + ttl, "lease_nonce": nonce}

    def renew(self, lease_id, worker_id, session_id, lease_nonce, ttl_seconds=None):
        """Only the owner (worker+session+nonce) may renew; expired leases cannot
        be renewed."""
        with self._tx():
            self._expire_overdue()
            row = self._conn.execute(
                "SELECT * FROM leases_r54 WHERE lease_id=?", (lease_id,)).fetchone()
            if row is None:
                raise LeaseError(f"Lease {lease_id} not found")
            if row["worker_id"] != worker_id or row["session_id"] != session_id:
                raise LeaseError("Renewal by alien worker/session rejected")
            if row["lease_nonce"] != lease_nonce:
                raise LeaseError("Stale lease nonce rejected")
            if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):
                raise LeaseError(f"Cannot renew lease in state {row['status']}")
            if row["status"] == LEASE_ACTIVE:
                status = LEASE_RENEWED
            else:
                status = LEASE_RENEWED
            now = self._now_f()
            ttl = float(ttl_seconds if ttl_seconds is not None else self._default_ttl)
            self._conn.execute(
                "UPDATE leases_r54 SET status=?, renewed_at=?, expires_at=?, revision=revision+1"
                " WHERE lease_id=?",
                (status, now, now + ttl, lease_id))
        return {"lease_id": lease_id, "status": status, "expires_at": now + ttl}

    def release(self, lease_id, worker_id, session_id, lease_nonce):
        with self._tx():
            row = self._conn.execute(
                "SELECT * FROM leases_r54 WHERE lease_id=?", (lease_id,)).fetchone()
            if row is None:
                raise LeaseError(f"Lease {lease_id} not found")
            if row["worker_id"] != worker_id or row["session_id"] != session_id:
                raise LeaseError("Release by alien worker/session rejected")
            if row["lease_nonce"] != lease_nonce:
                raise LeaseError("Stale lease nonce rejected")
            if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):
                raise LeaseError(f"Cannot release lease in state {row['status']}")
            self._conn.execute("UPDATE leases_r54 SET status=? WHERE lease_id=?",
                               (LEASE_RELEASED, lease_id))
        return {"lease_id": lease_id, "status": LEASE_RELEASED}

    def status(self, lease_id=None, unit_key=None):
        """Query one lease by id or the unit's active lease."""
        self._expire_overdue()
        if lease_id is not None:
            row = self._conn.execute(
                "SELECT * FROM leases_r54 WHERE lease_id=?", (lease_id,)).fetchone()
        elif unit_key is not None:
            row = self._active_lease_for_unit(*unit_key)
        else:
            raise LeaseError("status requires lease_id or unit_key")
        if row is None:
            return None
        return dict(row)

    def expire(self, run_id, round_id, assignment_id, micro_unit_id, worker_id=None):
        """unit.expire: force EXPIRED (used by crash mid-backward path)."""
        with self._tx():
            rows = self._conn.execute(
                "SELECT * FROM leases_r54 WHERE run_id=? AND round_id=? AND assignment_id=?"
                " AND micro_unit_id=? AND status IN (?,?)",
                (run_id, round_id, assignment_id, micro_unit_id,
                 LEASE_ACTIVE, LEASE_RENEWED)).fetchall()
            if not rows:
                raise LeaseError(f"No active lease for unit {micro_unit_id}")
            for r in rows:
                if worker_id is not None and r["worker_id"] != worker_id:
                    continue
                self._conn.execute("UPDATE leases_r54 SET status=? WHERE lease_id=?",
                                   (LEASE_EXPIRED, r["lease_id"]))
        return {"unit_id": micro_unit_id, "status": LEASE_EXPIRED}

    def abort(self, run_id, round_id, assignment_id, micro_unit_id):
        with self._tx():
            self._conn.execute(
                "UPDATE leases_r54 SET status=? WHERE run_id=? AND round_id=? AND assignment_id=?"
                " AND micro_unit_id=? AND status IN (?,?)",
                (LEASE_ABORTED, run_id, round_id, assignment_id, micro_unit_id,
                 LEASE_ACTIVE, LEASE_RENEWED))
        return {"unit_id": micro_unit_id, "status": LEASE_ABORTED}

    def can_reassign(self, run_id, round_id, assignment_id, micro_unit_id):
        """Coordinator may reassign only after EXPIRED or ABORTED (or never leased)."""
        self._expire_overdue()
        row = self._conn.execute(
            "SELECT status FROM leases_r54 WHERE run_id=? AND round_id=? AND assignment_id=?"
            " AND micro_unit_id=? ORDER BY issued_at DESC LIMIT 1",
            (run_id, round_id, assignment_id, micro_unit_id)).fetchone()
        if row is None:
            return True  # never leased
        return row["status"] in (LEASE_EXPIRED, LEASE_ABORTED)

    # -- recovery journal (exactly-once) ----------------------------------------
    def journal_set(self, unit_id, run_id, round_id, assignment_id, worker_id,
                    lease_id, lease_nonce, state, delta_sha=None, update_id=None,
                    receipt=None, checkpoint_response=None, adapter_hash=None):
        if state not in _JOURNAL_STATES:
            raise LeaseError(f"Invalid journal state {state}")
        # exactly-once guard: no contribution/receipt accepted after EXPIRED/ABORTED
        self._expire_overdue()
        lease = self._conn.execute(
            "SELECT status, worker_id, lease_nonce FROM leases_r54 WHERE lease_id=?",
            (lease_id,)).fetchone()
        if lease is None:
            raise LeaseError(f"Lease {lease_id} not found")
        if lease["worker_id"] != worker_id:
            raise LeaseError("Journal write by alien worker rejected")
        if lease["lease_nonce"] != lease_nonce:
            raise LeaseError("Stale lease nonce rejected")
        if lease["status"] in (LEASE_EXPIRED, LEASE_ABORTED):
            raise LeaseError(
                f"No contribution accepted after {lease['status']} lease")
        now = self._now_f()
        with self._tx():
            self._conn.execute(
                "INSERT INTO recovery_journal_r54 (unit_id, run_id, round_id, assignment_id,"
                " worker_id, lease_id, lease_nonce, state, delta_bundle_sha256, update_id,"
                " receipt_json, checkpoint_response, adapter_hash, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(unit_id, lease_id) DO UPDATE SET state=excluded.state,"
                " delta_bundle_sha256=CASE WHEN recovery_journal_r54.delta_bundle_sha256 IS NULL THEN excluded.delta_bundle_sha256 ELSE recovery_journal_r54.delta_bundle_sha256 END,"
                " update_id=CASE WHEN recovery_journal_r54.update_id IS NULL THEN excluded.update_id ELSE recovery_journal_r54.update_id END,"
                " receipt_json=CASE WHEN recovery_journal_r54.receipt_json IS NULL THEN excluded.receipt_json ELSE recovery_journal_r54.receipt_json END,"
                " checkpoint_response=CASE WHEN recovery_journal_r54.checkpoint_response IS NULL THEN excluded.checkpoint_response ELSE recovery_journal_r54.checkpoint_response END,"
                " adapter_hash=CASE WHEN recovery_journal_r54.adapter_hash IS NULL THEN excluded.adapter_hash ELSE recovery_journal_r54.adapter_hash END,"
                " updated_at=excluded.updated_at",
                (unit_id, run_id, round_id, assignment_id, worker_id, lease_id,
                 lease_nonce, state, delta_sha, update_id,
                 receipt, checkpoint_response, adapter_hash, now))
        return {"unit_id": unit_id, "state": state, "lease_id": lease_id}

    def journal_get(self, unit_id, lease_id):
        row = self._conn.execute(
            "SELECT * FROM recovery_journal_r54 WHERE unit_id=? AND lease_id=?",
            (unit_id, lease_id)).fetchone()
        return dict(row) if row else None

    def journal_state(self, unit_id, lease_id):
        row = self.journal_get(unit_id, lease_id)
        return row["state"] if row else None

    def journal_lease_for_unit(self, run_id, round_id, assignment_id, micro_unit_id,
                               worker_id=None):
        """Most recent journal row for the unit (used on retry to find the lease)."""
        q = ("SELECT * FROM recovery_journal_r54 WHERE run_id=? AND round_id=?"
             " AND assignment_id=? AND unit_id=?")
        args = [run_id, round_id, assignment_id, micro_unit_id]
        if worker_id is not None:
            q += " AND worker_id=?"
            args.append(worker_id)
        q += " ORDER BY updated_at DESC LIMIT 1"
        row = self._conn.execute(q, args).fetchone()
        return dict(row) if row else None
