# RC5.2 Protocol Tensors

## Tensor Envelope

Every tensor crossing the split boundary uses this JSON envelope:

```json
{
  "tensor_role": "<role>",
  "run_id": "string",
  "round_id": "string",
  "assignment_id": "string",
  "micro_unit_id": "string",
  "step_id": "string",
  "shape": [int, ...],
  "dtype": "float32",
  "byte_length": int,
  "sha256": "64-char hex",
  "encoding": "base64"
}
```

## Roles and Shapes

| Role | Shape | Content |
|---|---|---|
| server_activation | [1, T, D_cut] | Embedding output from server base layers |
| cut_activation | [1, T, D_cut] | Output of worker local layers + LoRA |
| cut_gradient | [1, T, D_cut] | Gradient of loss w.r.t. cut activation |
| lora_delta | varies | LoRA A/B weight delta (flattened or per layer) |

## Validation Order

1. tensor_role is known
2. Dimensions match expected for role
3. Each dimension is positive integer
4. dtype == "float32"
5. byte_length == product(shape) * 4
6. base64 decodes without error
7. Decoded bytes length == byte_length
8. sha256 == SHA256(decoded_bytes)
9. run_id matches active run
10. round_id matches active round
11. assignment_id matches unit's assignment
12. worker_id (from header) owns the unit
13. step_id is not replayed
