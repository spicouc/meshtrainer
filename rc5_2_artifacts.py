"""RC5.2 Phase 2 artifacts: worker_base_model_v1, base_adapter_v1."""
import torch, hashlib, json
from rc5_2_tensor_bundle import tensor_bundle_v1_pack, tensor_bundle_v1_unpack, bundle_sha256
from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash

WORKER_BASE_SCHEMA = ["local_layers.0", "local_layers.1"]
ADAPTER_KEYS = ["local_layers.0.linear1.lora_A.weight", "local_layers.0.linear1.lora_B.weight"]

def pack_worker_base_model(worker_model: torch.nn.Module) -> tuple:
    """Pack worker base model and return (bytes, hash)."""
    state = {}
    for n, p in worker_model.named_parameters():
        if "lora" not in n:
            state[n] = p.detach().cpu()
    data = tensor_bundle_v1_pack(state)
    return data, bundle_sha256(data)

def pack_base_adapter(worker_model: torch.nn.Module) -> tuple:
    """Pack base adapter (absolute LoRA weights)."""
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
    """Verify all worker artifacts. Returns dict with status per check."""
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
    # Verify base model unpack
    try:
        bm = tensor_bundle_v1_unpack(base_model_bytes, expected_base_hash)
        actual_keys = set(bm.keys())
        missing = set(WORKER_BASE_SCHEMA) - {k for k in actual_keys}
        if missing: result["ok"] = False; result["errors"].append(f"base_missing: {missing}")
    except Exception as e:
        result["ok"] = False; result["errors"].append(f"base_unpack: {e}")
    # Verify adapter unpack
    try:
        ad = tensor_bundle_v1_unpack(base_adapter_bytes, expected_adapter_hash)
        missing_ak = set(ADAPTER_KEYS) - set(ad.keys())
        unexpected_ak = set(ad.keys()) - set(ADAPTER_KEYS)
        if missing_ak: result["ok"] = False; result["errors"].append(f"adapter_missing: {missing_ak}")
        if unexpected_ak: result["ok"] = False; result["errors"].append(f"adapter_unexpected: {unexpected_ak}")
    except Exception as e:
        result["ok"] = False; result["errors"].append(f"adapter_unpack: {e}")
    return result
