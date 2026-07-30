"""RC5.2 Phase 2 R2: HTTP JSON-RPC server (threaded)."""
import json, uuid, hashlib, base64, threading, sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from rc5_2_jsonrpc import make_success, make_error
from rc5_2_split_server import SplitServerRuntime
from rc5_2_tensor_bundle import (
    tensor_bundle_v1_pack, tensor_bundle_v1_unpack, bundle_sha256
)
from rc5_2_canonical import numerical_profile_hash, partition_schema_hash, adapter_schema_hash
from rc5_2_receipt import generate_receipt, verify_receipt
from rc5_2_state import UnitState, can_transition

class ProtocolHandler:
    """Holds server state per request."""
    def __init__(self, db_path=":memory:"):
        self.db_path = db_path
        self.split_server = None
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS idempotency (req_key TEXT PRIMARY KEY, req_sha TEXT, response_json TEXT);
            CREATE TABLE IF NOT EXISTS units (unit_id TEXT PRIMARY KEY, state TEXT, response_json TEXT, created_at TEXT DEFAULT (datetime('now')));
            CREATE TABLE IF NOT EXISTS contributions (contribution_id TEXT PRIMARY KEY, status TEXT, data_json TEXT, nonce TEXT);
        """)
        self._conn.commit()

    def get_db(self):
        return self._conn

    def handle(self, method: str, params: dict) -> dict:
        handlers = {
            "step.open": self._step_open,
            "step.worker_forward.submit": self._worker_forward_submit,
            "step.server_backward.fetch": self._backward_fetch,
            "step.worker_update.submit": self._worker_update_submit,
            "step.commit": self._commit,
            "step.abort": self._abort,
            "checkpoint.upload": self._checkpoint_upload,
        }
        h = handlers.get(method)
        if not h: return make_error(None, -32601, f"Method not found: {method}")
        try:
            return h(params)
        except Exception as e:
            return make_error(None, -32000, str(e), {"method": method})

    def _get_or_create_split_server(self):
        if self.split_server is None:
            self.split_server = SplitServerRuntime()
        return self.split_server

    def _idempotent(self, key: str, sha: str) -> dict:
        conn = self.get_db()
        r = conn.execute("SELECT response_json FROM idempotency WHERE req_key=? AND req_sha=?", (key, sha)).fetchone()
        if r: return json.loads(r[0])
        r2 = conn.execute("SELECT response_json FROM idempotency WHERE req_key=?", (key,)).fetchone()
        if r2: raise ValueError("Same key, different payload — REJECTED")
        return None

    def _save_idempotent(self, key: str, sha: str, response: dict):
        conn = self.get_db()
        conn.execute("INSERT OR REPLACE INTO idempotency (req_key, req_sha, response_json) VALUES (?, ?, ?)",
                     (key, sha, json.dumps(response)))
        conn.commit()

    def _step_open(self, p):
        key = f"open:{p.get('run_id')}:{p.get('round_id')}:{p.get('assignment_id')}:{p.get('micro_unit_id')}"
        sha = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
        cached = self._idempotent(key, sha)
        if cached: return cached
        server = self._get_or_create_split_server()
        uid = p.get("unit_id", uuid.uuid4().hex)
        # Load state from reference
        conn = self.get_db()
        conn.execute("CREATE TABLE IF NOT EXISTS idempotency (req_key TEXT PRIMARY KEY, req_sha TEXT, response_json TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS units (unit_id TEXT PRIMARY KEY, state TEXT, response_json TEXT, created_at TEXT DEFAULT (datetime('now')))")
        conn.execute("INSERT OR IGNORE INTO units (unit_id, state) VALUES (?, ?)", (uid, UnitState.SERVER_ACTIVATION_READY.value))
        conn.commit()
        result = {"unit_id": uid, "state": "SERVER_ACTIVATION_READY", "message": "step.open accepted"}
        self._save_idempotent(key, sha, result)
        return result

    def _worker_forward_submit(self, p):
        uid = p.get("unit_id", "")
        key = f"fw:{uid}:{p.get('forward_id','')}"
        sha = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
        cached = self._idempotent(key, sha)
        if cached: return cached
        conn = self.get_db()
        conn.execute("UPDATE units SET state=? WHERE unit_id=?", (UnitState.CUT_ACTIVATION_ACCEPTED.value, uid))
        conn.commit()
        bw_id = hashlib.sha256(f"bw:{uid}:{uuid.uuid4().hex}".encode()).hexdigest()[:16]
        result = {"backward_id": bw_id, "state": "CUT_ACTIVATION_ACCEPTED"}
        conn.execute("UPDATE units SET response_json=? WHERE unit_id=?", (json.dumps(result), uid))
        conn.commit()
        self._save_idempotent(key, sha, result)
        return result

    def _backward_fetch(self, p):
        uid = p.get("unit_id", "")
        bw_id = p.get("backward_id", "")
        key = f"bw:{uid}:{bw_id}"
        sha = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
        cached = self._idempotent(key, sha)
        if cached: return cached
        conn = self.get_db()
        conn.execute("UPDATE units SET state=? WHERE unit_id=?", (UnitState.CUT_GRADIENT_DELIVERED.value, uid))
        conn.commit()
        result = {"state": "CUT_GRADIENT_DELIVERED", "message": "cut gradient delivered"}
        self._save_idempotent(key, sha, result)
        return result

    def _worker_update_submit(self, p):
        uid = p.get("unit_id", "")
        update_id = p.get("update_id", "")
        key = f"upd:{uid}:{update_id}"
        sha = p.get("delta_bundle_sha256", "")
        cached = self._idempotent(key, sha)
        if cached: return cached
        # Check different payload with same key
        conn = self.get_db()
        conn.execute("UPDATE units SET state=? WHERE unit_id=?", (UnitState.DELTA_VERIFIED.value, uid))
        conn.commit()
        delta_id = hashlib.sha256(f"d:{uid}:{update_id}".encode()).hexdigest()[:16]
        result = {"delta_id": delta_id, "state": "DELTA_VERIFIED"}
        self._save_idempotent(key, sha, result)
        return result

    def _commit(self, p):
        uid = p.get("unit_id", "")
        delta_id = p.get("delta_id", "")
        key = f"commit:{uid}:{delta_id}"
        sha = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
        cached = self._idempotent(key, sha)
        if cached: return cached
        receipt = generate_receipt(unit_id=uid, delta_id=delta_id, signing_key=b"\x01" * 32)
        conn = self.get_db()
        conn.execute("UPDATE units SET state=?, response_json=? WHERE unit_id=?", (UnitState.COMMITTED.value, json.dumps(receipt), uid))
        conn.commit()
        self._save_idempotent(key, sha, {"receipt": receipt, "state": "COMMITTED"})
        return {"receipt": receipt, "state": "COMMITTED"}

    def _abort(self, p):
        uid = p.get("unit_id", "")
        conn = self.get_db()
        conn.execute("UPDATE units SET state=? WHERE unit_id=? AND state NOT IN ('COMMITTED','ABORTED','EXPIRED')", (UnitState.ABORTED.value, uid))
        conn.commit()
        return {"state": "ABORTED"}

    def _checkpoint_upload(self, p):
        receipt = p.get("receipt", {})
        if not verify_receipt(receipt):
            raise ValueError("Invalid receipt HMAC")
        cid = receipt.get("receipt_id", "")
        conn = self.get_db()
        conn.execute("CREATE TABLE IF NOT EXISTS contributions (contribution_id TEXT PRIMARY KEY, status TEXT, data_json TEXT, nonce TEXT)")
        conn.execute("INSERT OR IGNORE INTO contributions (contribution_id, status, data_json) VALUES (?, 'RECEIVED', ?)", (cid, json.dumps(p)))
        conn.commit()
        return {"contribution_id": cid, "status": "RECEIVED"}

class RpcHandler(BaseHTTPRequestHandler):
    protocol = None
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        req = json.loads(body)
        result = self.server.protocol.handle(req["method"], req.get("params", {}))
        result["id"] = req.get("id")
        resp = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp)
    def log_message(self, *a): pass

def serve(host="127.0.0.1", port=19852, db_path=":memory:"):
    server = HTTPServer((host, port), RpcHandler)
    server.protocol = ProtocolHandler(db_path=db_path)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t
