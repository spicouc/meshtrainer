"""RC5.1 — Split Server: HTTP JSON-RPC server with tiny split model."""
import json
import time
import uuid
import hashlib
import base64
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import torch
import torch.nn as nn
import torch.nn.functional as F

from rc5_receipt import generate_receipt, verify_receipt

# ============================================================
# Tiny split model (4 layers, cut at layer 2)
# ============================================================
class TinyServerModel(nn.Module):
    """Server-side: embedding + first 2 layers."""
    def __init__(self, vocab_size=100, d_model=32, n_layers=2):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, d_model)
        self.layers = nn.ModuleList([
            nn.Linear(d_model, d_model) for _ in range(n_layers)
        ])

    def forward(self, x):
        x = self.embed(x)
        for layer in self.layers:
            x = F.relu(layer(x))
        return x

class TinyClientModel(nn.Module):
    """Client-side: last 2 layers."""
    def __init__(self, d_model=32, n_layers=2):
        super().__init__()
        self.layers = nn.ModuleList([
            nn.Linear(d_model, d_model) for _ in range(n_layers)
        ])
        self.out = nn.Linear(d_model, 10)  # 10 classes

    def forward(self, x):
        for layer in self.layers:
            x = F.relu(layer(x))
        return self.out(x)

# ============================================================
# Split Server state
# ============================================================
class SplitServerState:
    def __init__(self, vocab_size=100, d_model=32):
        self.server_model = TinyServerModel(vocab_size, d_model)
        self.server_model.eval()
        self.d_model = d_model
        self.vocab_size = vocab_size
        self.workers = {}  # worker_id -> {"session_id": ..., "status": ...}
        self.active_units = {}  # unit_id -> worker_id
        self.unit_queue = []  # Queue of pending micro-units
        self.max_active = 1  # Backpressure: max ACTIVE per worker
        self.completed_units = []  # (unit_id, receipt)
        self.unit_steps = {}  # unit_id -> step state
        self.run_id = None
        self.round_id = None
        self.assignment_counter = 0
        self.unit_counter = 0
        self.lock = threading.Lock()
        self.embed_cache = {}  # text_hash -> embedding tensor

    def reset_run(self, run_id, round_id):
        self.run_id = run_id
        self.round_id = round_id
        self.active_units.clear()
        self.completed_units.clear()
        self.unit_steps.clear()
        self.assignment_counter = 0
        self.unit_counter = 0

# Global state
state = SplitServerState()

# ============================================================
# JSON-RPC handlers
# ============================================================
def rpc_error(code, message, id_="1"):
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": id_}

def rpc_success(result, id_="1"):
    return {"jsonrpc": "2.0", "result": result, "id": id_}

def handle_step_open(params, worker_id):
    """Worker opens a step. Assigns next micro-unit or blocks."""
    with state.lock:
        if state.run_id is None:
            return rpc_error(-32000, "No active run")
        # Check backpressure
        active_count = sum(1 for u, w in state.active_units.items() if w == worker_id)
        if active_count >= state.max_active:
            return rpc_error(-32001, "Backpressure: max active units reached")
        # Get or create unit
        if state.unit_queue:
            unit_id = state.unit_queue.pop(0)
        else:
            state.unit_counter += 1
            unit_id = f"u-{state.unit_counter:06d}"
        state.active_units[unit_id] = worker_id
        state.assignment_counter += 1
        assignment_id = f"a-{state.assignment_counter:04d}"
        state.unit_steps[unit_id] = {
            "assignment_id": assignment_id,
            "step": 0,
            "text": None,
            "embedding": None,
            "activation": None,
            "gradient": None,
        }
    return rpc_success({
        "unit_id": unit_id,
        "assignment_id": assignment_id,
        "step_id": f"{unit_id}-s0",
    })

def handle_step_embedding(params, worker_id):
    """Worker sends text, server returns embeddings."""
    unit_id = params.get("unit_id")
    text_b64 = params.get("text_b64")
    if not unit_id or not text_b64:
        return rpc_error(-32602, "Missing unit_id or text_b64")
    with state.lock:
        if unit_id not in state.unit_steps:
            return rpc_error(-32004, f"Unknown unit: {unit_id}")
        step = state.unit_steps[unit_id]
    text_bytes = base64.b64decode(text_b64)
    text_str = text_bytes.decode("utf-8")
    # Tokenize: simple char-level encoding for PoC
    tokens = torch.tensor([[min(ord(c) % state.vocab_size, state.vocab_size - 1) for c in text_str]])
    with torch.no_grad():
        embedding = state.server_model(tokens)
    emb_bytes = embedding.numpy().tobytes()
    emb_b64 = base64.b64encode(emb_bytes).decode()
    step["embedding"] = embedding
    step["text"] = text_str
    return rpc_success({
        "unit_id": unit_id,
        "embedding_b64": emb_b64,
        "shape": list(embedding.shape),
        "dtype": "float32",
    })

