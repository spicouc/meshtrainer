"""Persistència de l'app: meshtrainer_app.db (SQLite independent del core).

Taules: jobs, datasets, worker_definitions, job_events, job_artifacts,
job_metrics. La UI/API no toca mai les taules internes del Coordinator.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from typing import Any, Optional


def _gen(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    revision TEXT DEFAULT '',
    local_path TEXT,
    base_model_hash TEXT DEFAULT '',
    dataset_id TEXT DEFAULT '',
    method TEXT DEFAULT 'lora',
    lora_rank INTEGER DEFAULT 8,
    lora_alpha INTEGER DEFAULT 16,
    lora_dropout REAL DEFAULT 0.05,
    learning_rate REAL DEFAULT 1e-4,
    rounds INTEGER DEFAULT 1,
    max_seq_len INTEGER DEFAULT 64,
    seed INTEGER DEFAULT 42,
    workers INTEGER DEFAULT 2,
    concurrency INTEGER DEFAULT 1,
    device TEXT DEFAULT 'cpu',
    memory_mb INTEGER,
    output_dir TEXT DEFAULT '',
    status TEXT DEFAULT 'DRAFT',
    last_error TEXT DEFAULT '',
    validation TEXT DEFAULT 'PENDING',
    validation_errors TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    format TEXT DEFAULT 'jsonl',
    size INTEGER DEFAULT 0,
    examples INTEGER DEFAULT 0,
    train_count INTEGER DEFAULT 0,
    validation_count INTEGER DEFAULT 0,
    test_count INTEGER DEFAULT 0,
    schema_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    validation_status TEXT DEFAULT 'PENDING',
    validation_errors TEXT DEFAULT '[]',
    scope TEXT DEFAULT 'generic',
    backend_id TEXT,
    model_id TEXT
);
CREATE TABLE IF NOT EXISTS worker_definitions (
    worker_id TEXT PRIMARY KEY,
    host TEXT DEFAULT '127.0.0.1',
    port INTEGER DEFAULT 0,
    backend_capabilities TEXT DEFAULT '[]',
    device TEXT DEFAULT 'cpu',
    memory_mb INTEGER,
    enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS job_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS job_artifacts (
    artifact_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    type TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER DEFAULT 0,
    round TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_metrics (
    metric_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    round TEXT DEFAULT '',
    worker_id TEXT DEFAULT '',
    ts TEXT NOT NULL,
    loss REAL,
    val_loss REAL,
    ppl REAL,
    ett INTEGER,
    duration_s REAL
);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id, ts);
CREATE INDEX IF NOT EXISTS idx_metrics_job ON job_metrics(job_id, round);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON job_artifacts(job_id);
"""

# Columnes de reconciliation (E0-03) afegides a jobs si no existeixen.
RECONCILE_COLUMNS = {
    "runner_pid": "INTEGER",
    "run_id": "TEXT DEFAULT ''",
    "runner_started_at": "TEXT DEFAULT ''",
    "runner_identity": "TEXT DEFAULT ''",
}

# v1.4.1 (P1 dataset compatibility): migració segura per datasets v1.4.0
# existents — scope default 'generic', backend_id/model_id NULL.
DATASET_COLUMNS = {
    "scope": "TEXT DEFAULT 'generic'",
    "backend_id": "TEXT",
    "model_id": "TEXT",
}


