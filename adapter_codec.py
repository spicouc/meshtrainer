"""adapter_codec.py — CODEC GENERIC D'ADAPTERS (R2.1).

Serialització/deserialització de tensors d'adapter en un format independent
del model. El Coordinator/FedAvg treballa només amb:
  tensor name / shape / dtype / bytes / SHA

Format: header JSON (schema, tensors amb offset/byte_length/shape/dtype) +
        blobs binaris. Compatible amb el format propi qwen_bundle_v1 que
        usava Qwen3TrainingBackend._serialize_delta (mateixa estructura),
        però sense cap import de Qwen.
"""
import base64
import hashlib
import io
import json
import struct

import numpy as np

SCHEMA = "adapter_bundle_v1"


def bundle_sha256(data: bytes) -> str:
    """SHA-256 canònic d'un bundle (mateixa semàntica que rc5_2_tensor_bundle)."""
    return hashlib.sha256(data).hexdigest()


def pack_tensors(tensors: dict) -> bytes:
    """Serialitza un dict de tensors (torch.Tensor o np.ndarray) en bytes.

    Cada tensor es converteix a float32 contigu. Header JSON amb:
      schema, tensors: [{name, offset, byte_length, shape, dtype}]
    """
    names = sorted(tensors.keys())
    buf = io.BytesIO()
    header = {"schema": SCHEMA, "tensors": []}
    offset = 0
    for n in names:
        t = tensors[n]
        arr = _as_float32_array(t)
        b = arr.tobytes()
        header["tensors"].append({"name": n, "offset": offset,
                                  "byte_length": len(b),
                                  "shape": list(arr.shape),
                                  "dtype": "float32"})
        offset += len(b)
    hdr = json.dumps(header, separators=(",", ":")).encode("utf-8")
    out = struct.pack("<I", len(hdr)) + hdr
    for n in names:
        out += _as_float32_array(tensors[n]).tobytes()
    return out


def unpack_tensors(data: bytes) -> dict:
    """Deserialitza bytes en dict de tensors (torch.Tensor float32).

    Accepta schema 'adapter_bundle_v1' (nou) i 'qwen_bundle_v1' (antic,
    retrocompatibilitat amb bundles Qwen generats abans de R2.1).
    """
    hdr_len = struct.unpack_from("<I", data, 0)[0]
    header = json.loads(data[4:4 + hdr_len])
    if header.get("schema") not in (SCHEMA, "qwen_bundle_v1"):
        raise ValueError(f"schema no admès: {header.get('schema')}")
    out = {}
    for t in header["tensors"]:
        start = 4 + hdr_len + t["offset"]
        arr = np.frombuffer(data[start:start + t["byte_length"]],
                            dtype=np.float32).reshape(t["shape"])
        out[t["name"]] = _to_torch(arr.copy())
    return out


def _as_float32_array(t):
    """Converteix torch.Tensor o np.ndarray a np.ndarray float32 contigua."""
    if hasattr(t, "detach"):            # torch.Tensor
        t = t.detach().float().cpu().numpy()
    arr = np.asarray(t, dtype=np.float32)
    return np.ascontiguousarray(arr)


def _to_torch(arr: np.ndarray):
    """np.ndarray float32 -> torch.Tensor float32."""
    import torch
    return torch.as_tensor(arr, dtype=torch.float32)


# ── helpers per al Coordinator/FedAvg (sense torch al core) ─────────────
def to_numpy_dict(tensors: dict) -> dict:
    """dict de tensors -> dict de np.ndarray float32 (per al FedAvg)."""
    return {n: np.asarray(_as_float32_array(t)) for n, t in tensors.items()}


def numpy_dict_to_bundle(npd: dict) -> bytes:
    """dict de np.ndarray -> bytes de bundle (per serialitzar adapter_1)."""
    return pack_tensors(npd)


def tensors_sha256(tensors: dict) -> str:
    """SHA-256 canònic d'un dict de tensors (noms + bytes, ordenat)."""
    h = hashlib.sha256()
    for n in sorted(tensors.keys()):
        h.update(n.encode("utf-8"))
        h.update(_as_float32_array(tensors[n]).tobytes())
    return h.hexdigest()


def fedavg(adapter_0: dict, deltas_ett: list[tuple[dict, int]]) -> dict:
    """adapter_1 = adapter_0 + Σ(delta_i * ETT_i) / Σ ETT_i  (float64).

    Fórmula idèntica a aggregation.FedAvgLoRA._aggregate_real i a
    fedavg_qwen, però amb dicts de np.ndarray (model-agnostic).
    """
    total = sum(e for _, e in deltas_ett)
    if total <= 0:
        raise ValueError("Σ ETT = 0")
    names = sorted({n for d, _ in deltas_ett for n in d})
    acc = {n: np.zeros(np.asarray(adapter_0[n]).shape, dtype=np.float64)
           for n in names}
    for d, e in deltas_ett:
        for n in names:
            acc[n] += np.asarray(d[n], dtype=np.float64) * e
    return {n: (np.asarray(adapter_0[n], dtype=np.float64) + acc[n] / total)
            .astype(np.float32) for n in names}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(s: str) -> bytes:
    return base64.b64decode(s)
