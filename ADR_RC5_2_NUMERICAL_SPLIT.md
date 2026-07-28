# RC5.2-ADR-R2 — Numerical Split-Learning Architecture Decision Record

**Status:** Proposed (R2)
**Author:** Hermes Agent (Executor)
**Date:** 2026-07-28
**Base tag:** `meshtrainer-v1.1-rc5.1` (commit `406089b`)
**Functional baseline:** commit `9b9aac1`
**ADR R1:** commit `476d6a0`

---

## ADR-R2-01 — Numerical Profile v1 (ACCEPTED)

```yaml
numerical_profile_v1:
  vocab_size: 50272
  sequence_length: 128
  batch_size: 1
  d_model: 256
  d_cut: 256  # partition point dimension
  base_layers: 2 x TransformerBlock(d_model=256, nhead=4)
  local_layers: 2 x TransformerBlock(d_model=256, nhead=4)
  top_layers: 2 x TransformerBlock(d_model=256, nhead=4)
  cut_entry_point: after base_layers[1]
  cut_exit_point: after local_layers[1]
  lora_target_module: local_layers[0].linear1  # nn.Linear(256, 1024)
  rank: 8
  alpha: 16
  scaling: 2.0  # alpha / rank
  dtype: float32
  seed: 42
```

LoRA attaches to `local_layers[0].linear1`:
```python
base_output = local_layers[0].linear1(x)
lora_output = scaling * self.lora_B(self.lora_A(x))
output = base_output + lora_output
```

---

## ADR-R2-02 — Topology and Directions (ACCEPTED)

### Direction map

| Tensor | From | To | RPC | Direction |
|---|---|---|---|---|
| server_activation | Split Server | Worker | step.server_forward.response.body | Server → Worker (HTTP response body) |
| cut_activation | Worker | Split Server | step.worker_forward.submit | Worker → Server (HTTP POST body) |
| cut_gradient | Split Server | Worker | step.server_backward.fetch.response.body | Server → Worker (HTTP response body) |
| lora_delta | Worker | Split Server | step.worker_update.submit | Worker → Server (HTTP POST body) |
| receipt | Split Server | Worker | step.worker_update.submit.response.body | Server → Worker (HTTP response body) |
| contribution | Worker | Coordinator | checkpoint.upload | Worker → Coordinator (HTTP POST) |

### Transport model

Worker-driven HTTP JSON-RPC. The worker initiates every request.
The Split Server never initiates connections.
No callbacks, no server-side push in RC5.2.

---

## ADR-R2-03 — Versioned Methods (ACCEPTED)

```yaml
protocol_version: 1.2.0-rc5.2
```

### Methods

| Method | Direction | Description |
|---|---|---|
| step.open | Worker → Server | Open micro-unit, receive server_activation |
| step.server_forward | Worker → Server | (same as step.open — merged) |
| step.worker_forward.submit | Worker → Server | Submit forward result, receive loss |
| step.server_backward.fetch | Worker → Server | Fetch cut gradient |
| step.worker_update.submit | Worker → Server | Submit delta, receive receipt |
| step.commit | Worker → Server | Same as R1 — finalize |
| step.abort | Worker → Server | Abort unit |

### RC5.1 backward compatibility

RC5.1 handlers remain available under protocol `1.1.0-rc5.1`.
The Split Server routes by `protocol_version` from the first request.
Handlers `step.embedding`, `step.cut_activation`, `step.cut_gradient` (RC5.1)
continue to work for protocol 1.1.0 requests.

---

## ADR-R2-04 — State Machine (ACCEPTED)

```
OPEN
  │ step.open (server computes activation)  
  ▼
SERVER_ACTIVATION_READY
  │ step.worker_forward.submit (worker sends cut activation)
  ▼
WORKER_ACTIVATION_ACCEPTED
  │ (server computes loss + backward, gradient ready to fetch)
  ▼
CUT_GRADIENT_READY
  │ step.server_backward.fetch (worker fetches cut gradient)
  ▼
WORKER_UPDATE_ACCEPTED
  │ (worker applies optimizer exactly once, presents delta)
  ▼
DELTA_VERIFIED
  │ step.commit (server validates delta, signs receipt)
  ▼
COMMITTED
```

Terminal states: `COMMITTED`, `ABORTED`, `EXPIRED`

### Idempotency

| Method | Idempotent | Notes |
|---|---|---|
| step.open | No | Fresh unit required |
| step.worker_forward.submit | Yes (same activations) | update_id prevents replay |
| step.server_backward.fetch | Yes | Returns same gradient |
| step.worker_update.submit | No | update_id prevents double optimizer step |
| step.commit | Yes | Returns same receipt |
| step.abort | Yes | Already aborted |

The `update_id` is a unique client-generated nonce for `step.worker_update.submit`.
If the server receives the same `update_id` twice, it returns the existing receipt
without re-applying the optimizer.

---

## ADR-R2-05 — Weight Artifacts (ACCEPTED)

Two separate artifacts are defined:

### worker_base_model artifact
```yaml
worker_model_hash: SHA256(worker_base_model.npz)
partition_schema_hash: SHA256(layer_names + shapes + dtypes)
model_version: "1.2.0-rc5.2"
```

### base_adapter artifact
```yaml
base_adapter_hash: SHA256(base_adapter.npz)
adapter_schema_hash: SHA256(lora_layer_names + shapes + dtypes)
```

Validated by: Coordinator and Split Server independently.
`base_adapter_hash` is NOT used to verify `local_layers`.

---

## ADR-R2-06 — Delta Schema (ACCEPTED)