class AppDB:
    """Accés SQLite de l'app. Una connexió per thread (check_same_thread=False)
    amb WAL per tolerar lectors concurrents (API + JobRunner)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
        self._lock = __import__("threading").RLock()
        self._init()

    def _init(self):
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            # migració E0-03: afegeix columnes de reconciliation si no hi són
            cols = {r["name"] for r in self._conn.execute(
                "PRAGMA table_info(jobs)").fetchall()}
            for name, decl in RECONCILE_COLUMNS.items():
                if name not in cols:
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")
            # v1.4.1: migració datasets (scope/backend_id/model_id)
            dcols = {r["name"] for r in self._conn.execute(
                "PRAGMA table_info(datasets)").fetchall()}
            for name, decl in DATASET_COLUMNS.items():
                if name not in dcols:
                    self._conn.execute(
                        f"ALTER TABLE datasets ADD COLUMN {name} {decl}")
            self._conn.commit()

    # ── helpers ──────────────────────────────────────────────────────────
    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        # lock OBLIGATORI: la mateixa connexió és compartida entre threads
        # (SSE long-poll + REST + JobRunner procés) — sense lock, sqlite3
        # llança "bad parameter or other API misuse" en accés concurrent.
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            r = cur.fetchone()
            return dict(r) if r else None

    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    # ── jobs ─────────────────────────────────────────────────────────────
    def insert_job(self, job: dict) -> None:
        self._exec(
            "INSERT INTO jobs (job_id,name,backend_id,model_id,revision,local_path,"
            "base_model_hash,dataset_id,method,lora_rank,lora_alpha,lora_dropout,"
            "learning_rate,rounds,max_seq_len,seed,workers,concurrency,device,"
            "memory_mb,output_dir,status,last_error,validation,validation_errors,"
            "created_at,started_at,finished_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job["job_id"], job["name"], job["backend_id"], job["model_id"],
             job.get("revision", ""), job.get("local_path"),
             job.get("base_model_hash", ""), job.get("dataset_id", ""),
             job.get("method", "lora"), job.get("lora_rank", 8),
             job.get("lora_alpha", 16), job.get("lora_dropout", 0.05),
             job.get("learning_rate", 1e-4), job.get("rounds", 1),
             job.get("max_seq_len", 64), job.get("seed", 42),
             job.get("workers", 2), job.get("concurrency", 1),
             job.get("device", "cpu"), job.get("memory_mb"),
             job.get("output_dir", ""), job.get("status", "DRAFT"),
             job.get("last_error", ""), job.get("validation", "PENDING"),
             json.dumps(job.get("validation_errors", [])),
             job.get("created_at"), job.get("started_at", ""),
             job.get("finished_at", "")))

    def update_job(self, job_id: str, **fields) -> None:
        sets, params = [], []
        for k, v in fields.items():
            if k == "validation_errors":
                v = json.dumps(v)
            sets.append(f"{k}=?")
            params.append(v)
        params.append(job_id)
        self._exec(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id=?", tuple(params))

    def get_job(self, job_id: str) -> Optional[dict]:
        j = self._one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if j:
            j["validation_errors"] = json.loads(j.get("validation_errors") or "[]")
        return j

    def list_jobs(self, status: Optional[str] = None) -> list[dict]:
        if status:
            return self._rows("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC", (status,))
        return self._rows("SELECT * FROM jobs ORDER BY created_at DESC")

    # ── datasets ─────────────────────────────────────────────────────────
    def insert_dataset(self, d: dict) -> None:
        self._exec(
            "INSERT INTO datasets (dataset_id,name,source_path,format,size,examples,"
            "train_count,validation_count,test_count,schema_json,created_at,"
            "validation_status,validation_errors,scope,backend_id,model_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["dataset_id"], d["name"], d["source_path"], d.get("format", "jsonl"),
             d.get("size", 0), d.get("examples", 0), d.get("train_count", 0),
             d.get("validation_count", 0), d.get("test_count", 0),
             json.dumps(d.get("schema_json", {})), d.get("created_at"),
             d.get("validation_status", "PENDING"),
             json.dumps(d.get("validation_errors", [])),
             d.get("scope", "generic"),
             d.get("backend_id"), d.get("model_id")))

    def update_dataset(self, dataset_id: str, **fields) -> None:
        sets, params = [], []
        for k, v in fields.items():
            if k == "validation_errors":
                v = json.dumps(v)
            sets.append(f"{k}=?")
            params.append(v)
        params.append(dataset_id)
        self._exec(f"UPDATE datasets SET {', '.join(sets)} WHERE dataset_id=?", tuple(params))

    def get_dataset(self, dataset_id: str) -> Optional[dict]:
        d = self._one("SELECT * FROM datasets WHERE dataset_id=?", (dataset_id,))
        if d:
            d["schema_json"] = json.loads(d.get("schema_json") or "{}")
            d["validation_errors"] = json.loads(d.get("validation_errors") or "[]")
        return d

    def list_datasets(self) -> list[dict]:
        return self._rows("SELECT * FROM datasets ORDER BY created_at DESC")

    # ── events ───────────────────────────────────────────────────────────
    def add_event(self, job_id: str, type_: str, payload: Optional[dict] = None) -> tuple[str, int]:
        eid = _gen("ev")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO job_events (event_id,job_id,ts,type,payload) "
                "VALUES (?,?,?,?,?)",
                (eid, job_id, _now_iso(), type_, json.dumps(payload or {})))
            # E0-04 (R1): cursor ESTABLE = rowid de SQLite (monotònic,
            # no reiniciat per connexió, no depèn del timestamp).
            seq = cur.lastrowid or 0
        return eid, seq

    def list_events(self, job_id: str, after_seq: int = 0) -> list[dict]:
        """Events amb seq (rowid) > after_seq, ordenats per seq (monotònic).
        E0-04 (R1): el cursor és el rowid — mai el timestamp en segons
        (evita pèrdua d'events creats al mateix segon)."""
        return self._rows(
            "SELECT rowid AS seq, event_id, job_id, ts, type, payload "
            "FROM job_events WHERE job_id=? AND rowid>? ORDER BY rowid",
            (job_id, int(after_seq)))

    # ── artifacts ────────────────────────────────────────────────────────
    def add_artifact(self, job_id: str, type_: str, path: str, sha256: str,
                     size: int, round_: str = "") -> str:
        aid = _gen("art")
        self._exec("INSERT INTO job_artifacts (artifact_id,job_id,type,path,sha256,"
                   "size,round,created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (aid, job_id, type_, path, sha256, size, round_, _now_iso()))
        return aid

    def list_artifacts(self, job_id: str) -> list[dict]:
        return self._rows("SELECT * FROM job_artifacts WHERE job_id=? ORDER BY round, created_at",
                          (job_id,))

    # ── metrics ──────────────────────────────────────────────────────────
    def add_metric(self, job_id: str, round_: str = "", worker_id: str = "",
                   loss: Optional[float] = None, val_loss: Optional[float] = None,
                   ppl: Optional[float] = None, ett: Optional[int] = None,
                   duration_s: Optional[float] = None) -> str:
        mid = _gen("met")
        self._exec("INSERT INTO job_metrics (metric_id,job_id,round,worker_id,ts,"
                   "loss,val_loss,ppl,ett,duration_s) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (mid, job_id, round_, worker_id, _now_iso(), loss, val_loss,
                    ppl, ett, duration_s))
        return mid

    def list_metrics(self, job_id: str) -> list[dict]:
        return self._rows("SELECT * FROM job_metrics WHERE job_id=? ORDER BY ts",
                          (job_id,))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
