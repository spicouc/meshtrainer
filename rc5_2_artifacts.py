"""RC5.2 Phase 2 R11: worker artifacts — full tensor names, no module names."""
import torch, hashlib, json
from rc5_2_tensor_bundle import tensor_bundle_v1_pack, tensor_bundle_v1_unpack, bundle_sha256
from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash

def _worker_base_keys():
    from rc5_2_numerical_models import WorkerNumericalModel
    w = WorkerNumericalModel()
    return [n for n, _ in w.named_parameters() if "lora" not in n]

# Full tensor names derived from the frozen schema (WorkerNumericalModel structure)
WORKER_BASE_SCHEMA = _worker_base_keys()
# (frozen-derived; kept as explicit list below for documentation)
_STATIC_WORKER_BASE_SCHEMA = [
    "local_layers.0.linear1.base_linear.weight",
    "local_layers.0.linear1.base_linear.bias",
    "local_layers.0.linear2.weight",
    "local_layers.0.linear2.bias",
    "local_layers.0.norm1.weight",
    "local_layers.0.norm1.bias",
    "local_layers.0.norm2.weight",
    "local_layers.0.norm2.bias",
    "local_layers.0.self_attn.in_proj_weight",
    "local_layers.0.self_attn.in_proj_bias",
    "local_layers.0.self_attn.out_proj.weight",
    "local_layers.0.self_attn.out_proj.bias",
    "local_layers.1.linear1.base_linear.weight",
    "local_layers.1.linear1.base_linear.bias",
    "local_layers.1.linear2.weight",
    "local_layers.1.linear2.bias",
    "local_layers.1.norm1.weight",
    "local_layers.1.norm1.bias",
    "local_layers.1.norm2.weight",
    "local_layers.1.norm2.bias",
    "local_layers.1.self_attn.in_proj_weight",
    "local_layers.1.self_attn.in_proj_bias",
    "local_layers.1.self_attn.out_proj.weight",
    "local_layers.1.self_attn.out_proj.bias",
]
ADAPTER_KEYS = ["local_layers.0.linear1.lora_A.weight", "local_layers.0.linear1.lora_B.weight"]

def pack_worker_base_model(worker_model: torch.nn.Module) -> tuple:
    """Pack worker base model (all non-LoRA tensors, full names)."""
    state = {}
    for n, p in worker_model.named_parameters():
        if "lora" not in n:
            state[n] = p.detach().cpu()
    data = tensor_bundle_v1_pack(state)
    return data, bundle_sha256(data)

def pack_base_adapter(worker_model: torch.nn.Module) -> tuple:
    """Pack base adapter: absolute LoRA A [8,256], B [1024,8]."""
    tensors = {
        "local_layers.0.linear1.lora_A.weight": worker_model.lora_wrapper.lora_A.weight.detach().cpu().clone(),
        "local_layers.0.linear1.lora_B.weight": worker_model.lora_wrapper.lora_B.weight.detach().cpu().clone(),
    }
    data = tensor_bundle_v1_pack(tensors)
    return data, bundle_sha256(data)

def verify_worker_artifacts(base_model_bytes: bytes, base_adapter_bytes: bytes,
                             expected_base_hash: str, expected_adapter_hash: str,
                             expected_partition_hash: str, expected_adapter_schema_hash: str,
                             expected_numerical_profile_hash: str) -> dict:
    """Verify all worker artifacts with exact key sets."""
    result = {"ok": True, "errors": []}
    if bundle_sha256(base_model_bytes) != expected_base_hash:
        result["ok"] = False; result["errors"].append("base_model_hash")
    if bundle_sha256(base_adapter_bytes) != expected_adapter_hash:
        result["ok"] = False; result["errors"].append("base_adapter_hash")
    if partition_schema_hash() != expected_partition_hash:
        result["ok"] = False; result["errors"].append("partition_schema_hash")
    if adapter_schema_hash() != expected_adapter_schema_hash:
        result["ok"] = False; result["errors"].append("adapter_schema_hash")
    if numerical_profile_hash() != expected_numerical_profile_hash:
        result["ok"] = False; result["errors"].append("numerical_profile_hash")
    try:
        bm = tensor_bundle_v1_unpack(base_model_bytes, expected_base_hash)
        actual = set(bm.keys())
        expected = set(WORKER_BASE_SCHEMA)
        missing = expected - actual
        unexpected = actual - expected
        if missing: result["ok"] = False; result["errors"].append(f"base_missing: {sorted(missing)}")
        if unexpected: result["ok"] = False; result["errors"].append(f"base_unexpected: {sorted(unexpected)}")
        if any("lora" in k for k in actual):
            result["ok"] = False; result["errors"].append("LoRA tensor in base model")
    except Exception as e:
        result["ok"] = False; result["errors"].append(f"base_unpack: {e}")
    try:
        ad = tensor_bundle_v1_unpack(base_adapter_bytes, expected_adapter_hash)
        missing_ak = set(ADAPTER_KEYS) - set(ad.keys())
        unexpected_ak = set(ad.keys()) - set(ADAPTER_KEYS)
        if missing_ak: result["ok"] = False; result["errors"].append(f"adapter_missing: {missing_ak}")
        if unexpected_ak: result["ok"] = False; result["errors"].append(f"adapter_unexpected: {unexpected_ak}")
        if list(ad["local_layers.0.linear1.lora_A.weight"].shape) != [8, 256]:
            result["ok"] = False; result["errors"].append("adapter_A shape")
        if list(ad["local_layers.0.linear1.lora_B.weight"].shape) != [1024, 8]:
            result["ok"] = False; result["errors"].append("adapter_B shape")
    except Exception as e:
        result["ok"] = False; result["errors"].append(f"adapter_unpack: {e}")
    return result
