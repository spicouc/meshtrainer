"""RC5.1 — SQLite persistence for RC5 entities."""
import sqlite3
import json
import time
import os
import threading

DB_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS rc5_worker_session (
    worker_id TEXT PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    auth_token TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'REGISTERED',
    joined_at REAL NOT NULL,
    protocol_version TEXT,
    capabilities TEXT
);

CREATE TABLE IF NOT EXISTS rc5_assignment (
    assignment_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    round_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    micro_unit_ids TEXT NOT NULL DEFAULT '[]',
    base_adapter_hash TEXT,
    model_hash TEXT,
    adapter_schema_hash TEXT,
    loss_definition_hash TEXT,
    precision_profile TEXT,
    lease_token TEXT,
    issued_at REAL,
    expires_at REAL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    FOREIGN KEY (worker_id) REFERENCES rc5_worker_session(worker_id)
);

CREATE TABLE IF NOT EXISTS rc5_micro_unit (
    micro_unit_id TEXT,
    assignment_id TEXT,
    worker_id TEXT,
    status TEXT NOT NULL,
    state_machine TEXT,
    created_at REAL,
    PRIMARY KEY (micro_unit_id, assignment_id)
);

CREATE TABLE IF NOT EXISTS rc5_receipt (
    receipt_nonce TEXT PRIMARY KEY,
    receipt_json TEXT NOT NULL,
    signature TEXT NOT NULL,
    received_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rc5_contribution (
    contribution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    round_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'RECEIVED',
    supersedes_contribution_id TEXT,
    receipt_nonce TEXT,
    delta_sha256 TEXT,
    delta_path TEXT,
    effective_trainable_tokens INTEGER DEFAULT 0,
    submitted_at REAL NOT NULL,
    UNIQUE(run_id, round_id, assignment_id, worker_id, revision)
);

CREATE TABLE IF NOT EXISTS rc5_nonce (
    nonce TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rc5_checkpoint (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    round_id TEXT NOT NULL,
    global_delta_sha256 TEXT,
    global_adapter_sha256 TEXT,
    manifest_json TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contribution_key ON rc5_contribution(run_id, round_id, assignment_id, worker_id);
CREATE INDEX IF NOT EXISTS idx_contribution_status ON rc5_contribution(status);
CREATE INDEX IF NOT EXISTS idx_assignment_worker ON rc5_assignment(worker_id, status);
CREATE INDEX IF NOT EXISTS idx_nonce_lookup ON rc5_nonce(nonce);
"""

class Rc5Db:
    def __init__(self, db_path):
        self.db_path = db_path
        with DB_LOCK:
            self.conn = sqlite3.connect(db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- worker_session ----
    def upsert_worker(self, worker_id, session_id, auth_token="", capabilities=None):
        with DB_LOCK:
            self.conn.execute("""INSERT OR REPLACE INTO rc5_worker_session
                (worker_id, session_id, auth_token, status, joined_at, capabilities)
                VALUES (?, ?, ?, 'REGISTERED', ?, ?)""",
                (worker_id, session_id, auth_token, time.time(),
                 json.dumps(capabilities or {})))
            self.conn.commit()

    def get_worker(self, worker_id):
        with DB_LOCK:
            c = self.conn.execute("SELECT * FROM rc5_worker_session WHERE worker_id=?", (worker_id,))
            return c.fetchone()

    # ---- nonce ----
    def nonce_exists(self, nonce):
        with DB_LOCK:
            c = self.conn.execute("SELECT 1 FROM rc5_nonce WHERE nonce=?", (nonce,))
            return c.fetchone() is not None

    def add_nonce(self, nonce):
        with DB_LOCK:
            self.conn.execute("INSERT OR IGNORE INTO rc5_nonce (nonce, created_at) VALUES (?, ?)",
                           (nonce, time.time()))
            self.conn.commit()

    # ---- assignment ----
    def create_assignment(self, assignment_id, run_id, round_id, worker_id, session_id,
                          unit_ids, lease_token=""):
        with DB_LOCK:
            self.conn.execute("""INSERT INTO rc5_assignment
                (assignment_id, run_id, round_id, worker_id, session_id,
                 micro_unit_ids, lease_token, issued_at, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')""",
                (assignment_id, run_id, round_id, worker_id, session_id,
                 json.dumps(unit_ids), lease_token, time.time(), time.time() + 3600))
            self.conn.commit()

    def get_assignment(self, assignment_id):
        with DB_LOCK:
            c = self.conn.execute("SELECT * FROM rc5_assignment WHERE assignment_id=?", (assignment_id,))
            return c.fetchone()

    def set_assignment_status(self, assignment_id, status):
        with DB_LOCK:
            self.conn.execute("UPDATE rc5_assignment SET status=? WHERE assignment_id=?",
                           (status, assignment_id))
            self.conn.commit()

    # ---- contribution ----
    def add_contribution(self, contrib):
        with DB_LOCK:
            self.conn.execute("""INSERT INTO rc5_contribution
                (contribution_id, run_id, round_id, assignment_id, worker_id,
                 revision, status, supersedes_contribution_id,
                 receipt_nonce, delta_sha256, delta_path,
                 effective_trainable_tokens, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (contrib["contribution_id"], contrib["run_id"], contrib["round_id"],
                 contrib["assignment_id"], contrib["worker_id"],
                 contrib["revision"], contrib["status"],
                 contrib.get("supersedes_contribution_id"),
                 contrib.get("receipt_nonce"), contrib.get("delta_sha256"),
                 contrib.get("delta_path", ""),
                 contrib.get("effective_trainable_tokens", 0), contrib["submitted_at"]))
            self.conn.commit()

    def get_contributions(self, run_id=None, round_id=None):
        with DB_LOCK:
            q = "SELECT * FROM rc5_contribution"
            params = []
            if run_id:
                q += " WHERE run_id=?"
                params.append(run_id)
                if round_id:
                    q += " AND round_id=?"
                    params.append(round_id)
            q += " ORDER BY submitted_at"
            return [dict(r) for r in self.conn.execute(q, params)]

    def update_contribution_status(self, contribution_id, status):
        with DB_LOCK:
            self.conn.execute("UPDATE rc5_contribution SET status=? WHERE contribution_id=?",
                           (status, contribution_id))
            self.conn.commit()

    def get_active_contribution(self, run_id, round_id, assignment_id, worker_id):
        with DB_LOCK:
            c = self.conn.execute(
                "SELECT * FROM rc5_contribution WHERE run_id=? AND round_id=? AND assignment_id=? AND worker_id=? AND status='ACTIVE'",
                (run_id, round_id, assignment_id, worker_id))
            return c.fetchone()

    def has_active_contribution(self, run_id, round_id, assignment_id, worker_id):
        return self.get_active_contribution(run_id, round_id, assignment_id, worker_id) is not None

    # ---- checkpoint ----
    def save_checkpoint(self, checkpoint_id, run_id, round_id, delta_sha256, adapter_sha256, manifest):
        with DB_LOCK:
            self.conn.execute("""INSERT INTO rc5_checkpoint
                (checkpoint_id, run_id, round_id, global_delta_sha256, global_adapter_sha256, manifest_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (checkpoint_id, run_id, round_id, delta_sha256, adapter_sha256,
                 json.dumps(manifest, default=str), time.time()))
            self.conn.commit()
