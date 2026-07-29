# RC5.2 Protocol Tensors (R3)

## Single-Tensor Envelope

For activations and gradients (numerical_profile_v1):

```json
{
  "tensor_role": "server_activation | cut_activation | cut_gradient",
  "shape": [1, 128, 256],
  "dtype": "<f4",
  "byte_order": "little",
  "memory_order": "C",
  "byte_length": 131072,
  "sha256": "64-char hex",
  "data_b64": "...",
  "session_id": "...",
  "step_id": "..."
}
```

Validation:
1. shape == [1, 128, 256]
2. dtype == "<f4"
3. byte_order == "little" else REJECTED
4. memory_order == "C" else REJECTED
5. byte_length == 131072
6. base64 decode (strict) succeeds
7. decoded length == byte_length
8. SHA256(decoded) == sha256
9. No NaN/Inf
10. session_id matches
11. step_id not replayed
12. X-Worker-Id owns the unit

## Multi-Tensor Bundle (tensor_bundle_v1)

For LoRA delta transport:

```
Magic:       "MTB1" (4 bytes)
HeaderLen:   uint32 LE
Header:      canonical JSON (UTF-8, sort_keys=True, separators=(",",":"))
Payload:     concatenated tensors (LE f32, C-order, no padding)
```

SHA-256 over the entire bundle (magic + header_length + header + payload).

## Delta Transport

```yaml
delta_bundle_b64: base64 of tensor_bundle_v1
delta_bundle_byte_length: total bundle size
delta_bundle_sha256: SHA256(bundle_bytes)
adapter_schema_hash: SHA256(schema JSON)
```
