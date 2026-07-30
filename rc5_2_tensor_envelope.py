"""RC5.2 Phase 2 R3: Tensor envelopes for network transport."""
import base64, json, hashlib, torch, io
from rc5_2_canonical import canonical_json_v1

ALLOWED_ROLES = {"server_activation", "cut_activation", "cut_gradient", "lora_delta"}
ALLOWED_DTYPE = "<f4"
ALLOWED_BYTE_ORDER = "little"
ALLOWED_MEMORY_ORDER = "C"
MAX_PAYLOAD = 16 * 1024 * 1024  # 16MB
PROTOCOL_VERSION = "1.2.0-rc5.2"

def pack_tensor_envelope(tensor: torch.Tensor, role: str, unit_id: str, step_id: str = None) -> dict:
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Invalid tensor_role: {role}")
    t = tensor.detach().cpu().to(torch.float32).contiguous()
    data = t.numpy().tobytes()
    sha256 = hashlib.sha256(data).hexdigest()
    return {
        "tensor_role": role,
        "protocol_version": PROTOCOL_VERSION,
        "unit_id": unit_id,
        "step_id": step_id or "",
        "shape": list(t.shape),
        "dtype": ALLOWED_DTYPE,
        "byte_order": ALLOWED_BYTE_ORDER,
        "memory_order": ALLOWED_MEMORY_ORDER,
        "byte_length": len(data),
        "sha256": sha256,
        "data_b64": base64.b64encode(data).decode("ascii"),
    }

def unpack_tensor_envelope(env: dict) -> torch.Tensor:
    validate_envelope(env, env.get("tensor_role", ""), env.get("unit_id", ""), env.get("step_id", ""))
    raw = base64.b64decode(env["data_b64"], validate=True)
    if env["byte_length"] != len(raw):
        raise ValueError("byte_length mismatch")
    if hashlib.sha256(raw).hexdigest() != env["sha256"]:
        raise ValueError("SHA-256 mismatch")
    arr = torch.frombuffer(raw, dtype=torch.float32).reshape(env["shape"]).clone()
    if torch.isnan(arr).any() or torch.isinf(arr).any():
        raise ValueError("NaN/Inf in tensor")
    if len(raw) > MAX_PAYLOAD:
        raise ValueError("Payload too large")
    return arr

def validate_envelope(env: dict, expected_role: str, expected_unit_id: str, expected_step_id: str = None):
    if not isinstance(env, dict):
        raise ValueError("Envelope must be a dict")
    if env.get("tensor_role") != expected_role:
        raise ValueError(f"Wrong tensor_role: {env.get('tensor_role')} != {expected_role}")
    if env.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError(f"Wrong protocol_version: {env.get('protocol_version')}")
    if env.get("unit_id") != expected_unit_id:
        raise ValueError(f"Wrong unit_id: {env.get('unit_id')}")
    if expected_step_id and env.get("step_id") != expected_step_id:
        raise ValueError(f"Wrong step_id: {env.get('step_id')}")
    if env.get("dtype") != ALLOWED_DTYPE:
        raise ValueError(f"Wrong dtype: {env.get('dtype')}")
    if env.get("byte_order") != ALLOWED_BYTE_ORDER:
        raise ValueError(f"Wrong byte_order: {env.get('byte_order')}")
    if env.get("memory_order") != ALLOWED_MEMORY_ORDER:
        raise ValueError(f"Wrong memory_order: {env.get('memory_order')}")
    if not isinstance(env.get("shape"), list):
        raise ValueError("shape must be list")
    if not isinstance(env.get("data_b64"), str):
        raise ValueError("data_b64 must be string")
    bl = env.get("byte_length", 0)
    if bl <= 0: raise ValueError("byte_length must be positive")
    expected_bl = 4
    for d in env["shape"]: expected_bl *= d
    if bl != expected_bl: raise ValueError(f"byte_length {bl} != shape product {expected_bl}")
