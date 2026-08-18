"""RC5.1 — Split Server: real split training, state machine, receipts."""
import json, time, uuid, hashlib, base64, threading, os
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from http.server import HTTPServer, BaseHTTPRequestHandler
from rc5_receipt import generate_receipt, verify_receipt, init_keyring

SEED = 42
torch.manual_seed(SEED)
np.random.seed(SEED)

# Init keyring at import (safe, idempotent)
init_keyring()

# ============================================================
# Tiny split model with LoRA
# ============================================================
class TinyServerModel(nn.Module):
    """Server-side: embedding + first 2 layers."""
    def __init__(self, vocab_size=100, d_model=32):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, d_model)
        self.layers = nn.ModuleList([nn.Linear(d_model, d_model) for _ in range(2)])
        self.lora_a = nn.Linear(d_model, 8, bias=False)
        self.lora_b = nn.Linear(8, d_model, bias=False)

    def forward(self, x):
        x = self.embed(x)
        for layer in self.layers:
            x = F.relu(layer(x))
        # LoRA contribution
        lora_out = self.lora_b(F.relu(self.lora_a(x)))
        return x + lora_out, x  # (output_with_lora, activations_for_cut)

    def get_lora_weight(self):
        return self.lora_b.weight @ self.lora_a.weight

class TinyClientModel(nn.Module):
    """Client-side: last 2 layers + output."""
    def __init__(self, d_model=32, n_classes=10):
        super().__init__()
        self.layers = nn.ModuleList([nn.Linear(d_model, d_model) for _ in range(2)])
        self.out = nn.Linear(d_model, n_classes)

    def forward(self, x):
        for layer in self.layers:
            x = F.relu(layer(x))
        return self.out(x)

class Tokenizer:
    """Deterministic tokenizer for PoC."""
    def __init__(self, vocab_size=100):
        self.vocab_size = vocab_size
        self.pad_id = 0
        self.mask_id = 1

    def encode(self, text, max_len=32):
        ids = [min(ord(c) % (self.vocab_size - 2) + 2, self.vocab_size - 1) for c in text[:max_len]]
        padding = max_len - len(ids)
        labels = ids + [-100] * padding
        ids = ids + [self.pad_id] * padding
        return torch.tensor([ids]), torch.tensor([labels])

    def effective_tokens(self, labels):
        """Count tokens where label != -100 and not padding."""
        return int((labels != -100).sum().item())

# ============================================================
# Split Server state with proper state machine
# ============================================================
VALID_TRANSITIONS = {
    None: ["OPEN"],
    "OPEN": ["EMBEDDING"],
    "EMBEDDING": ["CUT_ACTIVATION"],
    "CUT_ACTIVATION": ["CUT_GRADIENT"],
    "CUT_GRADIENT": ["COMMITTED"],
    "COMMITTED": [],
    "ABORTED": [],
}

class SplitServerState:
    def __init__(self, vocab_size=100, d_model=32):
        self.server_model = TinyServerModel(vocab_size, d_model)
        self.client_model = TinyClientModel(d_model)
        self.optimizer = torch.optim.AdamW(
            list(self.server_model.parameters()) + list(self.client_model.parameters()),
            lr=1e-3
        )
        self.tokenizer = Tokenizer(vocab_size)
        self.d_model = d_model
        self.vocab_size = vocab_size
        # Worker state
        self.workers = {}
        # Units: unit_id -> {state, worker_id, session_id, assignment_id, ...}
        self.units = {}
        # Completed receipts
        self.completed = []
        self.committed_nonces = set()
        self.lock = threading.Lock()
        self.run_id = None
        self.round_id = None

    def reset_run(self, run_id, round_id):
        self.run_id = run_id
        self.round_id = round_id
        self.units.clear()
        self.completed.clear()

    def validate_transition(self, unit_id, new_state):
        if unit_id not in self.units:
            return False, f"unknown unit {unit_id}"
        unit = self.units[unit_id]
        old = unit["state"]
        allowed = VALID_TRANSITIONS.get(old, [])
        if new_state not in allowed:
            return False, f"illegal transition {old} -> {new_state}"
        return True, "ok"

    def check_ownership(self, unit_id, worker_id, session_id=""):
        if unit_id not in self.units:
            return False, f"unknown unit {unit_id}"
        unit = self.units[unit_id]
        if unit["worker_id"] != worker_id:
            return False, f"unit {unit_id} not owned by {worker_id}"
        if session_id and unit["session_id"] != session_id:
            return False, f"session mismatch"
        return True, "ok"

    def count_effective_tokens(self, text):
        """Real token counting: labels with -100 excluded."""
        _, labels = self.tokenizer.encode(text)
        return self.tokenizer.effective_tokens(labels)

