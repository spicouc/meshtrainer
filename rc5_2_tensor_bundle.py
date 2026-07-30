"""RC5.2 Phase 2 W3: tensor_bundle_v1 and artifacts."""
import json, hashlib, struct, io, torch
from rc5_2_canonical import canonical_json_v1, ADAPTER_SCHEMA, adapter_schema_hash

MAGIC = b"MTB1"
MAX_HEADER = 16384

def tensor_bundle_v1_pack(tensors: dict) -> bytes:
    """Pack named float32 tensors into tensor_bundle_v1 binary format."""
    names = sorted(tensors.keys())
    header = {"tensors": []}
    offset = 0
    for name in names:
        t = tensors[name]
        b = t.detach().cpu().to(torch.float32).contiguous().numpy().tobytes()
        header["tensors"].append({
            "name": name, "offset": offset, "byte_length": len(b),
            "shape": list(t.shape), "dtype": "<f4", "byte_order": "little",
            "memory_order": "C",
        })
        offset += len(b)
    header_json = canonical_json_v1(header)
    if len(header_json) > MAX_HEADER:
        raise ValueError(f"Header too large: {len(header_json)} > {MAX_HEADER}")
    hdr = struct.pack("<4sI", MAGIC, len(header_json))
    payload = b"".join(tensors[n].detach().cpu().to(torch.float32).contiguous().numpy().tobytes() for n in names)
    return hdr + header_json + payload

def tensor_bundle_v1_unpack(data: bytes) -> dict:
    """Unpack tensor_bundle_v1 bytes into named tensors."""
    magic, hdr_len = struct.unpack_from("<4sI", data, 0)
    if magic != MAGIC:
        raise ValueError(f"Bad magic: {magic}")
    if hdr_len > MAX_HEADER:
        raise ValueError(f"Header too large: {hdr_len}")
    hdr_end = 8 + hdr_len
    header = json.loads(data[8:hdr_end])
    payload = data[hdr_end:]
    tensors = {}
    prev_end = None
    for t in header["tensors"]:
        nm, off, blen = t["name"], t["offset"], t["byte_length"]
        if nm in tensors:
            raise ValueError(f"Duplicate tensor: {nm}")
        if off % 4 != 0:
            raise ValueError(f"Misaligned offset: {off}")
        if prev_end is not None and off < prev_end:
            raise ValueError(f"Overlapping: {nm}")
        end = off + blen
        if end > len(payload):
            raise ValueError(f"Truncated payload for {nm}")
        chunk = payload[off:end]
        tsz = blen // 4
        arr = torch.frombuffer(chunk, dtype=torch.float32).reshape(t["shape"])
        if torch.isnan(arr).any() or torch.isinf(arr).any():
            raise ValueError(f"NaN/Inf in {nm}")
        tensors[nm] = arr
        prev_end = end
    if prev_end is not None and prev_end < len(payload):
        raise ValueError(f"Trailing bytes: {len(payload) - prev_end}")
    return tensors

def delta_bundle_pack(delta_A: torch.Tensor, delta_B: torch.Tensor) -> bytes:
    return tensor_bundle_v1_pack({
        "local_layers.0.linear1.lora_A.weight": delta_A,
        "local_layers.0.linear1.lora_B.weight": delta_B,
    })

def delta_bundle_unpack(data: bytes) -> dict:
    return tensor_bundle_v1_unpack(data)

def worker_base_model_pack(local_layers_state: dict) -> bytes:
    """Pack worker base model as canonical bundle."""
    return tensor_bundle_v1_pack(local_layers_state)

def base_adapter_pack(adapter: dict) -> bytes:
    """Pack base adapter (absolute LoRA weights)."""
    return tensor_bundle_v1_pack(adapter)

def bundle_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def verify_delta_bundle(data: bytes, expected_sha: str) -> bool:
    return bundle_sha256(data) == expected_sha

def verify_adapter_schema(data: bytes, expected_schema_hash: str) -> bool:
    """Verify the adapter schema matches by checking the bundle header."""
    magic, hdr_len = struct.unpack_from("<4sI", data, 0)
    if magic != MAGIC: return False
    if hdr_len > MAX_HEADER: return False
    header = json.loads(data[8:8+hdr_len])
    names = tuple(t["name"] for t in header["tensors"])
    expected = tuple(sorted(ADAPTER_SCHEMA.keys()))
    return names == expected and adapter_schema_hash() == expected_schema_hash
