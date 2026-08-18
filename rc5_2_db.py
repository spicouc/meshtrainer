"""RC5.2 Phase 2 W4: SQLite persistence."""
import sqlite3, json, threading
from rc5_2_state import UnitState

_local = threading.local()

def _get_conn(path=":memory:"):
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(path)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

def init_db(path=":memory:"):
    conn = _get_conn(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS units (
            unit_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            run_id TEXT, round_id TEXT, assignment_id TEXT, micro_unit_id TEXT,
            worker_id TEXT, session_id TEXT,
            worker_model_hash TEXT, partition_schema_hash TEXT,
            base_adapter_hash TEXT, adapter_schema_hash TEXT,
            numerical_profile_hash TEXT,
            server_activation_b64 TEXT,
            cut_activation_sha256 TEXT,
            cut_gradient_b64 TEXT,
            backward_id TEXT,
            loss REAL, ett INTEGER,
            forward_id TEXT, forward_sha256 TEXT,
            update_id TEXT, update_sha256 TEXT,
            delta_bundle_b64 TEXT, delta_bundle_sha256 TEXT,
            delta_id TEXT, delta_bundle_byte_length INTEGER,
            receipt_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS nonces (
            nonce_id TEXT PRIMARY KEY,
            unit_id TEXT,
            consumed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS contributions (
            contribution_id TEXT PRIMARY KEY,
            unit_id TEXT, run_id TEXT, round_id TEXT,
            worker_id TEXT, delta_bundle_b64 TEXT,
            delta_bundle_sha256 TEXT, receipt_json TEXT,
            status TEXT DEFAULT 'RECEIVED',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

def get_unit_state(unit_id, path=":memory:"):
    conn = _get_conn(path)
    r = conn.execute("SELECT state FROM units WHERE unit_id=?", (unit_id,)).fetchone()
    return UnitState(r[0]) if r else None

def set_unit_state(unit_id, state, path=":memory:"):
    conn = _get_conn(path)
    conn.execute("UPDATE units SET state=?, updated_at=datetime('now') WHERE unit_id=?", (state.value, unit_id))
    conn.commit()

def save_unit(**kw):
    conn = _get_conn(kw.get("db_path", ":memory:"))
    cols = ", ".join(k for k in kw if k != "db_path")
    phs = ", ".join("?" for _ in kw if _ != "db_path")
    vals = [kw[k] for k in kw if k != "db_path"]
    conn.execute(f"INSERT OR REPLACE INTO units ({cols}) VALUES ({phs})", vals)
    conn.commit()

def get_unit(unit_id, path=":memory:"):
    conn = _get_conn(path)
    r = conn.execute("SELECT * FROM units WHERE unit_id=?", (unit_id,)).fetchone()
    return dict(r) if r else None

def check_nonce(nonce_id, path=":memory:"):
    conn = _get_conn(path)
    return conn.execute("SELECT consumed FROM nonces WHERE nonce_id=?", (nonce_id,)).fetchone() is None

def consume_nonce(nonce_id, unit_id, path=":memory:"):
    conn = _get_conn(path)
    conn.execute("INSERT OR IGNORE INTO nonces (nonce_id, unit_id, consumed) VALUES (?, ?, 1)", (nonce_id, unit_id))
    conn.commit()

def save_contribution(**kw):
    conn = _get_conn(kw.get("db_path", ":memory:"))
    cols = ", ".join(k for k in kw if k != "db_path")
    phs = ", ".join("?" for _ in kw if _ != "db_path")
    vals = [kw[k] for k in kw if k != "db_path"]
    conn.execute(f"INSERT OR REPLACE INTO contributions ({cols}) VALUES ({phs})", vals)
    conn.commit()

def get_contribution(cid, path=":memory:"):
    conn = _get_conn(path)
    r = conn.execute("SELECT * FROM contributions WHERE contribution_id=?", (cid,)).fetchone()
    return dict(r) if r else None
