# RC5.2-ADR-R4 — Contract Freeze

**Status:** Contract Freeze (R4)
**Base tag:** `meshtrainer-v1.1-rc5.1`
**ADR R3:** commit `886ce79`

---

## R4-01 — Idempotency Keys (frozen)

Each idempotent operation has a unique key. The server stores the SHA of the
first request's payload and returns the cached response on retry.

| Method | Idempotency key | Retry rule |
|---|---|---|
| `step.open` | `(run_id, round_id, assignment_id, micro_unit_id)` | Same key + same request → cached activation. Same key + different request → REJECTED |
| `step.worker_forward.submit` | `(unit_id, forward_id)` | Same key + same SHA → cached result. Different SHA → REJECTED |
| `step.server_backward.fetch` | `(unit_id, backward_id)` | Always returns same gradient for same unit+backward_id |
| `step.worker_update.submit` | `(unit_id, update_id)` | Same key + same SHA → cached delta_id. Different SHA → REJECTED |
| `step.commit` | `(unit_id, delta_id)` | Returns same cached receipt |
| `step.abort` | `(unit_id,)` | Already aborted → same response |

The server stores `request_sha256` for every idempotent request to detect
payload changes on retry.

---

## R4-02 — Worker Artifacts (frozen)

### worker_base_model_v1
```yaml
contains:
  - local_layers[0]
  - local_layers[1]
metadata:
  worker_model_hash: SHA256(canonical JSON of model state + manifest)
  partition_schema_hash: SHA256(canonical JSON of partition schema)
  numerical_profile_hash: SHA256(canonical JSON of profile)
  model_format_version: "1.2.0-rc5.2"
  tensor_manifest: {names, shapes, dtypes}
```

### base_adapter_v1
```yaml
contains:
  local_layers.0.linear1.lora_A.weight  # [8, 256]
  local_layers.0.linear1.lora_B.weight  # [1024, 8]
metadata:
  base_adapter_hash: SHA256(canonical JSON of adapter tensors)
  adapter_schema_hash: SHA256(canonical JSON of schema)
  round_id: "..."
  revision: int
```

### Lifecycle
1. Coordinator creates `base_adapter_v1` from `base_adapter.npz` at round start
2. Coordinator sends `worker_model_hash` and `base_adapter_hash` to Split Server
3. Worker downloads both artifacts before `step.open`
4. Split Server validates hashes on `step.open`
5. Mismatch → REJECTED with reason identifying which hash failed

---

## R4-03 — Initialization and State Mapping (frozen)

```python
def build_reference_state(seed=42):
    torch.manual_seed(seed)
    model = MonolithicModel()  # as defined in ADR
    return model.state_dict()

# Mapping from monolithic keys to split keys
STATE_MAP = {
    # Server side
    "embedding.weight":          "split_server.embedding.weight",
    "base_layers.0.*":           "split_server.base_layers.0.*",
    "base_layers.1.*":           "split_server.base_layers.1.*",
    "top_layers.0.*":            "split_server.top_layers.0.*",
    "top_layers.1.*":            "split_server.top_layers.1.*",
    "lm_head.weight":            "split_server.lm_head.weight",
    "lm_head.bias":              "split_server.lm_head.bias",
    # Worker side
    "local_layers.0.*":          "worker.local_layers.0.*",
    "local_layers.1.*":          "worker.local_layers.1.*",
    "local_layers.0.linear1.lora_A.weight":  "worker.lora_A.weight",
    "local_layers.0.linear1.lora_B.weight":  "worker.lora_B.weight",
}

# Both split models load from the same reference state_dict
ref = build_reference_state(42)
server_model.load_state_dict({k: ref[k] for k in SERVER_KEYS})
worker_model.load_state_dict({k: ref[k] for k in WORKER_KEYS})
```

Init:
- lora_A: `kaiming_uniform_(weight, a=math.sqrt(5))`
- lora_B: `zeros_(weight)`
- base layers: default PyTorch init for Linear
- positional_embedding: default nn.Embedding init
- LM head: default nn.Linear init

---

## R4-04 — Canonical Hashes (frozen)

```python
def canonical_json_v1(obj):
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
```

### numerical_profile_hash
Applied to the canonical JSON of `numerical_profile_v1`. Not YAML.

### adapter_schema_hash
```json
{
  "schema_version": "adapter_schema_v1",
  "tensors": [
    {
      "name": "local_layers.0.linear1.lora_A.weight",
      "dtype": "<f4",
      "shape": [8, 256]
    },
    {
      "name": "local_layers.0.linear1.lora_B.weight",
      "dtype": "<f4",
      "shape": [1024, 8]
    }
  ]
}
```
Tensors sorted by `name`.

### partition_schema_hash
```json
{
  "schema_version": "partition_schema_v1",
  "cut_entry": "after base_layers[1]",
  "cut_exit": "after worker_layers[1]",
  "tensors": [
    {"name": "server_activation", "shape": [1,128,256], "dtype": "<f4"},
    {"name": "cut_activation",    "shape": [1,128,256], "dtype": "<f4"},
    {"name": "cut_gradient",      "shape": [1,128,256], "dtype": "<f4"}
  ]
}
```