ss_state = SplitServerState()

# ============================================================
# JSON-RPC handlers
# ============================================================
def rpc_success(result, id_="1"):
    return {"jsonrpc": "2.0", "result": result, "id": id_}

def rpc_error(code, message, id_="1"):
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": id_}

def handle_step_open(params, worker_id):
    with ss_state.lock:
        if ss_state.run_id is None:
            return rpc_error(-32000, "No active run")
        # Check backpressure: max 1 ACTIVE per worker (excluding COMMITTED)
        active = sum(1 for u in ss_state.units.values()
                     if u["worker_id"] == worker_id and u["state"] not in ("COMMITTED", "ABORTED"))
        if active >= 1:
            return rpc_error(-32001, "Backpressure: max active units reached")

        assignment_id = params.get("assignment_id")
        micro_unit_id = params.get("micro_unit_id")
        if not assignment_id or not micro_unit_id:
            return rpc_error(-32602, "assignment_id and micro_unit_id required")

        # Verify assignment is not already open
        if micro_unit_id in ss_state.units:
            return rpc_error(-32002, f"unit {micro_unit_id} already exists")

        session_id = params.get("session_id", "")
        ss_state.units[micro_unit_id] = {
            "state": "OPEN",
            "worker_id": worker_id,
            "session_id": session_id,
            "assignment_id": assignment_id,
            "micro_unit_id": micro_unit_id,
            "text": None,
            "embedding": None,
            "cut_activation": None,
            "client_logits": None,
            "cut_gradient": None,
            "server_grad": None,
            "loss": None,
            "lora_delta": None,
            "step": 0,
        }
    return rpc_success({"unit_id": micro_unit_id, "assignment_id": assignment_id, "status": "OPEN"})

def handle_step_embedding(params, worker_id):
    unit_id = params.get("unit_id")
    text_b64 = params.get("text_b64")
    if not unit_id or not text_b64:
        return rpc_error(-32602, "Missing unit_id or text_b64")
    with ss_state.lock:
        ok, msg = ss_state.validate_transition(unit_id, "EMBEDDING")
        if not ok:
            return rpc_error(-32003, msg)
        ok, msg = ss_state.check_ownership(unit_id, worker_id)
        if not ok:
            return rpc_error(-32003, msg)
        unit = ss_state.units[unit_id]

    # Tokenize and run server forward (real autograd)
    text = base64.b64decode(text_b64).decode("utf-8")
    tokens, labels = ss_state.tokenizer.encode(text)
    ss_state.server_model.train()
    output_with_lora, cut_activation = ss_state.server_model(tokens)
    unit["text"] = text
    unit["embedding"] = output_with_lora.detach()
    unit["labels"] = labels
    unit["cut_activation_ref"] = cut_activation  # keep for backward
    unit["state"] = "EMBEDDING"

    emb_b64 = base64.b64encode(output_with_lora.detach().numpy().tobytes()).decode()
    return rpc_success({
        "unit_id": unit_id, "embedding_b64": emb_b64,
        "shape": list(output_with_lora.shape), "dtype": "float32",
    })

def handle_step_cut_activation(params, worker_id):
    unit_id = params.get("unit_id")
    act_b64 = params.get("activation_b64")
    if not unit_id or not act_b64:
        return rpc_error(-32602, "Missing unit_id or activation_b64")
    with ss_state.lock:
        ok, msg = ss_state.validate_transition(unit_id, "CUT_ACTIVATION")
        if not ok:
            return rpc_error(-32003, msg)
        ok, msg = ss_state.check_ownership(unit_id, worker_id)
        if not ok:
            return rpc_error(-32003, msg)
        unit = ss_state.units[unit_id]

    act = torch.frombuffer(base64.b64decode(act_b64), dtype=torch.float32).reshape(1, -1)
    unit["client_logits"] = act
    unit["state"] = "CUT_ACTIVATION"
    return rpc_success({"unit_id": unit_id})

