"""RC5.2 Phase 2 R9: HTTP JSON-RPC — real step.open, per-unit numerical contexts."""
import json, uuid, hashlib, base64, sqlite3, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import torch

from rc5_2_jsonrpc import make_success, make_error, JSONRPC_VERSION
from rc5_2_canonical import canonical_json_v1, numerical_profile_hash, partition_schema_hash, adapter_schema_hash
from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope, validate_envelope
from rc5_2_numerical_models import SplitServerNumericalModel, MonolithicNumericalModel
from rc5_2_numerical_profile import PROFILE as P
from rc5_2_state import UnitState, can_transition, TERMINAL
from rc5_2_receipt import generate_receipt, verify_receipt
from rc5_2_coordinator import Coordinator

MAX_REQUEST = 10 * 1024 * 1024
SIGNING_KEY = None

class RpcHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_REQUEST:
            return self._respond(make_error(None, -32600, "Request too large"))
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._respond(make_error(None, -32600, "Content-Type must be application/json"))
        try: req = json.loads(self.rfile.read(length))
        except json.JSONDecodeError: return self._respond(make_error(None, -32700, "Parse error"))
        rid = req.get("id")
        if not isinstance(req, dict) or req.get("jsonrpc") != JSONRPC_VERSION:
            return self._respond(make_error(rid, -32600, "Invalid request"))
        method = req.get("method", "")
        if not isinstance(method, str) or not method:
            return self._respond(make_error(rid, -32600, "Invalid method"))
        params = req.get("params", {})
        if not isinstance(params, dict):
            return self._respond(make_error(rid, -32602, "Params must be object"))
        try:
            result = self.server.proto.handle(method, params)
            self._respond(make_success(rid, result))
        except ValueError as e:
            self._respond(make_error(rid, -32000, str(e)))
        except RuntimeError as e:
            self._respond(make_error(rid, -32003, str(e)))
    def _respond(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def log_message(self, *a): pass

def _make_causal_fixture():
    tokens = (torch.arange(P.sequence_length, dtype=torch.long).reshape(1, P.sequence_length) % P.vocab_size).contiguous()
    labels = tokens.clone()
    causal_mask = torch.triu(torch.full((P.sequence_length, P.sequence_length), float('-inf')), diagonal=1)
    return tokens, labels, causal_mask

def _server_forward(tokens, labels, causal_mask):
    """Run split server: embedding + base layers → server_activation, then top layers → loss."""
    import torch
    server = SplitServerNumericalModel()
    result = server.forward(tokens, labels, causal_mask, return_all=True)
    return result, server

def _ett(labels):
    shift = labels[:, 1:].contiguous()
    return int((shift != -100).sum().item())

def _causal_loss(logits, labels):
    shift_logits = logits[:, :-1, :].contiguous()
    shift_labels = labels[:, 1:].contiguous()
    loss = torch.nn.functional.cross_entropy(shift_logits.view(-1, P.vocab_size), shift_labels.view(-1), ignore_index=-100)
    return loss

class ProtocolHandler:
    def __init__(self, db_path="rpc_state.db", signing_key=None):
        self.db_path = db_path
        self.signing_key = signing_key or SIGNING_KEY
        self.coordinator = Coordinator(db_path)
        self._lock = threading.Lock()
        self._units = {}  # unit_id -> per-unit numerical context (A3: no shared runtime)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS units (
                unit_id TEXT PRIMARY KEY, state TEXT, run_id TEXT, round_id TEXT,
                assignment_id TEXT, micro_unit_id TEXT, worker_id TEXT, session_id TEXT,
                worker_model_hash TEXT, partition_schema_hash TEXT,
                base_adapter_hash TEXT, adapter_schema_hash TEXT,
                numerical_profile_hash TEXT,
                server_activation_b64 TEXT, cut_activation_b64 TEXT,
                cut_gradient_b64 TEXT, loss REAL, ett INTEGER,
                forward_id TEXT, forward_sha TEXT, backward_id TEXT,
                update_id TEXT, update_sha TEXT, delta_bundle_sha TEXT,
                delta_id TEXT, delta_bundle_b64 TEXT,
                receipt_json TEXT, created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS idempotency (
                logical_key TEXT, request_sha TEXT, response_json TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (logical_key, request_sha)
            );
        """)
        self._conn.commit()

    def get_db(self): return self._conn

    def _logical_key(self, method, params):
        if method == "step.open":
            return ":".join(params.get(k, "") for k in ["run_id", "round_id", "assignment_id", "micro_unit_id"])
        if method == "step.worker_forward.submit":
            return f"{params.get('unit_id','')}:{params.get('forward_id','')}"
        if method == "step.server_backward.fetch":
            return f"{params.get('unit_id','')}:{params.get('backward_id','')}"
        if method == "step.worker_update.submit":
            return f"{params.get('unit_id','')}:{params.get('update_id','')}"
        if method == "step.commit":
            return f"{params.get('unit_id','')}:{params.get('delta_id','')}"
        return method

    def _check_idem(self, lk, sha):
        c = self._conn
        r = c.execute("SELECT response_json FROM idempotency WHERE logical_key=? AND request_sha=?", (lk, sha)).fetchone()
        if r: return json.loads(r[0])
        r2 = c.execute("SELECT response_json FROM idempotency WHERE logical_key=?", (lk,)).fetchone()
        if r2: raise ValueError("Same key, different payload — REJECTED")
        return None

    def _save_idem(self, lk, sha, resp):
        self._conn.execute("INSERT OR REPLACE INTO idempotency (logical_key, request_sha, response_json) VALUES (?,?,?)",
                           (lk, sha, json.dumps(resp)))
        self._conn.commit()

    def handle(self, method, params):
        with self._lock:
            lk = self._logical_key(method, params)
            sha = hashlib.sha256(canonical_json_v1(params)).hexdigest()
            cached = self._check_idem(lk, sha)
            if cached: return cached
            handlers = {
                "step.open": self._step_open,
                "step.worker_forward.submit": self._step_worker_forward_submit,
                "step.server_backward.fetch": self._step_server_backward_fetch,
                "step.worker_update.submit": self._step_worker_update_submit,
                "step.commit": self._step_commit,
                "step.abort": self._step_abort,
                "checkpoint.upload": self._step_checkpoint_upload,
            }
            h = handlers.get(method)
            if not h: raise ValueError(f"Method not found: {method}")
            result = h(params)
            self._save_idem(lk, sha, result)
            return result

    def _get_unit(self, uid):
        r = self._conn.execute("SELECT * FROM units WHERE unit_id=?", (uid,)).fetchone()
        return dict(r) if r else None

    def _step_open(self, p):
        import torch
        required = ["protocol_version", "unit_id", "run_id", "round_id", "assignment_id",
                    "micro_unit_id", "worker_id", "session_id",
                    "worker_model_hash", "partition_schema_hash", "base_adapter_hash",
                    "adapter_schema_hash", "numerical_profile_hash"]
        for k in required:
            if k not in p or not p[k]:
                raise ValueError(f"Missing or empty required field: {k}")
        if p["protocol_version"] != "1.2.0-rc5.2":
            raise ValueError(f"Wrong protocol_version: {p['protocol_version']}")
        # A1: verify hashes, not just store
        if p["numerical_profile_hash"] != numerical_profile_hash():
            raise ValueError("Numerical profile hash mismatch")
        if p["partition_schema_hash"] != partition_schema_hash():
            raise ValueError("Partition schema hash mismatch")
        if p["adapter_schema_hash"] != adapter_schema_hash():
            raise ValueError("Adapter schema hash mismatch")
        uid = p["unit_id"]
        existing = self._get_unit(uid)
        if existing and existing["server_activation_b64"]:
            return {"unit_id": uid, "state": existing["state"],
                    "server_activation": json.loads(existing["server_activation_b64"])}
        # A3: per-unit numerical context — compute real server activation
        tokens, labels, causal_mask = _make_causal_fixture()
        torch.manual_seed(12345)  # deterministic per-unit model (oracle-comparable)
        server = SplitServerNumericalModel()
        sa = server.server_forward(tokens, causal_mask)
        env = pack_tensor_envelope(sa, "server_activation", uid, "s1")
        # store per-unit context
        self._units[uid] = {"server": server, "tokens": tokens, "labels": labels,
                            "causal_mask": causal_mask, "cut_activation": None, "cut_gradient": None}
        self._conn.execute("""
            INSERT OR REPLACE INTO units (unit_id, state, run_id, round_id, assignment_id, micro_unit_id,
                worker_id, session_id, worker_model_hash, partition_schema_hash,
                base_adapter_hash, adapter_schema_hash, numerical_profile_hash,
                server_activation_b64)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (uid, UnitState.SERVER_ACTIVATION_READY.value, p["run_id"], p["round_id"],
              p["assignment_id"], p["micro_unit_id"], p["worker_id"], p["session_id"],
              p["worker_model_hash"], p["partition_schema_hash"], p["base_adapter_hash"],
              p["adapter_schema_hash"], p["numerical_profile_hash"], json.dumps(env)))
        self._conn.commit()
        return {"unit_id": uid, "state": "SERVER_ACTIVATION_READY", "server_activation": env}

    def _step_worker_forward_submit(self, p):
        import torch
        uid = p["unit_id"]
        unit = self._get_unit(uid)
        if not unit: raise ValueError("Unit not found")
        s = UnitState(unit["state"])
        if not can_transition(s, UnitState.CUT_GRADIENT_READY):
            raise RuntimeError(f"Illegal state: {s.value}")
        env = p.get("cut_activation", {})
        validate_envelope(env, "cut_activation", uid, p.get("step_id", ""))
        tensor = unpack_tensor_envelope(env)
        ctx = self._units.get(uid)
        if ctx is None:
            raise RuntimeError("Per-unit context missing — call step.open first")
        server = ctx["server"]
        # top layers + loss + backward (real) via server_loss_and_backward
        result = server.server_loss_and_backward(tensor, ctx["labels"], ctx["causal_mask"])
        loss = result["loss"]
        ett = _ett(ctx["labels"])
        cut_gradient = result["cut_gradient"]
        ctx["cut_gradient"] = cut_gradient.detach().clone()
        bw_id = hashlib.sha256(f"bw:{uid}:{uuid.uuid4().hex}".encode()).hexdigest()[:16]
        self._conn.execute("UPDATE units SET state=?, cut_activation_b64=?, loss=?, ett=?, backward_id=? WHERE unit_id=?",
            (UnitState.CUT_GRADIENT_READY.value, json.dumps(env), float(loss), ett, bw_id, uid))
        self._conn.commit()
        return {"backward_id": bw_id, "state": "CUT_GRADIENT_READY",
                "loss": float(loss), "ett": ett}

    def _step_server_backward_fetch(self, p):
        uid = p["unit_id"]; bw_id = p.get("backward_id", "")
        unit = self._get_unit(uid)
        if not unit: raise ValueError("Unit not found")
        if unit["backward_id"] != bw_id: raise RuntimeError("Alien backward_id")
        s = UnitState(unit["state"])
        if not can_transition(s, UnitState.CUT_GRADIENT_DELIVERED):
            raise RuntimeError(f"Illegal state: {s.value}")
        ctx = self._units.get(uid)
        if ctx is None or ctx["cut_gradient"] is None:
            raise RuntimeError("No cut gradient available")
        env = pack_tensor_envelope(ctx["cut_gradient"], "cut_gradient", uid)
        self._conn.execute("UPDATE units SET state=?, cut_gradient_b64=? WHERE unit_id=?",
            (UnitState.CUT_GRADIENT_DELIVERED.value, json.dumps(env), uid))
        self._conn.commit()
        return {"cut_gradient": env, "state": "CUT_GRADIENT_DELIVERED"}

    def _step_worker_update_submit(self, p):
        uid = p["unit_id"]; update_id = p.get("update_id", "")
        unit = self._get_unit(uid)
        if not unit: raise ValueError("Unit not found")
        s = UnitState(unit["state"])
        if s != UnitState.CUT_GRADIENT_DELIVERED:
            raise RuntimeError(f"Illegal state: {s.value}, expected CUT_GRADIENT_DELIVERED")
        delta_sha = p.get("delta_bundle_sha256", "")
        bundle_b64 = p.get("delta_bundle_b64", "")
        bundle_bytes = base64.b64decode(bundle_b64, validate=True)
        if hashlib.sha256(bundle_bytes).hexdigest() != delta_sha:
            raise ValueError("Delta bundle SHA mismatch")
        delta_bundle_unpack(bundle_bytes, delta_sha)
        delta_id = hashlib.sha256(f"d:{uid}:{update_id}".encode()).hexdigest()[:16]
        self._conn.execute("UPDATE units SET state=?, update_id=?, update_sha=?, delta_bundle_sha=?, delta_id=?, delta_bundle_b64=? WHERE unit_id=?",
            (UnitState.DELTA_VERIFIED.value, update_id, delta_sha, delta_sha, delta_id, bundle_b64, uid))
        self._conn.commit()
        return {"delta_id": delta_id, "state": "DELTA_VERIFIED"}

    def _step_commit(self, p):
        uid = p["unit_id"]; delta_id = p.get("delta_id", "")
        unit = self._get_unit(uid)
        if not unit: raise ValueError("Unit not found")
        s = UnitState(unit["state"])
        if s != UnitState.DELTA_VERIFIED:
            raise RuntimeError(f"Illegal state: {s.value}")
        if unit["delta_id"] != delta_id: raise RuntimeError("delta_id mismatch")
        if self.signing_key is None: raise RuntimeError("No signing_key configured")
        if unit.get("receipt_json"):
            return {"receipt": json.loads(unit["receipt_json"]), "state": "COMMITTED"}
        receipt = generate_receipt(
            unit_id=uid, delta_id=delta_id, update_id=unit.get("update_id", ""),
            worker_id=unit.get("worker_id", ""), session_id=unit.get("session_id", ""),
            run_id=unit.get("run_id", ""), round_id=unit.get("round_id", ""),
            assignment_id=unit.get("assignment_id", ""),
            micro_unit_id=unit.get("micro_unit_id", ""),
            worker_model_hash=unit.get("worker_model_hash", ""),
            partition_schema_hash=unit.get("partition_schema_hash", ""),
            base_adapter_hash=unit.get("base_adapter_hash", ""),
            adapter_schema_hash=unit.get("adapter_schema_hash", ""),
            numerical_profile_hash=unit.get("numerical_profile_hash", ""),
            delta_bundle_sha256=unit.get("delta_bundle_sha", ""),
            delta_bundle_byte_length=len(base64.b64decode(unit.get("delta_bundle_b64", "") or "AA==")),
            ett=unit.get("ett", 0) or 0, loss=unit.get("loss", 0.0) or 0.0,
            signing_key=self.signing_key,
        )
        self._conn.execute("UPDATE units SET state=?, receipt_json=? WHERE unit_id=?",
            (UnitState.COMMITTED.value, json.dumps(receipt), uid))
        self._conn.commit()
        return {"receipt": receipt, "state": "COMMITTED"}

    def _step_abort(self, p):
        uid = p["unit_id"]; unit = self._get_unit(uid)
        if not unit: raise ValueError("Unit not found")
        s = UnitState(unit["state"])
        if s in TERMINAL: raise RuntimeError(f"Cannot abort {s.value}")
        self._conn.execute("UPDATE units SET state=? WHERE unit_id=?", (UnitState.ABORTED.value, uid))
        self._conn.commit(); return {"state": "ABORTED"}

    def _step_checkpoint_upload(self, p):
        if self.signing_key is None: raise RuntimeError("No signing_key configured")
        return self.coordinator.checkpoint_upload(p, self.signing_key)

def serve(host="127.0.0.1", port=19852, db_path="rpc_state.db", signing_key=None):
    import torch
    server = ThreadingHTTPServer((host, port), RpcHandler)
    server.proto = ProtocolHandler(db_path=db_path, signing_key=signing_key)
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    return server, t
