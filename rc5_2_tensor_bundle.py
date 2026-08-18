"""RC5.2 Phase 2 R2: Hardened tensor_bundle_v1 with strict validation."""
import json, hashlib, struct, torch, io, base64
from rc5_2_canonical import canonical_json_v1

MAGIC = b"MTB1"
MAX_HEADER = 16384
ALLOWED_DTYPE = "<f4"
ALLOWED_BYTE_ORDER = "little"
ALLOWED_MEMORY_ORDER = "C"
DELTA_NAMES = {"local_layers.0.linear1.lora_A.weight", "local_layers.0.linear1.lora_B.weight"}

def tensor_bundle_v1_pack(tensors: dict) -> bytes:
    names = sorted(tensors.keys())
    header = {"schema_version": "tensor_bundle_v1", "tensors": []}
    offset = 0
    prev_name = None
    for name in names:
        t = tensors[name]
        if not isinstance(t, torch.Tensor) or t.dtype != torch.float32:
            raise ValueError(f"Only float32 tensors supported: {name}")
        b = t.detach().cpu().contiguous().numpy().tobytes()
        header["tensors"].append({
            "name": name, "offset": offset, "byte_length": len(b),
            "shape": list(t.shape), "dtype": ALLOWED_DTYPE,
            "byte_order": ALLOWED_BYTE_ORDER, "memory_order": ALLOWED_MEMORY_ORDER,
        })
        offset += len(b)
    hdr = canonical_json_v1(header)
    if len(hdr) > MAX_HEADER: raise ValueError(f"Header too large: {len(hdr)}")
    hdr_bin = struct.pack("<4sI", MAGIC, len(hdr))
    payload = b"".join(
        tensors[n].detach().cpu().contiguous().numpy().tobytes() for n in names
    )
    return hdr_bin + hdr + payload

def tensor_bundle_v1_unpack(data: bytes, expected_sha: str = None) -> dict:
    if expected_sha and hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError("SHA-256 mismatch")
    magic, hdr_len = struct.unpack_from("<4sI", data, 0)
    if magic != MAGIC: raise ValueError(f"Bad magic: {magic!r}")
    if hdr_len > MAX_HEADER: raise ValueError(f"Header too large: {hdr_len}")
    if hdr_len < 1: raise ValueError("Empty header")
    hdr_end = 8 + hdr_len
    if hdr_end > len(data): raise ValueError("Truncated header")
    header = json.loads(data[8:hdr_end])
    if not isinstance(header, dict): raise ValueError("Header must be JSON object")
    tensors_list = header.get("tensors", [])
    if not isinstance(tensors_list, list): raise ValueError("tensors must be array")
    payload = data[hdr_end:]
    result = {}
    prev_end = None
    seen = set()
    for t in tensors_list:
        nm, off, blen = t["name"], t["offset"], t["byte_length"]
        if nm in seen: raise ValueError(f"Duplicate: {nm}")
        seen.add(nm)
        if off < 0: raise ValueError(f"Negative offset: {nm}")
        if off % 4 != 0: raise ValueError(f"Misaligned offset: {nm}")
        if prev_end is not None and off < prev_end: raise ValueError(f"Overlap: {nm}")
        if prev_end is not None and off > prev_end: raise ValueError(f"Gap before: {nm}")
        end = off + blen
        if end > len(payload): raise ValueError(f"Truncated: {nm}")
        if t.get("dtype") != ALLOWED_DTYPE: raise ValueError(f"Wrong dtype: {nm}")
        if t.get("byte_order") != ALLOWED_BYTE_ORDER: raise ValueError(f"Wrong byte_order: {nm}")
        if t.get("memory_order") != ALLOWED_MEMORY_ORDER: raise ValueError(f"Wrong memory_order: {nm}")
        if blen % 4 != 0: raise ValueError(f"byte_length not multiple of 4: {nm}")
        expected_blen = 4
        for d in t.get("shape", []): expected_blen *= d
        if blen != expected_blen: raise ValueError(f"shape*4 != byte_length: {nm}")
        chunk = payload[off:end]
        arr = torch.frombuffer(chunk, dtype=torch.float32).reshape(t["shape"]).clone()
        if torch.isnan(arr).any() or torch.isinf(arr).any():
            raise ValueError(f"NaN/Inf in {nm}")
        result[nm] = arr
        prev_end = end
    if prev_end is not None and prev_end < len(payload):
        raise ValueError(f"Trailing bytes: {len(payload) - prev_end}")
    return result

def delta_bundle_pack(delta_A, delta_B) -> bytes:
    return tensor_bundle_v1_pack({
        "local_layers.0.linear1.lora_A.weight": delta_A,
        "local_layers.0.linear1.lora_B.weight": delta_B,
    })

def delta_bundle_unpack(data: bytes, expected_sha: str = None) -> dict:
    tensors = tensor_bundle_v1_unpack(data, expected_sha)
    if set(tensors.keys()) != DELTA_NAMES:
        raise ValueError(f"Delta must have exactly {sorted(DELTA_NAMES)} tensors")
    for nm in ["local_layers.0.linear1.lora_A.weight", "local_layers.0.linear1.lora_B.weight"]:
        if nm not in tensors: raise ValueError(f"Missing: {nm}")
        expected = [8,256] if "A" in nm else [1024,8]
        if list(tensors[nm].shape) != expected:
            raise ValueError(f"Wrong shape for {nm}: {tensors[nm].shape}")
    return tensors

def bundle_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_adapter_schema(bundle_data: bytes, expected_schema_hash: str) -> bool:
    """Verify the bundle header names match the adapter schema and hash."""
    from rc5_2_canonical import adapter_schema_hash
    try:
        hdr_len = struct.unpack_from("<I", bundle_data, 4)[0]
        header = json.loads(bundle_data[8:8+hdr_len])
        names = tuple(t["name"] for t in header.get("tensors", []))
        expected = ("local_layers.0.linear1.lora_A.weight", "local_layers.0.linear1.lora_B.weight")
        return names == expected and adapter_schema_hash() == expected_schema_hash
    except Exception:
        return False
