# RC5.2-ADR-R3 FINAL — Numerical Split-Learning Architecture

**Status:** Proposed (R3 FINAL)
**Base tag:** `meshtrainer-v1.1-rc5.1` (commit `406089b`)
**ADR R1:** commit `476d6a0`
**ADR R2:** commit `cf236cc`

---

## R3-01 — Numerical Profile v1 (frozen)

```yaml
numerical_profile_v1:
  vocab_size: 512
  sequence_length: 128
  batch_size: 1
  d_model: 256
  d_cut: 256
  positional_embedding: learned, shape [128, 256], owned by server
  causal_attention_mask: mandatory on all Transformer layers
  padding: absent
  attention_mask: all tokens active
  transformer_block:
    class: torch.nn.TransformerEncoderLayer
    params:
      d_model: 256
      nhead: 4
      dim_feedforward: 1024
      dropout: 0.0
      activation: gelu
      layer_norm_eps: 1e-5
      batch_first: true
      norm_first: false
      bias: true
  layer_counts:
    base_layers: 2
    worker_layers: 2
    top_layers: 2
  cut_entry_point: after base_layers[1]
  cut_exit_point: after worker_layers[1]
```

Justificacio vocab_size=512: mes petit que 50272, suficient per demostrar
equivalencia numerica split vs monolithic en CPU, gate mes rapid.

---

## R3-02 — LoRA Schema (frozen)

```yaml
target_module: local_layers[0].linear1  # nn.Linear(256, 1024)
tensors:
  local_layers.0.linear1.lora_A.weight: {shape: [8, 256], dtype: float32}
  local_layers.0.linear1.lora_B.weight: {shape: [1024, 8], dtype: float32}
rank: 8
alpha: 16
scaling: 2.0
bias: false
```

Implementation:
```python
base_output = linear1(x)                             # Linear(256, 1024)
lora_output = scaling * lora_B(lora_A(x))             # A: [8,256], B: [1024,8]
output = base_output + lora_output
```

FedAvg aggregates A and B separately preserving the schema.

---

## R3-03 — Optimizer Responsibility (frozen)

```yaml
optimizer.location: worker ONLY
optimizer.class: torch.optim.SGD
learning_rate: 0.01
momentum: 0.0
weight_decay: 0.0
params: [lora_A.weight, lora_B.weight]  # LoRA ONLY
split_server: NEVER executes optimizer.step()
```

Worker-side update journal:
```python
journal_entry = {
    "update_id": "...",           # unique nonce
    "pre_adapter_hash": "...",    # SHA before optimizer
    "post_adapter_hash": "...",   # SHA after optimizer
    "delta_sha256": "...",        # SHA of delta
    "delta_bundle": "...",        # base64 of tensor_bundle_v1
    "status": "APPLIED",          # APPLIED | PENDING
}
```

Sequence:
1. Generate update_id
2. Check not already APPLIED
3. Execute optimizer.step() exactly once
4. Compute and store delta
5. Mark update_id as APPLIED
6. Reuse same delta on network retries

Crash window between optimizer and persist: deferred to RC5.4.

---

## R3-04 — RPC Methods (frozen)

```yaml
protocol_version: 1.2.0-rc5.2
```

| Method | Body sent | Response | Idempotent |
|---|---|---|---|
| `step.open` | assignment_id, micro_unit_id, session_id | server_activation bundle | yes (returns same activation for same unit) |
| `step.worker_forward.submit` | cut_activation bundle | loss (scalar), status | yes (same input → same output) |
| `step.server_backward.fetch` | — | cut_gradient bundle | yes (returns same gradient) |
| `step.worker_update.submit` | update_id, delta_bundle, delta_sha256 | delta_id, status="DELTA_VERIFIED" | yes (same update_id+sha → same delta_id) |
| `step.commit` | delta_id | receipt (signed HMAC) | yes (returns same receipt) |
| `step.abort` | — | status="ABORTED" | yes |

Key points:
- `step.open` includes server forward (no separate `step.server_forward`)
- `step.worker_forward.submit` returns loss only, NOT gradient
- `step.worker_update.submit` validates delta, returns delta_id, does NOT generate receipt
- `step.commit` generates and signs the receipt
- Same update_id + same SHA → same delta_id
- Same update_id + different SHA → REJECTED

RC5.1 handlers (protocol 1.1.0-rc5.1) remain available.

---

## R3-05 — State Machine (frozen)

