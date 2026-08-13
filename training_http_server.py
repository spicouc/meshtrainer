"""training_http_server.py — SERVIDOR HTTP GENERIC (R2.1).

Capa de control model-agnostic. Reutilitza DIRECTAMENT les garanties RC5.4:
  - rc5_4_leases.LeaseManager  (taules leases_r54, TTL, rellotge injectable)
  - semàntica d'idempotència persistent (SQLite, no self._idem = {})
  - journal certificat (coordinator genèric)

El protocol NO depèn del model: els bundles són bytes opacs + SHA.
El backend és un plugin del worker, no del servidor.

Endpoints JSON-RPC: worker.register, worker.calibrate, assignment.get,
lease.acquire, lease.renew, step.open, step.worker_update.submit,
step.commit, checkpoint.upload, contribution.register,
contribution.validate, contribution.activate, step.release, round.fedavg.

IDEMPOTÈNCIA PERSISTENT (punt 6 de l'ordre):
  Taula idem_responses (lk PRIMARY KEY, sha, response_json) a la MATEIXA
  SQLite del servidor. Les respostes idempotents sobreviuen al reinici del
  procés: nou servidor + mateixa BD -> mateixa resposta byte-for-byte.
"""
import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from rc5_4_leases import (LeaseManager, LeaseError,
                          LEASE_ACTIVE, LEASE_RENEWED)
from model_round_coordinator import (ModelRoundCoordinator,
                                     ModelRecoveryError)

DEFAULT_PORT = 19862