---

## R4-05 — Delta Contract (frozen)

`step.worker_update.submit` receives exclusively:

```json
{
  "update_id": "unique-client-nonce",
  "delta_bundle_b64": "base64 of tensor_bundle_v1",
  "delta_bundle_byte_length": 41000,
  "delta_bundle_sha256": "64-char hex",
  "adapter_schema_hash": "64-char hex"
}
```

No `delta_sha256` (replaced by `delta_bundle_sha256`).
Bundle contains delta_A = post_A - pre_A and delta_B = post_B - pre_B.

---

## R4-06 — tensor_bundle_v1 (hardened)

```
Offset  Size  Field
0       4     magic "MTB1"
4       4     header_length (uint32 LE)
8       H     header (canonical JSON, max 16 KiB)
8+H     P     payload (concatenated float32 LE, C-order)
```

Rules:
- Exactly 2 tensors: `lora_A.weight`, `lora_B.weight`
- Sorted lexicographically by `name`
- Unknown name → REJECTED
- Missing tensor → REJECTED
- Duplicate name → REJECTED
- Consecutive offsets, no overlap, no trailing bytes
- `<f4`, C-contiguous
- NaN/Inf → REJECTED
- SHA-256 over entire bundle (magic + header_length + header + payload)

Total bundle length = 8 + header_length + sum(byte_length).

---

## R4-07 — checkpoint.upload RC5.2 (frozen)

```json
{
  "delta_format": "tensor_bundle_v1",
  "delta_bundle_b64": "...",
  "delta_bundle_sha256": "...",
  "adapter_schema_hash": "...",
  "receipt": {
    "protocol_version": "1.2.0-rc5.2",
    ...
  }
}
```

RC5.1 uses `delta_format: "npz_v1"`. Coordinator selects parser by
`protocol_version + delta_format`. Not inferred from content.

Validations:
- receipt.protocol_version = "1.2.0-rc5.2"
- receipt.delta_bundle_sha256 matches bytes
- adapter_schema_hash matches receipt
- worker_model_hash, partition_schema_hash, numerical_profile_hash match stored profile
- bundle decodes correctly

---

## R4-08 — State Machine (corrected)

```
OPEN
  │ step.open
  ▼
SERVER_ACTIVATION_READY
  │ step.worker_forward.submit
  ▼
CUT_ACTIVATION_ACCEPTED
  │ (server backward)
  ▼
CUT_GRADIENT_READY
  │ step.server_backward.fetch (first)
  ▼
CUT_GRADIENT_DELIVERED
  │ step.worker_update.submit
  ▼
DELTA_VERIFIED
  │ step.commit
  ▼
COMMITTED
```

Terminal states: `COMMITTED`, `ABORTED`, `EXPIRED` (reserved, not implemented in RC5.2)

Transitions:
- Any non-terminal state → ABORTED (via step.abort)
- Terminal states (COMMITTED, ABORTED, EXPIRED) are IMMUTABLE — no further transitions
- EXPIRED: lease deadline before DELTA_VERIFIED. Deferred to RC5.4.

---

## R4-09 — Delta Definition (frozen)

delta_A = post_lora_A.weight - pre_lora_A.weight
delta_B = post_lora_B.weight - pre_lora_B.weight

The bundle transports these delta tensors, not absolute post-step weights.
The worker journal records:
- pre_adapter_hash
- post_adapter_hash
- delta_bundle_sha256

The Split Server validates schema and bytes but CANNOT recompute the delta
without the complete pre-step adapter. This limitation is documented.

---

## R4-10 — Additional Tests (frozen)

| Test | Expected |
|---|---|
| duplicate forward_id + same payload | cached response |
| duplicate forward_id + different payload | REJECTED |
| duplicate update_id + same bundle | same delta_id |
| duplicate update_id + different bundle | REJECTED |
| committed → abort | REJECTED (terminal state) |
| duplicate tensor name in bundle | REJECTED |
| missing LoRA tensor in bundle | REJECTED |
| unknown LoRA tensor in bundle | REJECTED |
| trailing bundle bytes | REJECTED |
| wrong numerical_profile_hash | REJECTED |
| wrong partition_schema_hash | REJECTED |
| wrong worker_model_hash | REJECTED |

Fix fixture:
```python
tokens = torch.randint(0, 512, (1, 128))  # vocab_size=512 exclusive
```

---

## R4-11 — Weight Ownership (confirmed)

| Weight | Artifact | Loaded by |
|---|---|---|
| embedding | model state dict | Split Server |
| base_layers | model state dict | Split Server |
| local_layers | worker_base_model_v1 | Worker |
| LoRA A/B | base_adapter_v1 | Worker |
| top_layers | model state dict | Split Server |
| LM head | model state dict | Split Server |