```
OPEN
  │ step.open (server computes activation)
  ▼
SERVER_ACTIVATION_READY
  │ step.worker_forward.submit (worker sends cut_activation)
  ▼
CUT_ACTIVATION_ACCEPTED
  │ (server computes loss + backward internally)
  ▼
CUT_GRADIENT_READY
  │ step.server_backward.fetch (worker fetches gradient)
  ▼
CUT_GRADIENT_DELIVERED
  │ step.worker_update.submit (worker presents delta)
  ▼
DELTA_VERIFIED
  │ step.commit (server signs receipt)
  ▼
COMMITTED
```

Terminals: `ABORTED`, `EXPIRED`

### Transitions
| From | To | Trigger |
|---|---|---|
| OPEN | SERVER_ACTIVATION_READY | step.open |
| SERVER_ACTIVATION_READY | CUT_ACTIVATION_ACCEPTED | step.worker_forward.submit |
| CUT_ACTIVATION_ACCEPTED | CUT_GRADIENT_READY | server backward (internal) |
| CUT_GRADIENT_READY | CUT_GRADIENT_DELIVERED | step.server_backward.fetch (first call) |
| CUT_GRADIENT_DELIVERED | DELTA_VERIFIED | step.worker_update.submit |
| DELTA_VERIFIED | COMMITTED | step.commit |
| any | ABORTED | step.abort |

No state implies server-side optimizer execution.

---

## R3-06 — Tensor Bundle (tensor_bundle_v1)

Binary format for multi-tensor transport.

```
┌──────────────────────────────┐
│ magic: "MTB1" (4 bytes)      │
│ header_length: uint32 LE      │
│ header: canonical JSON (UTF-8)│
│ payload: concatenated tensors │
└──────────────────────────────┘
```

Header:
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

Rules:
- Keys sorted lexicographically
- JSON separators=(",",":"), sort_keys=True
- float32 little-endian, C-contiguous
- No padding
- Consecutive offsets
- SHA-256 over the entire bundle (magic + header_length + header + payload)

Delta transport:
```yaml
delta_bundle_b64: base64 of tensor_bundle_v1
delta_bundle_byte_length: total bundle size
delta_bundle_sha256: SHA256(bundle_bytes)
adapter_schema_hash: SHA256(schema JSON)
```

The Coordinator supports both bundle RC5.2 and NPZ RC5.1.

---

## R3-07 — Tensor Envelope (frozen)

```json
{
  "tensor_role": "server_activation | cut_activation | cut_gradient",
  "run_id": "...",
  "round_id": "...",
  "assignment_id": "...",
  "micro_unit_id": "...",
  "step_id": "...",
  "session_id": "...",
  "shape": [1, 128, 256],
  "dtype": "<f4",
  "byte_order": "little",
  "memory_order": "C",
  "byte_length": 131072,
  "sha256": "64-char hex",
  "data_b64": "base64-encoded bytes",
  "encoding": "base64"
}
```

Validation:
1. role known
2. shape == [1, 128, 256] (for numerical_profile_v1)
3. dtype == "<f4"
4. byte_order == "little" — if not → REJECTED
5. memory_order == "C" — if not → REJECTED
6. byte_length == product(shape) * 4 == 131072
7. base64 decode succeeds (strict mode)
8. decoded length == byte_length
9. SHA256(decoded) == sha256
10. No NaN/Inf in decoded values
11. session_id matches
12. step_id not replayed
13. ownership matches X-Worker-Id

---

## R3-08 — Causal LM Fixture (frozen)

```python
def deterministic_fixture(seed=42):
    torch.manual_seed(seed)
    tokens = torch.randint(0, 511, (1, 128))  # vocab_size=512
    labels = tokens.clone()                     # no padding in profile v1
    return tokens, labels

# After internal causal shift:
shift_logits = logits[:, :-1, :].contiguous()   # [1, 127, 512]
shift_labels = labels[:, 1:].contiguous()        # [1, 127]
loss = CrossEntropyLoss(ignore_index=-100)(
    shift_logits.view(-1, 512), shift_labels.view(-1)
)
ETT = 127  # all shift_labels are != -100

# Causal attention mask applied to ALL Transformer layers
causal_mask = torch.triu(torch.full((128, 128), float('-inf')), diagonal=1)
```

---

## R3-09 — Determinism (frozen)

