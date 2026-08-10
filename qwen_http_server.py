"""qwen_http_server.py — serve54 Qwen: control HTTP JSON-RPC per al pilot
distribuït (R2). ADDITIU: no modifica cap mòdul certificat.

Reutilitza rc5_4_leases.LeaseManager REAL (mateixes taules leases_r54, estats
ACTIVE/RENEWED/RELEASED/EXPIRED/ABORTED, TTL, rellotge injectable) i el
QwenRoundCoordinator (assignacions, contribucions, FedAvg).

Patró de control (com el serve54 certificat):
  1) LEASE VALIDATION primer (absent/expirada/wrong worker-session -> REJECTED)
  2) IDEMPOTÈNCIA (mateix logical_key + mateix sha -> mateixa resposta exacta;
     mateix lk + sha diferent -> REJECTED)
  3) EXECUCIÓ
  4) IDEMPOTÈNCIA SAVE

Endpoints JSON-RPC: worker.register, worker.calibrate, assignment.get,
lease.acquire, step.open, step.worker_update.submit, step.commit,
checkpoint.upload, contribution.register, contribution.validate,
contribution.activate, step.release.
"""
import base64
import hashlib
import json
import sqlite3
import threading
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from rc5_4_leases import (LeaseManager, LeaseError,
                          LEASE_ACTIVE, LEASE_RENEWED)
from qwen_round_coordinator import QwenRoundCoordinator, QwenRecoveryError

DEFAULT_PORT = 19862


