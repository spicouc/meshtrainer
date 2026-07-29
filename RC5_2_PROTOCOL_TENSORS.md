# RC5.2 Protocol Tensors (R4)

## tensor_bundle_v1 — Frozen Binary Format

```
┌───────────────────────────────────────────────┐
│ Offset  Size  Field                           │
├───────────────────────────────────────────────┤
│ 0       4     magic "MTB1" (ASCII)            │
│ 4       4     header_length (uint32 LE)       │
│ 8       H     header (canonical JSON, max 16K)│
│ 8+H     P     payload (LE f32, C-order)       │
└───────────────────────────────────────────────┘
```

### Header (canonical JSON, sorted keys)

```json
{
  "schema_version": "tensor_bundle_v1",
  "tensors": [
    {
      "name": "local_layers.0.linear1.lora_A.weight",
      "dtype": "<f4",
      "shape": [8, 256],
      "offset": 0,
      "byte_length": 8192
    },
    {
      "name": "local_layers.0.linear1.lora_B.weight",
      "dtype": "<f4",
      "shape": [1024, 8],
      "offset": 8192,
      "byte_length": 32768
    }
  ]
}
```

### Rules
- Exactly 2 tensors, sorted by name
- Unknown name → REJECTED
- Missing tensor → REJECTED
- Duplicate name → REJECTED
- Offsets consecutive, no overlap, no trailing bytes
- `<f4`, C-contiguous
- NaN/Inf → REJECTED
- SHA-256 over the entire bundle (magic + header_length + header + payload)

### Bundle length
8 + header_length + sum(byte_length)

## Single-Tensor Envelope (activations, gradients)

```json
{
  "shape": [1, 128, 256],
  "dtype": "<f4",
  "byte_order": "little",
  "memory_order": "C",
  "byte_length": 131072,
  "sha256": "...",
  "data_b64": "...",
  "session_id": "...",
  "step_id": "..."
}
```

Validation:
1. shape == [1, 128, 256]
2. dtype == "<f4"
3. byte_order == "little" → else REJECTED
4. memory_order == "C" → else REJECTED
5. byte_length == 131072
6. base64 (strict) decode OK
7. decoded length matches
8. SHA256 matches
9. No NaN/Inf
10. session_id matches
11. step_id not replayed
12. X-Worker-Id owns