def handle_step_cut_activation(params, worker_id):
    """Server receives activation from worker (forward complete on client side)."""
    unit_id = params.get("unit_id")
    act_b64 = params.get("activation_b64")
    if not unit_id or not act_b64:
        return rpc_error(-32602, "Missing unit_id or activation_b64")
    with state.lock:
        if unit_id not in state.unit_steps:
            return rpc_error(-32004, f"Unknown unit: {unit_id}")
        step = state.unit_steps[unit_id]
    act_bytes = base64.b64decode(act_b64)
    act_tensor = torch.frombuffer(act_bytes, dtype=torch.float32).reshape(1, -1)
    step["activation"] = act_tensor
    # For PoC: server does forward on last layers and computes loss
    with torch.no_grad():
        logits = act_tensor @ act_tensor.T  # dummy loss computation
    return rpc_success({
        "unit_id": unit_id,
        "logits_hash": hashlib.sha256(act_bytes).hexdigest()[:16],
    })

def handle_step_cut_gradient(params, worker_id):
    """Worker sends gradient of the cut. Server computes backward on server side."""
    unit_id = params.get("unit_id")
    grad_b64 = params.get("gradient_b64")
    if not unit_id or not grad_b64:
        return rpc_error(-32602, "Missing unit_id or gradient_b64")
    with state.lock:
        if unit_id not in state.unit_steps:
            return rpc_error(-32004, f"Unknown unit: {unit_id}")
        step = state.unit_steps[unit_id]
    grad_bytes = base64.b64decode(grad_b64)
    grad_tensor = torch.frombuffer(grad_bytes, dtype=torch.float32).reshape(1, -1)
    # For PoC: server-side backward (dummy)
    return rpc_success({
        "unit_id": unit_id,
        "server_grad_hash": hashlib.sha256(grad_bytes).hexdigest()[:16],
    })

def handle_step_commit(params, worker_id):
    """Both sides confirm. Server generates receipt."""
    unit_id = params.get("unit_id")
    with state.lock:
        if unit_id not in state.unit_steps:
            return rpc_error(-32004, f"Unknown unit: {unit_id}")
        step = state.unit_steps[unit_id]
        worker = state.workers.get(worker_id, {})
        receipt = generate_receipt(
            run_id=state.run_id,
            round_id=state.round_id,
            assignment_id=step["assignment_id"],
            micro_unit_id=unit_id,
            worker_id=worker_id,
            session_id=worker.get("session_id", ""),
            base_adapter_hash="sha256:base",
            model_hash="sha256:model",
            adapter_schema_hash="sha256:schema",
            loss_definition_hash="sha256:loss",
            precision_profile="fp32",
            data_shard_hash=hashlib.sha256(step.get("text", "").encode()).hexdigest()[:16],
            effective_trainable_tokens=len(step.get("text", "")),
            optimizer_steps=1,
            first_step_id=f"{unit_id}-s0",
            last_step_id=f"{unit_id}-s0",
        )
        state.completed_units.append((unit_id, receipt))
        state.active_units.pop(unit_id, None)
        state.unit_steps.pop(unit_id, None)
    return rpc_success({
        "unit_id": unit_id,
        "receipt": receipt,
        "receipt_verified": verify_receipt(receipt),
    })

# ============================================================
# HTTP Server
# ============================================================
HANDLERS = {
    "step.open": handle_step_open,
    "step.embedding": handle_step_embedding,
    "step.cut_activation": handle_step_cut_activation,
    "step.cut_gradient": handle_step_cut_gradient,
    "step.commit": handle_step_commit,
}

class SplitServerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._respond(rpc_error(-32700, "Parse error"))
            return
        method = req.get("method", "")
        params = req.get("params", {})
        worker_id = self.headers.get("X-Worker-Id", "unknown")
        req_id = req.get("id", "1")
        handler = HANDLERS.get(method)
        if handler is None:
            self._respond(rpc_error(-32601, f"Method not found: {method}", req_id))
            return
        result = handler(params, worker_id)
        result["id"] = req_id
        self._respond(result)

    def _respond(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, format, *args):
        pass  # Silence for PoC

def start_split_server(host="127.0.0.1", port=18792):
    server = HTTPServer((host, port), SplitServerHandler)
    print(f"[SPLIT SERVER] Listening on {host}:{port}")
    print(f"[SPLIT SERVER] d_model={state.d_model}, vocab_size={state.vocab_size}")
    server.serve_forever()

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18792
    start_split_server(port=port)
