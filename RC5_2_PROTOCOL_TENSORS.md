# RC5.2 Protocol Tensors (R2)

## Envelope

```json
{
  "tensor_role": "server_activation | cut_activation | cut_gradient | lora_delta",
  "run_id": "string",
  "round_id": "string",
  "assignment_id": "string",
  "micro_unit_id": "string",
  "step_id": "string",
  "session_id": "string",
  "shape": [int, ...],
  "dtype": "float32",
  "byte_length": int,
  "sha256": "64-char hex",
  "data_b64": "base64-encoded bytes",
  "encoding": "base64"
}
```

## Roles, Shapes, and Limits

| Role | Shape | Max dims | Max bytes | Encoding |
|---|---|---|---|---|
| server_activation | [1, T, D_cut] = [1, 128, 256] | 3 | 100 MB | BE: little, order: C |
| cut_activation | [1, T, D_cut] = [1, 128, 256] | 3 | 100 MB | BE: little, order: C |
| cut_gradient | [1, T, D_cut] = [1, 128, 256] | 3 | 100 MB | BE: little, order: C |
| lora_delta | see delta schema (ADR-R2-06) | 2 | 10 MB | BE: little, order: C |

## Validation Order (hardened)

1. tensor_role is known
2. Dimensions count matches role's max
3. All dimensions are positive int
4. dtype == "float32"
5. byte_length == product(shape) * 4 (strict: no padding, no endianness tricks)
6. base64 decode (strict mode) succeeds
7. Decoded bytes length == byte_length
8. SHA256(decoded_bytes) == sha256 field
9. No NaN or Inf in decoded values
10. run_id matches active run
11. round_id matches active round
12. assignment_id matches unit's assignment
13. session_id matches unit's session
14. step_id not replayed
15. worker_id (X-Worker-Id header) owns the unit
16. Payload size ≤ role's max_bytes
