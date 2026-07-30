# RC5.2 Protocol Addendum — Final (W1)

**Protocol version:** 1.2.0-rc5.2
**RC5.1:** 1.1.0-rc5.1 (unchanged)

## Transport
Worker-driven HTTP JSON-RPC. Server does not initiate callbacks.

## Methods

| Method | Driver | Returns |
|---|---|---|
| `step.open` | Server | server_activation |
| `step.worker_forward.submit` | Worker | loss, ETT, backward_id |
| `step.server_backward.fetch` | Worker | cut_gradient |
| `step.worker_update.submit` | Worker | delta_id |
| `step.commit` | Worker | receipt v1.2 |
| `checkpoint.upload` | Worker | contribution_id |

## State Machine
OPEN → SERVER_ACTIVATION_READY → CUT_ACTIVATION_ACCEPTED → CUT_GRADIENT_READY → CUT_GRADIENT_DELIVERED → DELTA_VERIFIED → COMMITTED
Terminals: COMMITTED, ABORTED, EXPIRED

## Idempotency
Same key + same payload SHA → identical cached response
Same key + different payload → REJECTED

## Keys per method
- `step.open`: run_id + round_id + assignment_id + micro_unit_id
- `step.worker_forward.submit`: unit_id + forward_id
- `step.server_backward.fetch`: unit_id + backward_id
- `step.worker_update.submit`: unit_id + update_id
- `step.commit`: unit_id + delta_id

## Tensor format: tensor_bundle_v1
Magic "MTB1", header JSON, payload concatenated float32 LE C-contiguous.
Delta bundle: local_layers.0.linear1.lora_A.weight + local_layers.0.linear1.lora_B.weight

## Receipt v1.2
HMAC-signed, covers all normative fields.
Delta format field distinguishes tensor_bundle_v1 vs npz_v1 (RC5.1).

## Coordinator
checkpoint.upload validates receipt, nonce, hashes. FedAvg per tensor.
RC5.1 NPZ continues unchanged.