def canonical_json_v1(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class TrainingServerError(Exception):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


class TrainingProtocolHandler:
    """Protocol JSON-RPC genèric (una connexió SQLite, single writer).

    R2.4: frontera administrativa real. Operacions ADMIN:
      round.create, assignment.create, contribution.activate, round.fedavg
    requereixen admin_token (configurat, no derivable, comparació segura).
    Un worker normal NO pot invocar operacions ADMIN (SEC-01..04).
    """

    # operacions ADMIN (R2.4, punt 1) — qualsevol operació futura que
    # modifiqui el pla autoritatiu de la ronda s'afegeix aquí.
    # R2.5: contribution.validate també és ADMIN (un worker NO pot validar
    # la seva pròpia contribució — l'estat la decideix el Coordinator).
    ADMIN_METHODS = frozenset([
        "round.create",
        "assignment.create",
        "contribution.validate",
        "contribution.activate",
        "round.fedavg",
    ])

    def __init__(self, db_path="training_server.db", now=None,
                 default_ttl_seconds=30.0, admin_token="",
                 admin_token_source="env"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.isolation_level = None
        except Exception:
            pass
        self.lm = LeaseManager(self._conn, now=now,
                               default_ttl_seconds=default_ttl_seconds)
        self.coord = ModelRoundCoordinator(self._conn)
        self._workers = {}   # worker_id -> {session_id, base_model_hash, ...}
        self._units = {}     # unit_id -> dict(state, ett_registered, ...)
        self._lock = threading.RLock()
        # R2.4: admin_token — configurat al servidor, no derivable del
        # worker_id, no retornat per cap endpoint, comparació segura.
        self._admin_token = admin_token
        self._admin_token_source = admin_token_source
        self._init_db()

    def _require_admin(self, p):
        """R2.4 (punt 2): credencial ADMIN obligatòria. Comparació segura
        (constant-time). Worker token/session/lease NO substitueix."""
        provided = p.get("admin_token", "")
        if not self._admin_token:
            raise TrainingServerError(
                "admin_token_not_configured",
                msg="el servidor no té admin_token configurat — REJECTED")
        if not provided:
            raise TrainingServerError(
                "admin_required",
                msg="operació ADMIN sense credencial — REJECTED")
        import hmac
        if not hmac.compare_digest(str(provided), self._admin_token):
            raise TrainingServerError(
                "admin_credential_invalid",
                msg="credencial ADMIN incorrecta — REJECTED (SEC-05)")
        return True

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS training_workers (
                worker_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                base_model_hash TEXT NOT NULL, config_sha TEXT,
                registered_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS training_units (
                unit_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                round_id TEXT NOT NULL, assignment_id TEXT NOT NULL,
                micro_unit_id TEXT NOT NULL, worker_id TEXT NOT NULL,
                session_id TEXT NOT NULL, lease_id TEXT NOT NULL,
                state TEXT NOT NULL, ett_registered INTEGER,
                ett_actual INTEGER, shard_manifest_sha TEXT,
                adapter_pre_hash TEXT, delta_bundle_sha256 TEXT,
                request_sha TEXT, receipt_json TEXT
            );
            CREATE TABLE IF NOT EXISTS idem_responses (
                lk TEXT PRIMARY KEY,
                sha TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
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
            raise TrainingServerError("missing_lease_params", missing=missing)
        self.lm._expire_overdue()
        row = self._conn.execute(
            "SELECT * FROM leases_r54 WHERE lease_id=?",
            (p["lease_id"],)).fetchone()
        if row is None:
            raise TrainingServerError("unknown_lease", lease_id=p["lease_id"])
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
            raise TrainingServerError("lease_binding_mismatch", fields=bad)
        if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):
            raise TrainingServerError(f"lease_not_active: {row['status']}")
        return row

    def _idem_get(self, lk):
        row = self._conn.execute(
            "SELECT sha, response_json FROM idem_responses WHERE lk=?",
            (lk,)).fetchone()
        return (row["sha"], row["response_json"]) if row else None

    def _idem_put(self, lk, sha, response_json):
        self._conn.execute(
            "INSERT OR REPLACE INTO idem_responses (lk, sha, response_json)"
            " VALUES (?,?,?)", (lk, sha, response_json))
        self._conn.commit()

    def _gated(self, method, params, fn):
        """Patró serve54: 1) lease primer, 2) idempotència PERSISTENT,
        3) execució, 4) save (SQLite)."""
        with self._lock:
            self._require_lease(params)
            lk = f"{method}|{params.get('unit_id') or params.get('contribution_id') or params.get('worker_id')}"
            sha = hashlib.sha256(
                canonical_json_v1(params).encode("utf-8")).hexdigest()
            cached = self._idem_get(lk)
            if cached:
                csha, cresp = cached
                if csha != sha:
                    raise TrainingServerError(
                        "idempotency_sha_mismatch",
                        msg="mateix logical_key, payload diferent — REJECTED")
                return json.loads(cresp)
            result = fn(params)
            self._idem_put(lk, sha, json.dumps(result, sort_keys=True))
            return result

    def _unit(self, unit_id):
        row = self._conn.execute(
            "SELECT * FROM training_units WHERE unit_id=?",
            (unit_id,)).fetchone()
        return dict(row) if row else None

    def _set_unit_state(self, unit_id, **fields):
        sets = ", ".join(f"{k}=?" for k in fields)
        self._conn.execute(f"UPDATE training_units SET {sets} WHERE unit_id=?",
                           (*fields.values(), unit_id))
        self._conn.commit()

    # -- endpoints ---------------------------------------------------------
    def handle(self, method, params):
        params = params or {}
        fn = getattr(self, f"_m_{method.replace('.', '_')}", None)
        if fn is None:
            raise TrainingServerError("method_not_found", method=method)
        # R2.4: frontera ADMIN (punt 1) — un worker NO pot invocar
        # operacions de control (SEC-01..04); cal admin_token vàlid.
        # NORMALITZEM el nom: "round_fedavg" i "round.fedavg" són el mateix
        # mètode i cap dels dos pot saltar-se la frontera.
        method_norm = method.replace("_", ".")
        if method_norm in self.ADMIN_METHODS:
            self._require_admin(params)
        if method in ("step.open", "step.worker_update.submit", "step.commit",
                      "checkpoint.upload", "step.release",
                      "contribution.register"):
            return self._gated(method, params, fn)
        return fn(params)

    def _m_worker_register(self, p):
        wid = p.get("worker_id", "")
        if not wid:
            raise TrainingServerError("worker_id_required")
        row = self._conn.execute(
            "SELECT * FROM training_workers WHERE worker_id=?",
            (wid,)).fetchone()
        if row is not None:
            if row["base_model_hash"] != p.get("base_model_hash"):
                raise TrainingServerError(
                    "base_model_mismatch",
                    msg="worker ja registrat amb base_model_hash DIFERENT — REJECTED")
            return {"worker_id": wid, "status": "REGISTERED"}
        with self._tx():
            self._conn.execute(
                "INSERT INTO training_workers (worker_id, session_id,"
                " base_model_hash, config_sha) VALUES (?,?,?,?)",
                (wid, p.get("session_id", ""), p.get("base_model_hash", ""),
                 p.get("config_sha")))
        self._workers[wid] = p
        return {"worker_id": wid, "status": "REGISTERED"}

    def _m_round_create(self, p):
        """Operació Coordinator/admin: crea la ronda (R2.3, punt 1).
        base_model_hash/adapter_0_sha/backend_id queden immutables."""
        try:
            r = self.coord.create_round(
                p.get("run_id", ""), p.get("round_id", ""),
                p.get("backend_id", ""), p.get("base_model_hash", ""),
                p.get("adapter_0_sha", ""), p.get("adapter_pre_hash", ""),
                p.get("dataset_manifest_sha", ""))
        except ModelRecoveryError as e:
            raise TrainingServerError(e.reason, **e.details)
        return {"run_id": p.get("run_id", ""), "round_id": p.get("round_id", ""),
                "status": r["status"], "backend_id": p.get("backend_id", "")}

    def _m_assignment_create(self, p):
        """Operació Coordinator/admin: crea l'assignació ABANS del calibrate
        (R2.3, punt 2). El worker NO pot crear/sobreescriure (AUTH-01/14)."""
        try:
            r = self.coord.create_assignment(
                p.get("run_id", ""), p.get("round_id", ""),
                p.get("assignment_id", ""), p.get("worker_id", ""),
                p.get("shard_id", ""), p.get("shard_manifest_sha", ""),
                p.get("base_model_hash", ""), p.get("adapter_0_sha", ""),
                int(p.get("expected_ett", 0)), int(p.get("revision", 1)))
        except ModelRecoveryError as e:
            raise TrainingServerError(e.reason, **e.details)
        return {"assignment_id": p.get("assignment_id", ""), "status": "ASSIGNED",
                "expected_ett": int(p.get("expected_ett", 0))}

    def _m_worker_calibrate(self, p):
        """worker.calibrate = VERIFICACIÓ (R2.3, punt 3).

        El Coordinator carrega l'assignació PREEXISTENT (assignment.create)
        i compara TOT: worker_id, base model, adapter_0_sha, shard_id,
        shard_manifest_sha i ETT. Qualsevol diferència -> REJECTED.
        NO crea ni modifica cap assignació (AUTH-01/02)."""
        wid = p.get("worker_id", "")
        row = self._conn.execute(
            "SELECT * FROM training_workers WHERE worker_id=?",
            (wid,)).fetchone()
        if row is None:
            raise TrainingServerError("worker_not_registered", worker_id=wid)
        if row["session_id"] != p.get("session_id"):
            raise TrainingServerError("wrong_session", worker_id=wid)
        # 1) la ronda ha d'existir i el base model ha de ser el GLOBAL
        try:
            rnd = self.coord.verify_round_base(
                p.get("run_id", ""), p.get("round_id", ""),
                p.get("base_model_hash", ""))
            # 2) l'adapter ha de ser el GLOBAL de la ronda (AUTH-05)
            self.coord.verify_round_adapter(
                p.get("run_id", ""), p.get("round_id", ""),
                p.get("adapter_0_sha", ""))
            # 3) l'assignació preexistent ha de coincidir en TOT
            a = self.coord.verify_calibration(
                p.get("run_id", ""), p.get("round_id", ""),
                p.get("assignment_id", ""), wid,
                p.get("shard_id", ""), p.get("shard_manifest_sha", ""),
                p.get("base_model_hash", ""), p.get("adapter_0_sha", ""),
                int(p.get("ett_registered", 0)))
        except ModelRecoveryError as e:
            raise TrainingServerError(e.reason, **e.details)
        return {"worker_id": wid, "status": "CALIBRATED",
                "expected_ett": a["expected_ett"], "round_status": rnd["status"]}

    def _m_assignment_get(self, p):
        a = self.coord.get_assignment(
            p.get("run_id", ""), p.get("round_id", ""),
            p.get("assignment_id", ""), p.get("worker_id", ""))
        if a is None:
            raise TrainingServerError("unknown_assignment",
                                      assignment_id=p.get("assignment_id"))
        return {"assignment_id": a["assignment_id"], "shard_id": a["shard_id"],
                "shard_manifest_sha": a["shard_manifest_sha"],
                "base_model_hash": a["base_model_hash"],
                "adapter_0_sha": a["adapter_0_sha"],
                "ett_registered": a["ett_registered"]}

    def _m_lease_acquire(self, p):
        try:
            return self.lm.acquire(
                p["run_id"], p["round_id"], p["assignment_id"],
                p["micro_unit_id"], p["worker_id"], p["session_id"],
                ttl_seconds=p.get("ttl_seconds"))
        except LeaseError as e:
            raise TrainingServerError("lease_rejected", msg=str(e))

    def _m_lease_renew(self, p):
        try:
            return self.lm.renew(p["lease_id"], p["worker_id"],
                                 p["session_id"], p["lease_nonce"],
                                 ttl_seconds=p.get("ttl_seconds"))
        except LeaseError as e:
            raise TrainingServerError("lease_renew_failed", msg=str(e))

    def _m_step_open(self, p):
        self.coord.verify_base_model(
            p["run_id"], p["round_id"], p["assignment_id"], p["worker_id"],
            p.get("base_model_hash", ""))
        unit_id = p.get("unit_id", "")
        ex = self._unit(unit_id)
        if ex:
            return {"unit_id": unit_id, "state": ex["state"]}
        with self._tx():
            self._conn.execute(
                "INSERT INTO training_units (unit_id, run_id, round_id,"
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
            raise TrainingServerError("unit_not_open",
                                      unit_id=p.get("unit_id"))
        self.coord.verify_shard(
            p["run_id"], p["round_id"], p["assignment_id"], p["worker_id"],
            p.get("shard_manifest_sha", ""))
        self.coord.verify_ett(
            p["run_id"], p["round_id"], p["assignment_id"], p["worker_id"],
            int(p.get("ett", -1)))
        rs = p.get("request_sha", "")
        if unit["request_sha"] and unit["request_sha"] != rs:
            raise TrainingServerError(
                "request_sha_mismatch",
                msg="mateixa unitat, request_sha diferent — REJECTED")
        if unit["adapter_pre_hash"] and unit["adapter_pre_hash"] != p.get("adapter_pre_hash", ""):
            raise TrainingServerError(
                "adapter_pre_hash_mismatch",
                msg="adapter_pre_hash no coincideix amb step.open — REJECTED")
        dsha = p.get("delta_bundle_sha256", "")
        if len(dsha) != 64:
            raise TrainingServerError("delta_sha_invalid")
        receipt = {
            "receipt_id": f"tr_{uuid.uuid4().hex[:16]}",
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
            raise TrainingServerError("unit_not_applied",
                                      unit_id=p.get("unit_id"))
        self._set_unit_state(p["unit_id"], state="COMMITTED")
        return {"unit_id": p["unit_id"], "state": "COMMITTED"}

    def _m_checkpoint_upload(self, p):
        unit = self._unit(p.get("unit_id", ""))
        if unit is None or unit["state"] != "COMMITTED":
            raise TrainingServerError("unit_not_committed",
                                      unit_id=p.get("unit_id"))
        receipt = json.loads(unit["receipt_json"])
        if canonical_json_v1(p.get("receipt", {})) != canonical_json_v1(receipt):
            raise TrainingServerError("receipt_mismatch",
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
        cid = p.get("contribution_id", "")
        c = self.coord.contribution(cid)
        if c is None:
            raise TrainingServerError("contribution_not_found", cid=cid)
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
            raise TrainingServerError("lease_release_failed", msg=str(e))
        return {"lease_id": p["lease_id"], "status": "RELEASED"}

    def _m_round_fedavg(self, p):
        a0 = self.coord.get_assignment(
            p.get("run_id", ""), p.get("round_id", ""),
            p.get("assignment_id", ""), p.get("worker_id", ""))
        if a0 is None:
            a0 = self._conn.execute(
                "SELECT * FROM model_assignments WHERE run_id=? AND round_id=?"
                " LIMIT 1",
                (p.get("run_id", ""), p.get("round_id", ""))).fetchone()
        if a0 is None:
            raise TrainingServerError("no_assignment_for_fedavg")
        return self.coord.fedavg(p["run_id"], p["round_id"],
                                 p.get("adapter_0_bundle_b64", ""))


class TrainingRpcHandler(BaseHTTPRequestHandler):
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
        except TrainingServerError as e:
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


def serve(host="127.0.0.1", port=DEFAULT_PORT, db_path="training_server.db",
          now=None, default_ttl_seconds=30.0, admin_token=None):
    """Arrenca el servidor. R2.4: admin_token es llegeix de l'entorn
    MESH_ADMIN_TOKEN si no es passa explícitament (mai derivable)."""
    if admin_token is None:
        admin_token = os.environ.get("MESH_ADMIN_TOKEN", "")
    proto = TrainingProtocolHandler(db_path, now=now,
                                    default_ttl_seconds=default_ttl_seconds,
                                    admin_token=admin_token)
    httpd = ThreadingHTTPServer((host, port), TrainingRpcHandler)
    httpd.proto = proto
    return httpd, proto
