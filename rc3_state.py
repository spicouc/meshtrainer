"""
rc3_state.py — RC3.1 State machine + SQLite schema.

Màquina d'estats de tasques (agnòstica d'estratègia d'entrenament).

Estats d'una tasca:
  PENDING    → creada, espera assignació
  ASSIGNED   → assignada a un worker (lease actiu)
  COMPLETED  → resultat acceptat
  FAILED     → resultat rebutjat o error irrecuperable
  TIMEOUT    → lease expirat sense resultat
  CANCELLED  → cancel·lada pel coordinador
  DUPLICATE  → resultat duplicat rebutjat

Transicions:
  PENDING  ──(assign)──▶ ASSIGNED
  ASSIGNED ──(submit)──▶ COMPLETED
  ASSIGNED ──(reject)──▶ FAILED
  ASSIGNED ──(timeout)──▶ TIMEOUT  → PENDING (reassignable)
  ASSIGNED ──(cancel)──▶ CANCELLED
  PENDING  ──(cancel)──▶ CANCELLED
  TIMEOUT  ──(reassign)──▶ PENDING
  FAILED   ──(retry)──▶ PENDING
  COMPLETED ──(duplicate_submit)──▶ DUPLICATE
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple


# ── Constants ──────────────────────────────────────────────────────────

RC3_PROTOCOL_VERSION = "rc3-protocol-v1"
LEASE_DEFAULT_SECONDS = 300
LEASE_EXTENSION_MAX = 600  # 2x default
HEARTBEAT_INTERVAL = 30
HEARTBEAT_TIMEOUT = 90  # 3 missed heartbeats


# ── Task state machine ─────────────────────────────────────────────────

class TaskState(str, Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    DUPLICATE = "DUPLICATE"

    @classmethod
    def valid_transitions(cls) -> Dict[str, List[str]]:
        return {
            "PENDING": ["ASSIGNED", "CANCELLED"],
            "ASSIGNED": ["COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"],
            "TIMEOUT": ["PENDING"],  # reassign
            "FAILED": ["PENDING"],   # retry
            "COMPLETED": ["DUPLICATE"],
            "CANCELLED": [],
            "DUPLICATE": [],
        }

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        return state in ("COMPLETED", "CANCELLED", "DUPLICATE")

    @classmethod
    def validate_transition(cls, from_state: str, to_state: str) -> bool:
        return to_state in cls.valid_transitions().get(from_state, [])


# ── SQLite schema ─────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS project (
    project_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    config_hash  TEXT,
    status       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS worker (
    worker_id    TEXT PRIMARY KEY,
    auth_token   TEXT NOT NULL,
    worker_type  TEXT NOT NULL,  -- 'python', 'webgpu', 'wasm'
    capabilities TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'unknown',  -- unknown, idle, busy, offline
    last_heartbeat_at TEXT,
    registered_at    TEXT NOT NULL DEFAULT (datetime('now')),
    protocol_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task (
    task_id         TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    project_id      TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'PENDING',
    strategy        TEXT,  -- 'delta_lora', 'gradient', 'simulated'
    config_hash     TEXT,
    input_hash      TEXT,
    output_hash     TEXT,
    assigned_worker_id TEXT,
    assignment_count INTEGER NOT NULL DEFAULT 0,
    lease_expires_at    TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at        TEXT,
    result_status       TEXT,  -- 'accepted', 'rejected', 'duplicate'
    result_detail       TEXT,
    execution_metadata  TEXT DEFAULT '{}',
    task_params         TEXT DEFAULT '{}',
    CONSTRAINT fk_project FOREIGN KEY (project_id) REFERENCES project(project_id),
    CONSTRAINT fk_worker  FOREIGN KEY (assigned_worker_id) REFERENCES worker(worker_id)
);

CREATE INDEX IF NOT EXISTS idx_task_state ON task(state);
CREATE INDEX IF NOT EXISTS idx_task_worker ON task(assigned_worker_id);
CREATE INDEX IF NOT EXISTS idx_task_run    ON task(run_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    event_type  TEXT NOT NULL,
    task_id     TEXT,
    worker_id   TEXT,
    run_id      TEXT,
    payload     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_task   ON audit_log(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_log(timestamp);

-- WAL mode for concurrent access
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
"""


# ── State Store ────────────────────────────────────────────────────────