def canonical_json_v1(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class QwenServerError(Exception):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


class QwenProtocolHandler:
    """Protocol JSON-RPC del serve54 Qwen (una connexió SQLite, single writer)."""

    def __init__(self, db_path="qwen_server.db", now=None,
                 default_ttl_seconds=30.0):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.isolation_level = None
        except Exception:
            pass
        self.lm = LeaseManager(self._conn, now=now,
                               default_ttl_seconds=default_ttl_seconds)
        self.coord = QwenRoundCoordinator(self._conn)
        self._workers = {}   # worker_id -> {session_id, base_model_hash, config_sha}
        self._units = {}     # unit_id -> dict(state, ett_registered, ...)
        self._idem = {}      # lk -> (sha, response_json)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS qwen_workers (
                worker_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                base_model_hash TEXT NOT NULL, config_sha TEXT,
                registered_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS qwen_units (
                unit_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                round_id TEXT NOT NULL, assignment_id TEXT NOT NULL,
                micro_unit_id TEXT NOT NULL, worker_id TEXT NOT NULL,
                session_id TEXT NOT NULL, lease_id TEXT NOT NULL,
                state TEXT NOT NULL, ett_registered INTEGER,
                ett_actual INTEGER, shard_manifest_sha TEXT,
                adapter_pre_hash TEXT, delta_bundle_sha256 TEXT,
                request_sha TEXT, receipt_json TEXT
            );
        """)
        self._conn.commit()

    # -- helpers -----------------------------------------------------------
    def _tx(self):
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

    def _require_lease(self, p):
        """Full-binding check (com RC54RecoveryCoordinator._check_lease):
        lease absent, expirada o worker/session incorrectes -> REJECTED."""
        need = ["lease_id", "lease_nonce", "worker_id", "session_id",
                "run_id", "round_id", "assignment_id", "micro_unit_id"]
        missing = [k for k in need if k not in p or p.get(k) in (None, "")]
        if missing:
            raise QwenServerError("missing_lease_params", missing=missing)
        self.lm._expire_overdue()
        row = self._conn.execute(
            "SELECT * FROM leases_r54 WHERE lease_id=?",
            (p["lease_id"],)).fetchone()
        if row is None:
            raise QwenServerError("unknown_lease", lease_id=p["lease_id"])
        checks = [
            ("run_id", row["run_id"] == p["run_id"]),
            ("round_id", row["round_id"] == p["round_id"]),
            ("assignment_id", row["assignment_id"] == p["assignment_id"]),
            ("micro_unit_id", row["micro_unit_id"] == p["micro_unit_id"]),
            ("worker_id", row["worker_id"] == p["worker_id"]),
            ("session_id", row["session_id"] == p["session_id"]),
            ("lease_nonce", row["lease_nonce"] == p["lease_nonce"]),
        ]
        bad = [n for n, ok in checks if not ok]
        if bad:
            raise QwenServerError("lease_binding_mismatch", fields=bad)
        if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):
            raise QwenServerError(f"lease_not_active: {row['status']}")
        return row

    def _gated(self, method, params, fn):
        """Patró serve54: 1) lease primer, 2) idempotència, 3) execució, 4) save."""
        with self._lock:
            self._require_lease(params)
            lk = f"{method}|{params.get('unit_id') or params.get('contribution_id') or params.get('worker_id')}"
            sha = hashlib.sha256(
                canonical_json_v1(params).encode("utf-8")).hexdigest()
            cached = self._idem.get(lk)
            if cached:
                csha, cresp = cached
                if csha != sha:
                    raise QwenServerError(
                        "idempotency_sha_mismatch",
                        msg="mateix logical_key, payload diferent — REJECTED")
                return json.loads(cresp)
            result = fn(params)
            self._idem[lk] = (sha, json.dumps(result, sort_keys=True))
            return result

    def _unit(self, unit_id):
        row = self._conn.execute(
            "SELECT * FROM qwen_units WHERE unit_id=?", (unit_id,)).fetchone()
        return dict(row) if row else None

    def _set_unit_state(self, unit_id, **fields):
        sets = ", ".join(f"{k}=?" for k in fields)
        self._conn.execute(f"UPDATE qwen_units SET {sets} WHERE unit_id=?",
                           (*fields.values(), unit_id))
        self._conn.commit()

    # -- endpoints ---------------------------------------------------------
    def handle(self, method, params):
        params = params or {}
        fn = getattr(self, f"_m_{method.replace('.', '_')}", None)
        if fn is None:
            raise QwenServerError("method_not_found", method=method)
        if method in ("step.open", "step.worker_update.submit", "step.commit",
                      "checkpoint.upload", "step.release",
                      "contribution.register"):
            return self._gated(method, params, fn)
        return fn(params)

    def _m_worker_register(self, p):
        wid = p.get("worker_id", "")
        if not wid:
            raise QwenServerError("worker_id_required")
        ex = self._workers.get(wid)
        row = self._conn.execute(
            "SELECT * FROM qwen_workers WHERE worker_id=?", (wid,)).fetchone()
        if row is not None:
            if row["base_model_hash"] != p.get("base_model_hash"):
                raise QwenServerError(
                    "base_model_mismatch",
                    msg="worker ja registrat amb base_model_hash DIFERENT — REJECTED")
            return {"worker_id": wid, "status": "REGISTERED"}
        with self._tx():
            self._conn.execute(
                "INSERT INTO qwen_workers (worker_id, session_id,"
                " base_model_hash, config_sha) VALUES (?,?,?,?)",
                (wid, p.get("session_id", ""), p.get("base_model_hash", ""),
                 p.get("config_sha")))
        self._workers[wid] = p
        return {"worker_id": wid, "status": "REGISTERED"}

    def _m_worker_calibrate(self, p):
        wid = p.get("worker_id", "")
        row = self._conn.execute(
            "SELECT * FROM qwen_workers WHERE worker_id=?", (wid,)).fetchone()
        if row is None:
            raise QwenServerError("worker_not_registered", worker_id=wid)
        if row["session_id"] != p.get("session_id"):
            raise QwenServerError("wrong_session", worker_id=wid)
        # ETT registrat: el guardem a l'assignació del Coordinator
        self.coord.assign(
            p.get("run_id", ""), p.get("round_id", ""),
            p.get("assignment_id", ""), wid, p.get("shard_id", ""),
            p.get("shard_manifest_sha", ""), row["base_model_hash"],
            p.get("adapter_0_sha", ""), ett_registered=int(p.get("ett_registered", 0)))
        return {"worker_id": wid, "status": "CALIBRATED"}

    def _m_assignment_get(self, p):
        a = self.coord.get_assignment(
            p.get("run_id", ""), p.get("round_id", ""),
            p.get("assignment_id", ""), p.get("worker_id", ""))
        if a is None:
            raise QwenServerError("unknown_assignment",
                                  assignment_id=p.get("assignment_id"))
        return {"assignment_id": a["assignment_id"], "shard_id": a["shard_id"],
                "shard_manifest_sha": a["shard_manifest_sha"],
                "base_model_hash": a["base_model_hash"],
                "adapter_0_sha": a["adapter_0_sha"],
                "ett_registered": a["ett_registered"]}

    def _m_lease_acquire(self, p):
        try:
            ls = self.lm.acquire(
                p["run_id"], p["round_id"], p["assignment_id"],
                p["micro_unit_id"], p["worker_id"], p["session_id"],
                ttl_seconds=p.get("ttl_seconds"))
        except LeaseError as e:
            raise QwenServerError("lease_rejected", msg=str(e))
        return ls

    def _m_lease_renew(self, p):
        """Renovació de la lease pròpia (recovery): només l'owner
        (worker+session+nonce) pot renovar; una lease expirada no es pot
        renovar. És el mecanisme del RC5.4 perquè un worker reiniciat
        continuï la seva unitat sense reassignació."""
        try:
            return self.lm.renew(p["lease_id"], p["worker_id"],
                                 p["session_id"], p["lease_nonce"],
                                 ttl_seconds=p.get("ttl_seconds"))
        except LeaseError as e:
            raise QwenServerError("lease_renew_failed", msg=str(e))

    def _m_step_open(self, p):
        # base model identity vs assignació (Coordinator rebutja base diferent)
        self.coord.verify_base_model(
            p["run_id"], p["round_id"], p["assignment_id"], p["worker_id"],
            p.get("base_model_hash", ""))
        unit_id = p.get("unit_id", "")
        ex = self._unit(unit_id)
        if ex:
            return {"unit_id": unit_id, "state": ex["state"]}
        with self._tx():
            self._conn.execute(
                "INSERT INTO qwen_units (unit_id, run_id, round_id,"
                " assignment_id, micro_unit_id, worker_id, session_id,"
                " lease_id, state, ett_registered, shard_manifest_sha,"
                " adapter_pre_hash) VALUES (?,?,?,?,?,?,?,?, 'OPEN', ?, ?, ?)",
                (unit_id, p["run_id"], p["round_id"], p["assignment_id"],
                 p["micro_unit_id"], p["worker_id"], p["session_id"],
                 p["lease_id"], p.get("ett_registered"),
                 p.get("shard_manifest_sha"), p.get("adapter_pre_hash")))
        return {"unit_id": unit_id, "state": "OPEN"}

    def _m_step_worker_update_submit(self, p):
        unit = self._unit(p.get("unit_id", ""))
        if unit is None or unit["state"] != "OPEN":
            raise QwenServerError("unit_not_open",
                                  unit_id=p.get("unit_id"))
        # shard binding: els exemples processats han de correspondre al manifest
        self.coord.verify_shard(
            p["run_id"], p["round_id"], p["assignment_id"], p["worker_id"],
            p.get("shard_manifest_sha", ""))
        # ETT: ha de coincidir amb el registrat (inventat/constant -> REJECTED)
        self.coord.verify_ett(
            p["run_id"], p["round_id"], p["assignment_id"], p["worker_id"],
            int(p.get("ett", -1)))
        # request_sha: mateixa unitat + mateix request_sha -> mateixa resposta;
        # request_sha diferent -> REJECTED
        rs = p.get("request_sha", "")
        if unit["request_sha"] and unit["request_sha"] != rs:
            raise QwenServerError(
                "request_sha_mismatch",
                msg="mateixa unitat, request_sha diferent — REJECTED")
        # adapter_pre_hash: ha de coincidir amb el de step.open (stale adapter
        # o adapter diferent -> REJECTED)
        if unit["adapter_pre_hash"] and unit["adapter_pre_hash"] != p.get("adapter_pre_hash", ""):
            raise QwenServerError(
                "adapter_pre_hash_mismatch",
                msg="adapter_pre_hash no coincideix amb step.open — REJECTED")
        dsha = p.get("delta_bundle_sha256", "")
        if len(dsha) != 64:
            raise QwenServerError("delta_sha_invalid")
        receipt = {
            "receipt_id": f"qw_{uuid.uuid4().hex[:16]}",
            "receipt_nonce": hashlib.sha256(
                f"{p['unit_id']}|{rs}|{uuid.uuid4().hex}".encode()).hexdigest()[:32],
            "update_id": p.get("update_id", ""),
            "delta_id": p.get("delta_id", ""),
            "run_id": p["run_id"], "round_id": p["round_id"],
            "assignment_id": p["assignment_id"],
            "micro_unit_id": p["micro_unit_id"],
            "worker_id": p["worker_id"], "session_id": p["session_id"],
            "delta_bundle_sha256": dsha,
            "delta_bundle_byte_length": int(p.get("delta_bundle_byte_length", 0)),
            "effective_trainable_tokens": int(p.get("ett", -1)),
            "loss": float(p.get("loss", 0.0)),
            "base_model_hash": p.get("base_model_hash", ""),
            "adapter_pre_hash": p.get("adapter_pre_hash", ""),
            "shard_manifest_sha": p.get("shard_manifest_sha", ""),
            "request_sha": rs,
        }
        self._set_unit_state(p["unit_id"], state="APPLIED",
                             ett_actual=int(p.get("ett", -1)),
                             request_sha=rs,
                             adapter_pre_hash=p.get("adapter_pre_hash", ""),
                             delta_bundle_sha256=dsha,
                             receipt_json=json.dumps(receipt, sort_keys=True))
        return {"unit_id": p["unit_id"], "state": "APPLIED", "receipt": receipt}

    def _m_step_commit(self, p):
        unit = self._unit(p.get("unit_id", ""))
        if unit is None or unit["state"] != "APPLIED":
            raise QwenServerError("unit_not_applied",
                                  unit_id=p.get("unit_id"))
        self._set_unit_state(p["unit_id"], state="COMMITTED")
        return {"unit_id": p["unit_id"], "state": "COMMITTED"}

    def _m_checkpoint_upload(self, p):
        unit = self._unit(p.get("unit_id", ""))
        if unit is None or unit["state"] != "COMMITTED":
            raise QwenServerError("unit_not_committed",
                                  unit_id=p.get("unit_id"))
        receipt = json.loads(unit["receipt_json"])
        # byte-equal: el receipt del payload ha de coincidir amb el registrat
        if canonical_json_v1(p.get("receipt", {})) != canonical_json_v1(receipt):
            raise QwenServerError("receipt_mismatch",
                                  msg="receipt no coincideix amb el registrat")
        cid = receipt["receipt_id"]
        delta_b64 = p.get("delta_bundle_b64", "")
        self.coord.register_uploaded_contribution(
            cid, unit["run_id"], unit["round_id"], unit["assignment_id"],
            unit["worker_id"], receipt["effective_trainable_tokens"],
            delta_b64, receipt["delta_bundle_sha256"],
            receipt["request_sha"], receipt["adapter_pre_hash"],
            source_sha=hashlib.sha256(canonical_json_v1(p).encode()).hexdigest())
        return {"contribution_id": cid, "status": "RECEIVED"}

    def _m_contribution_register(self, p):
        # idempotent: mateix cid -> mateixa resposta (Coordinator)
        cid = p.get("contribution_id", "")
        c = self.coord.contribution(cid)
        if c is None:
            raise QwenServerError("contribution_not_found", cid=cid)
        return {"contribution_id": cid, "status": c["status"]}

    def _m_contribution_validate(self, p):
        cid = p.get("contribution_id", "")
        self.coord.validate_contribution(cid)
        return {"contribution_id": cid, "status": "VALIDATED"}

    def _m_contribution_activate(self, p):
        cid = p.get("contribution_id", "")
        self.coord.activate_contribution(cid)
        return {"contribution_id": cid, "status": "ACTIVE"}

    def _m_step_release(self, p):
        try:
            self.lm.release(p["lease_id"], p["worker_id"], p["session_id"],
                            p["lease_nonce"])
        except LeaseError as e:
            raise QwenServerError("lease_release_failed", msg=str(e))
        return {"lease_id": p["lease_id"], "status": "RELEASED"}

    def _m_round_fedavg(self, p):
        # només per al driver/coordinació (no gatejat per lease)
        a0 = self.coord.get_assignment(
            p.get("run_id", ""), p.get("round_id", ""),
            p.get("assignment_id", ""), p.get("worker_id", ""))
        if a0 is None:
            a0 = self._conn.execute(
                "SELECT * FROM qwen_assignments WHERE run_id=? AND round_id=? LIMIT 1",
                (p.get("run_id", ""), p.get("round_id", ""))).fetchone()
        if a0 is None:
            raise QwenServerError("no_assignment_for_fedavg")
        return self.coord.fedavg(p["run_id"], p["round_id"],
                                 p.get("adapter_0_bundle_b64", ""))


class QwenRpcHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _respond(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._respond({"jsonrpc": "2.0",
                           "error": {"code": -32700, "message": "Parse error"},
                           "id": None})
            return
        try:
            result = self.server.proto.handle(body.get("method", ""),
                                              body.get("params", {}))
            resp = {"jsonrpc": "2.0", "result": result, "id": body.get("id")}
        except QwenServerError as e:
            resp = {"jsonrpc": "2.0",
                    "error": {"code": -32042, "message": e.reason,
                              "data": e.details}, "id": body.get("id")}
        except Exception as e:  # noqa: BLE001
            resp = {"jsonrpc": "2.0",
                    "error": {"code": -32000, "message": str(e)},
                    "id": body.get("id")}
        self._respond(resp)

    def log_message(self, *args):
        pass


def serve(host="127.0.0.1", port=DEFAULT_PORT, db_path="qwen_server.db",
          now=None, default_ttl_seconds=30.0):
    proto = QwenProtocolHandler(db_path, now=now,
                                default_ttl_seconds=default_ttl_seconds)
    httpd = ThreadingHTTPServer((host, port), QwenRpcHandler)
    httpd.proto = proto
    return httpd, proto