def handle_step_cut_gradient(params, worker_id):
    unit_id = params.get("unit_id")
    grad_b64 = params.get("gradient_b64")
    if not unit_id or not grad_b64:
        return rpc_error(-32602, "Missing unit_id or gradient_b64")
    with ss_state.lock:
        ok, msg = ss_state.validate_transition(unit_id, "CUT_GRADIENT")
        if not ok:
            return rpc_error(-32003, msg)
        ok, msg = ss_state.check_ownership(unit_id, worker_id)
        if not ok:
            return rpc_error(-32003, msg)
        unit = ss_state.units[unit_id]

    grad = torch.frombuffer(base64.b64decode(grad_b64), dtype=torch.float32).reshape(1, -1)
    unit["cut_gradient"] = grad
    unit["state"] = "CUT_GRADIENT"

    # ---- REAL BACKWARD on server side ----
    ss_state.server_model.train()
    # Capture LoRA weights BEFORE optimizer step
    lora_before = ss_state.server_model.get_lora_weight().detach().clone()
    ss_state.optimizer.zero_grad()
    # Server forward again with requires_grad
    tokens = ss_state.tokenizer.encode(unit["text"])[0]
    output_with_lora, _ = ss_state.server_model(tokens)
    # Simplified: compute loss using gradient direction
    if unit.get("cut_activation_ref") is not None:
        loss = (output_with_lora * grad).sum()
        loss.backward()
        ss_state.optimizer.step()
    # Compute LoRA delta from captured weights
    lora_after = ss_state.server_model.get_lora_weight().detach().clone()
    lora_delta = lora_after - lora_before
    unit["lora_delta"] = lora_delta
    unit["loss"] = loss.item() if isinstance(loss, torch.Tensor) else 0.0

    return rpc_success({
        "unit_id": unit_id,
        "loss": unit["loss"],
        "lora_delta_sha256": hashlib.sha256(lora_delta.numpy().tobytes()).hexdigest()[:16],
    })

def handle_step_commit(params, worker_id):
    unit_id = params.get("unit_id")
    if not unit_id:
        return rpc_error(-32602, "Missing unit_id")
    with ss_state.lock:
        ok, msg = ss_state.validate_transition(unit_id, "COMMITTED")
        if not ok:
            return rpc_error(-32003, msg)
        ok, msg = ss_state.check_ownership(unit_id, worker_id)
        if not ok:
            return rpc_error(-32003, msg)
        unit = ss_state.units[unit_id]
        if unit.get("loss") is None:
            return rpc_error(-32005, "Cannot commit without loss computation")

        ett = ss_state.count_effective_tokens(unit.get("text", ""))
        delta_bytes = unit["lora_delta"].numpy().tobytes()
        delta_sha256_val = hashlib.sha256(delta_bytes).hexdigest()
        receipt = generate_receipt(
            run_id=ss_state.run_id,
            round_id=ss_state.round_id,
            assignment_id=unit["assignment_id"],
            micro_unit_id=unit_id,
            worker_id=worker_id,
            session_id=unit["session_id"],
            base_adapter_hash="sha256:base",
            model_hash="sha256:model",
            adapter_schema_hash="sha256:schema",
            loss_definition_hash="sha256:loss",
            precision_profile="fp32",
            data_shard_hash=hashlib.sha256(unit.get("text", "").encode()).hexdigest()[:16],
            effective_trainable_tokens=ett,
            optimizer_steps=1,
            first_step_id=f"{unit_id}-s0",
            last_step_id=f"{unit_id}-s0",
            delta_sha256=delta_sha256_val,
            receipt_key_id="k1",
        )
        unit["state"] = "COMMITTED"
        unit["receipt"] = receipt
        unit["delta_sha256"] = hashlib.sha256(delta_bytes).hexdigest()
        ss_state.completed.append((unit_id, receipt))
        ss_state.committed_nonces.add(receipt["receipt_nonce"])

    is_valid, msg = verify_receipt(receipt)
    return rpc_success({
        "unit_id": unit_id,
        "loss": unit["loss"],
        "receipt": receipt,
        "receipt_valid": is_valid,
        "delta_sha256": unit["delta_sha256"],
    })

HANDLERS = {
    "step.open": handle_step_open,
    "step.embedding": handle_step_embedding,
    "step.cut_activation": handle_step_cut_activation,
    "step.cut_gradient": handle_step_cut_gradient,
    "step.commit": handle_step_commit,
}

class SplitServerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            req = json.loads(body)
        except:
            self._respond(rpc_error(-32700, "Parse error"))
            return
        method = req.get("method", "")
        params = req.get("params", {})
        worker_id = self.headers.get("X-Worker-Id", "unknown")
        req_id = req.get("id", "1")
        handler = HANDLERS.get(method)
        if not handler:
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

    def log_message(self, *a):
        pass

def start_split_server(host="127.0.0.1", port=18792):
    server = HTTPServer((host, port), SplitServerHandler)
    print(f"[SPLIT SERVER] {host}:{port}  d_model={ss_state.d_model}")
    server.serve_forever()
