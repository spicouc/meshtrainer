"""RC5.2 P1-01: Canonical numerical profile v1."""
import dataclasses
import json
import hashlib
from typing import List, Tuple

@dataclasses.dataclass(frozen=True)
class NumericalProfileV1:
    vocab_size: int = 512
    sequence_length: int = 128
    batch_size: int = 1
    d_model: int = 256
    nhead: int = 4
    dim_feedforward: int = 1024
    dropout: float = 0.0
    activation: str = "gelu"
    layer_norm_eps: float = 1e-5
    batch_first: bool = True
    norm_first: bool = False
    bias: bool = True
    base_layers: int = 2
    worker_layers: int = 2
    top_layers: int = 2
    lora_target: str = "local_layers.0.linear1"
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_scaling: float = 2.0
    dtype: str = "float32"
    device: str = "cpu"
    seed: int = 42

def canonical_json_v1(obj) -> bytes:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

def numerical_profile_hash(profile: NumericalProfileV1 = None) -> str:
    if profile is None:
        profile = NumericalProfileV1()
    d = dataclasses.asdict(profile)
    return hashlib.sha256(canonical_json_v1(d)).hexdigest()

PROFILE = NumericalProfileV1()
PROFILE_HASH = numerical_profile_hash(PROFILE)
ETT = PROFILE.sequence_length - 1  # 127 after shift
