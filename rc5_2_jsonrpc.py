"""RC5.2 Phase 2 R2: JSON-RPC 2.0 over HTTP."""
import json, urllib.request, urllib.error, uuid

JSONRPC_VERSION = "2.0"

def make_request(method: str, params: dict, req_id: str = None) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id or uuid.uuid4().hex, "method": method, "params": params}

def make_success(req_id: str, result: dict) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}

def make_error(req_id: str, code: int, message: str, data: dict = None) -> dict:
    err = {"code": code, "message": message}
    if data: err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": err}

class JsonRpcClient:
    def __init__(self, base_url: str = "http://127.0.0.1:19852"):
        self.base = base_url

    def call(self, method: str, params: dict) -> dict:
        req = make_request(method, params)
        data = json.dumps(req).encode("utf-8")
        r = urllib.request.Request(self.base, data=data, headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(r, timeout=30)
            body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
        if "error" in body:
            raise ValueError(f"RPC error: {body['error']}")
        return body["result"]