```python
import random, numpy as np, torch
random.seed(42)
np.random.seed(42)
torch.manual_seed(42)
torch.set_num_threads(1)
torch.use_deterministic_algorithms(True)

# Profile
device = "cpu"
dtype = torch.float32
dropout = 0.0
optimizer = torch.optim.SGD(lora_params, lr=0.01, momentum=0.0, weight_decay=0.0)

# Tolerances
rtol = 1e-5
atol = 1e-6
```

Exact byte comparison only for:
- Loaded artifacts (state dict)
- Canonical serialization (tensor_bundle_v1)
- SHA hashes
- Deterministic repeats

---

## R3-10 — N-SPLIT-09 Assertions (frozen)

```python
# optimizer params == LoRA params only
optimizer_ids = {id(p) for group in optimizer.param_groups for p in group["params"]}
lora_ids = {id(lora_A.weight), id(lora_B.weight)}
assert optimizer_ids == lora_ids

# base weights: no grad, no change
assert all(not p.requires_grad for p in base_params)
assert all(p.grad is None for p in base_params)

# base state dict unchanged
base_before = {k: v.clone() for k, v in base_model.state_dict().items()}
# ... step ...
base_after = base_model.state_dict()
assert base_before.keys() == base_after.keys()
for k in base_before:
    assert torch.equal(base_before[k], base_after[k])
```

---

## R3-11 — Threat Model (corrected)

| Risk | Mitigation (RC5.2) | Status |
|---|---|---|
| Worker sends arbitrary delta | SHA + HMAC only prove byte integrity and protocol binding. Do NOT prove numerical honesty. | NOT MITIGATED — deferred to RC5.6 |
| Worker model incorrect | worker_model_hash links assignment to artifact. Without remote attestation, does NOT prove execution. | DOCUMENTED — not mitigated |
| Double optimizer step | Worker-side update journal (not just server-side update_id) | MITIGATED |
| Canonical serialization mismatch | tensor_bundle_v1 with strict byte_length, order, endianness | MITIGATED |
| Activations leak input patterns | Documented trust boundary | NOT MITIGATED — deferred to RC5.6 |
| Crash after optimizer, before persist | Window between optimizer.step() and journal write | NOT MITIGATED — deferred to RC5.4 |

---

## R3-12 — Receipt v1.2

Additional receipt fields for RC5.2:

```json
{
  "protocol_version": "1.2.0-rc5.2",
  "worker_model_hash": "sha256:...",
  "partition_schema_hash": "sha256:...",
  "base_adapter_hash": "sha256:...",
  "adapter_schema_hash": "sha256:...",
  "loss_definition_hash": "sha256:...",
  "numerical_profile_hash": "sha256:...",
  "update_id": "...",
  "delta_bundle_sha256": "...",
  "effective_trainable_tokens": 127
}
```

`numerical_profile_hash` = SHA256 of canonical JSON of `numerical_profile_v1` YAML.

---

## R3-13 — Performance Budget

| Component | Estimat |
|---|---|
| Forward pass (6 Transformer blocks, d_model=256) | ~5 ms |
| Backward pass | ~10 ms |
| Per test (16 fwd+bwd) | ~240 ms |
| 16 numerical tests | ~4 s |
| 6 extra tests | ~1 s |
| 76 regression tests | ~10 s |
| Per run total | ~15 s |
| Per mutant | ~15 s |
| 6 mutants | ~90 s |
| **Normal gate (2 runs)** | **~30 s** |
| **Mutation gate** | **~90 s** |
| **Total** | **~120 s** |

Maximum gate runtime: 300 s (5 min).
If exceeded, reduce vocab_size (already 512).
Do NOT reduce d_model, rank, or partition without updating profile + hashes.

---

## R3-14 — Backward Compatibility (RC5.1)

Unchanged:
- 76 existing tests must still pass
- 5/5 mutation gate must still pass
- Receipts: HMAC, nonce, key_id, expiry (extended but compatible)
- Coordinator: ledger, contribution states, SUPERSEDED, ACTIVE, FedAvg, round.close
- SQLite persistence
- NPZ delta format for RC5.1 (bundle for RC5.2)

New/Changed:
- `step.open` extended to support server forward
- `step.worker_forward.submit` new
- `step.server_backward.fetch` new
- `step.worker_update.submit` new
- RC5.1 handlers remain at protocol 1.1.0-rc5.1
- Coordinator supports both NPZ (RC5.1) and bundle (RC5.2) deltas