class StateStore:
    """SQLite-backed persistent state for RC3 coordinator.

    Thread-safe (SQLite WAL mode + per-connection thread check).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # ── Project ────────────────────────────────────────────────────

    def create_project(self, name: str, config_hash: Optional[str] = None) -> str:
        project_id = f"p-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO project (project_id, name, config_hash) VALUES (?, ?, ?)",
                (project_id, name, config_hash),
            )
            self._conn.commit()
        return project_id

    def get_project(self, project_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM project WHERE project_id = ?", (project_id,)
            ).fetchone()
        return dict(row) if row else None

    # ── Worker ─────────────────────────────────────────────────────

    def register_worker(self, worker_type: str, capabilities: dict,
                        protocol_version: str) -> Tuple[str, str]:
        worker_id = f"w-{uuid.uuid4().hex[:8]}"
        auth_token = f"tok_{uuid.uuid4().hex}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO worker (worker_id, auth_token, worker_type, "
                "capabilities, status, protocol_version) VALUES (?, ?, ?, ?, 'idle', ?)",
                (worker_id, auth_token, worker_type,
                 json.dumps(capabilities), protocol_version),
            )
            self._conn.commit()
            self._audit("worker_registered", worker_id=worker_id,
                        payload={"worker_type": worker_type})
        return worker_id, auth_token

    def get_worker(self, worker_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM worker WHERE worker_id = ?", (worker_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_worker_heartbeat(self, worker_id: str, status: str,
                                progress_pct: Optional[int] = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE worker SET status=?, last_heartbeat_at=? WHERE worker_id=?",
                (status, now, worker_id),
            )
            self._conn.commit()

    def get_idle_workers(self) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM worker WHERE status IN ('idle', 'unknown')"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_workers_offline(self, timeout_seconds: int = HEARTBEAT_TIMEOUT):
        """Mark workers as offline if they haven't sent heartbeat in timeout_seconds."""
        cutoff = datetime.now(timezone.utc).timestamp() - timeout_seconds
        with self._lock:
            self._conn.execute(
                "UPDATE worker SET status='offline' WHERE "
                "last_heartbeat_at IS NOT NULL AND "
                "strftime('%s', last_heartbeat_at) < ?",
                (int(cutoff),),
            )
            self._conn.commit()

    # ── Task ───────────────────────────────────────────────────────

    def create_task(self, run_id: str, project_id: str,
                    strategy: str = "simulated",
                    config_hash: Optional[str] = None,
                    input_hash: Optional[str] = None,
                    task_params: Optional[dict] = None) -> str:
        task_id = f"t-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO task (task_id, run_id, project_id, strategy, "
                "config_hash, input_hash, task_params, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')",
                (task_id, run_id, project_id, strategy,
                 config_hash, input_hash,
                 json.dumps(task_params or {})),
            )
            self._conn.commit()
            self._audit("task_created", task_id=task_id, run_id=run_id,
                        payload={"strategy": strategy, "task_params": task_params or {}})
        return task_id

    def assign_task(self, task_id: str, worker_id: str,
                    lease_seconds: int = LEASE_DEFAULT_SECONDS) -> bool:
        """Assign task to worker. Returns False if task is not PENDING."""
        now = datetime.now(timezone.utc).isoformat()
        lease_expires = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + lease_seconds,
            tz=timezone.utc,
        ).isoformat()

        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM task WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row or row["state"] != "PENDING":
                return False

            self._conn.execute(
                "UPDATE task SET state='ASSIGNED', assigned_worker_id=?, "
                "lease_expires_at=?, assignment_count=assignment_count+1 "
                "WHERE task_id=?",
                (worker_id, lease_expires, task_id),
            )
            self._conn.commit()
            self._audit("task_assigned", task_id=task_id, worker_id=worker_id,
                        payload={"lease_until": lease_expires})
        return True

    def submit_result(self, task_id: str, worker_id: str,
                      status: str, output_hash: Optional[str] = None,
                      execution_metadata: Optional[dict] = None,
                      result_detail: Optional[str] = None) -> Dict:
        """Submit a task result. Returns {'accepted': bool, 'reason': str}."""
        with self._lock:
            row = self._conn.execute(
                "SELECT state, assigned_worker_id, input_hash, output_hash, "
                "lease_expires_at FROM task WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                return {"accepted": False, "reason": "task_not_found"}

            # Duplicate detection
            if row["state"] == "COMPLETED":
                if row["output_hash"] and output_hash and row["output_hash"] == output_hash:
                    # Same result as before
                    self._audit("result_duplicate", task_id=task_id, worker_id=worker_id,
                                payload={"status": "duplicate"})
                    self._conn.execute(
                        "UPDATE task SET result_status='duplicate' WHERE task_id=?",
                        (task_id,),
                    )
                    self._conn.commit()
                    return {"accepted": False, "reason": "duplicate"}

            if row["state"] != "ASSIGNED":
                return {"accepted": False,
                        "reason": f"task_not_assigned (state={row['state']})"}

            if row["assigned_worker_id"] != worker_id:
                return {"accepted": False,
                        "reason": "worker_not_assigned_to_this_task"}

            # Check lease
            if row["lease_expires_at"]:
                lease_exp = datetime.fromisoformat(row["lease_expires_at"])
                if datetime.now(timezone.utc) > lease_exp:
                    self._audit("result_late", task_id=task_id, worker_id=worker_id)
                    return {"accepted": False, "reason": "lease_expired"}

            now = datetime.now(timezone.utc).isoformat()
            meta_json = json.dumps(execution_metadata or {})
            self._conn.execute(
                "UPDATE task SET state='COMPLETED', completed_at=?, "
                "output_hash=?, result_status='accepted', "
                "execution_metadata=?, result_detail=? WHERE task_id=?",
                (now, output_hash, meta_json, result_detail, task_id),
            )
            self._conn.commit()
            self._audit("result_accepted", task_id=task_id, worker_id=worker_id,
                        payload={"output_hash": output_hash,
                                 "execution_metadata": execution_metadata})
        return {"accepted": True, "reason": "ok"}

    def reject_result(self, task_id: str, worker_id: str,
                      reason: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM task WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row or row["state"] != "ASSIGNED":
                return False
            self._conn.execute(
                "UPDATE task SET state='FAILED', result_status='rejected', "
                "result_detail=? WHERE task_id=?", (reason, task_id),
            )
            self._conn.commit()
            self._audit("result_rejected", task_id=task_id, worker_id=worker_id,
                        payload={"reason": reason})
        return True

    def timeout_tasks(self) -> List[Dict]:
        """Mark expired leases as TIMEOUT. Returns list of timed-out tasks."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id, assigned_worker_id FROM task WHERE "
                "state='ASSIGNED' AND lease_expires_at IS NOT NULL AND "
                "lease_expires_at < ?",
                (now,),
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE task SET state='TIMEOUT' WHERE task_id=?",
                    (row["task_id"],),
                )
                self._audit("task_timeout", task_id=row["task_id"],
                            worker_id=row["assigned_worker_id"])
            self._conn.commit()
        return [dict(r) for r in rows]

    def reassign_task(self, task_id: str) -> bool:
        """Move TIMEOUT or FAILED task back to PENDING for reassignment."""
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM task WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row or row["state"] not in ("TIMEOUT", "FAILED"):
                return False
            self._conn.execute(
                "UPDATE task SET state='PENDING', assigned_worker_id=NULL, "
                "lease_expires_at=NULL WHERE task_id=?",
                (task_id,),
            )
            self._conn.commit()
            self._audit("task_reassigned", task_id=task_id)
        return True

    def get_pending_tasks(self, strategy: Optional[str] = None) -> List[Dict]:
        with self._lock:
            if strategy:
                rows = self._conn.execute(
                    "SELECT * FROM task WHERE state='PENDING' AND strategy=? "
                    "ORDER BY created_at ASC LIMIT 10",
                    (strategy,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM task WHERE state='PENDING' "
                    "ORDER BY created_at ASC LIMIT 10",
                ).fetchall()
        return [dict(r) for r in rows]

    def get_task(self, task_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task WHERE task_id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_tasks_by_run(self, run_id: str) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task WHERE run_id=? ORDER BY created_at", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tasks_by_worker(self, worker_id: str) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task WHERE assigned_worker_id=? "
                "ORDER BY created_at DESC", (worker_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Audit ──────────────────────────────────────────────────────

    def _audit(self, event_type: str, task_id: Optional[str] = None,
               worker_id: Optional[str] = None, run_id: Optional[str] = None,
               payload: Optional[dict] = None):
        self._conn.execute(
            "INSERT INTO audit_log (event_type, task_id, worker_id, run_id, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_type, task_id, worker_id, run_id, json.dumps(payload or {})),
        )
        self._conn.commit()

    def get_audit_log(self, limit: int = 100, task_id: Optional[str] = None) -> List[Dict]:
        with self._lock:
            if task_id:
                rows = self._conn.execute(
                    "SELECT * FROM audit_log WHERE task_id=? "
                    "ORDER BY id DESC LIMIT ?", (task_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ── Recovery ───────────────────────────────────────────────────

    def recover_after_restart(self):
        """After coordinator restart, reassign all ASSIGNED tasks (leases expired)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id FROM task WHERE state='ASSIGNED'"
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE task SET state='TIMEOUT' WHERE task_id=?",
                    (row["task_id"],),
                )
                self._audit("task_recovered_timeout", task_id=row["task_id"],
                            payload={"reason": "coordinator_restart"})
            # Mark all workers as unknown (they must re-register)
            self._conn.execute("UPDATE worker SET status='unknown'")
            self._conn.commit()


# ── Simulated deterministic operation ──────────────────────────────────

def simulated_compute(task_input: str, config_seed: int = 42) -> str:
    """Deterministic operation simulating a worker's computation.

    Usa serialització canònica JSON (claus ordenades, compacte, UTF-8)
    per garantir que Python i JS produeixin el MATEIX hash.

    task_input + seed → determinstic output hash.
    Used for RC3.1 protocol validation (no real training).
    """
    import hashlib, json

    # Serialització canònica: claus ordenades, sense espais, UTF-8
    canonical = json.dumps(
        {
            "input": task_input,
            "protocol_version": "rc3-protocol-v1",
            "seed": config_seed,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    h = hashlib.sha256()
    h.update(canonical.encode("utf-8"))
    return h.hexdigest()
