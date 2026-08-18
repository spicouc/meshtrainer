"""RC5.2 Phase 2 W2: Canonical JSON v1 and schema hashes."""
import json, hashlib
from rc5_2_numerical_profile import PROFILE, canonical_json_v1 as _profile_cj

def canonical_json_v1(obj: dict) -> bytes:
    """Deterministic JSON: sorted keys, compact separators, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def sha256_of(obj: dict) -> str:
    return hashlib.sha256(canonical_json_v1(obj)).hexdigest()

def numerical_profile_hash() -> str:
    return sha256_of({
        "vocab_size": PROFILE.vocab_size, "sequence_length": PROFILE.sequence_length,
        "batch_size": PROFILE.batch_size, "d_model": PROFILE.d_model,
        "nhead": PROFILE.nhead, "dim_feedforward": PROFILE.dim_feedforward,
        "dropout": PROFILE.dropout, "activation": PROFILE.activation,
        "layer_norm_eps": PROFILE.layer_norm_eps, "batch_first": PROFILE.batch_first,
        "norm_first": PROFILE.norm_first, "bias": PROFILE.bias,
        "base_layers": PROFILE.base_layers, "worker_layers": PROFILE.worker_layers,
        "top_layers": PROFILE.top_layers,
        "lora_rank": PROFILE.lora_rank, "lora_alpha": PROFILE.lora_alpha,
        "lora_scaling": PROFILE.lora_scaling,
        "dtype": str(PROFILE.dtype), "device": str(PROFILE.device), "seed": PROFILE.seed,
    })

PARTITION_SCHEMA_SERVER = {
    "server": ["token_embedding", "positional_embedding",
               "base_layers.0", "base_layers.1",
               "top_layers.0", "top_layers.1", "lm_head"]
}
PARTITION_SCHEMA_WORKER = {
    "worker": ["local_layers.0", "local_layers.1",
               "local_layers.0.linear1.lora_A.weight",
               "local_layers.0.linear1.lora_B.weight"]
}

def partition_schema_hash() -> str:
    return sha256_of({"partition_schema_v1": {
        "server": PARTITION_SCHEMA_SERVER["server"],
        "worker": PARTITION_SCHEMA_WORKER["worker"],
    }})

ADAPTER_SCHEMA = {
    "local_layers.0.linear1.lora_A.weight": {"dtype": "<f4", "shape": [8, 256]},
    "local_layers.0.linear1.lora_B.weight": {"dtype": "<f4", "shape": [1024, 8]},
}

def adapter_schema_hash() -> str:
    return sha256_of({"adapter_schema_v1": ADAPTER_SCHEMA})