```yaml
delta_keys:
  - local_layer.lora_A.weight
  - local_layer.lora_B.weight

shape:
  local_layer.lora_A.weight: [d_cut, rank]   # [256, 8]
  local_layer.lora_B.weight: [rank, d_cut]   # [8, 256]

encoding: little-endian float32, C-contiguous
name_encoding: UTF-8
sort_order: lexicographic by key
```

FedAvg aggregates A and B separately preserving the schema.

---

## ADR-R2-07 — Task and Loss (ACCEPTED)

Causal Language Modelling:

```python
shift_logits = logits[:, :-1, :].contiguous()
shift_labels = labels[:, 1:].contiguous()

loss_fn = torch.nn.CrossEntropyLoss(ignore_index=-100)
loss = loss_fn(shift_logits.view(-1, vocab_size), shift_labels.view(-1))

ETT = (shift_labels != -100).sum().item()
```

Deterministic synthetic fixture for tests:
```python
tokens = torch.randint(0, vocab_size-1, (batch_size, sequence_length))
labels = tokens.clone()
labels[:, :-1] = -100  # causal masking for test
```

---

## ADR-R2-08 — Optimizer and Resume (ACCEPTED)

```yaml
optimizer: torch.optim.SGD
learning_rate: 0.01
momentum: 0.0
weight_decay: 0.0
params: [lora_A.weight, lora_B.weight]  # ONLY LoRA
```

N-SPLIT-16 (resume) limited to:
- Resume after a step reaches COMMITTED
- Mid-step resume deferred to RC5.4

---

## ADR-R2-09 — Tensor Envelope (ACCEPTED)

```json
{
  "tensor_role": "server_activation | cut_activation | cut_gradient | lora_delta",
  "run_id": "...",
  "round_id": "...",
  "assignment_id": "...",
  "micro_unit_id": "...",
  "step_id": "...",
  "session_id": "...",
  "shape": [int, ...],
  "dtype": "float32",
  "byte_length": int,
  "sha256": "64-char hex",
  "data_b64": "base64-encoded bytes",
  "encoding": "base64"
}
```

Validation rules:
1. role known
2. dimensions match role's expected shape
3. dtype == "float32"
4. byte_length == product(shape) * 4
5. base64 decodes without error (strict mode)
6. decoded bytes length == byte_length
7. SHA256(decoded_bytes) == sha256 field
8. No NaN or Inf in decoded values
9. Maximum dimensions: 3 for activations, 2 for LoRA
10. Maximum byte_length: 100 MB for activations, 10 MB for LoRA
11. session_id matches
12. step_id not replayed
13. ownership matches X-Worker-Id header

---

## ADR-R2-10 — Receipt and Contribution Sequence (ACCEPTED)

1. Worker presents delta + delta_sha256 to Split Server via `step.worker_update.submit`
2. Split Server validates: bytes, schema, profile, step, SHA, NaN/Inf
3. Split Server generates receipt with HMAC
4. Worker receives receipt
5. Worker sends delta + receipt to Coordinator via `checkpoint.upload`
6. Coordinator repeats RC5.1 validations

The receipt certifies protocol integrity and step completion.
The receipt does NOT certify numerical honesty of a malicious worker.
This limitation is documented.

---

## ADR-R2-11 — Test Plan (ACCEPTED)

N-SPLIT-09 must validate:
```python
assert set(optimizer.param_groups[0]['params']) == {lora_A.weight, lora_B.weight}
assert all(not p.requires_grad for p in base_params)
assert all(p.grad is None for p in base_params)
assert base_state_dict_before == base_state_dict_after
```

Additional tests:
- protocol_version mismatch → REJECTED
- worker_model_hash mismatch → REJECTED
- partition_schema_hash mismatch → REJECTED
- NaN/Inf in tensor → REJECTED
- wrong endianness → REJECTED (byte_length mismatch)
- duplicate update_id → returns existing receipt, no double optimizer

Full N-SPLIT + MUT-N matrix: see RC5_2_NUMERICAL_TEST_PLAN.md.

---

## ADR-R2-12 — Risk Register (ACCEPTED)

See separate RC5_2_RISK_REGISTER.md for expanded register.

Key additions:
- Worker returns arbitrary delta → mitigated by SHA-256 + receipt (RC5.2)
- Worker model incorrect → mitigated by worker_model_hash (RC5.2)
- Double optimizer step → mitigated by update_id (RC5.2)
- Gradient replay → mitigated by step_id + nonce (RC5.2)
- Protocol version collision → mitigated by routing (RC5.2)
- Canonical serialization mismatch → mitigated by strict byte_length check (RC5.2)
- Crash after optimizer, before commit → NOT mitigated in RC5.2 (deferred to RC5.4)
- Activation inversion / model extraction → documented, NOT mitigated in RC5.2

---

## ADR-R2-13 — Weight Ownership and Artifact Distribution (ACCEPTED)

| Weight | Artifact | Owner | Verified by |
|---|---|---|---|
| embedding | model.npz | Server | model_hash |
| base_layers | model.npz | Server | model_hash |
| local_layers | worker_base_model.npz | Worker (copy) | worker_model_hash |
| LoRA A/B | base_adapter.npz | Worker | base_adapter_hash |
| top_layers | model.npz | Server | model_hash |
| LM head | model.npz | Server | model_hash |

The Coordinator validates both `worker_model_hash` and `base_adapter_hash` before round start.

---

## Decisions Not Yet Made

Deferred beyond RC5.2:
- Multiple workers per round (RC5.3)
- Mid-step resume (RC5.4)
- WebGPU (RC5.5)
- Mixed precision
- Gradient inversion mitigations
- Calibration protocol
