# RC5.2 State Machine (R2)

```
OPEN
  │ step.open  (server computes activation)
  ▼
SERVER_ACTIVATION_READY
  │ step.worker_forward.submit  (worker sends cut activation)
  ▼
WORKER_ACTIVATION_ACCEPTED
  │ (server computes loss + backward internally)
  ▼
CUT_GRADIENT_READY
  │ step.server_backward.fetch  (worker fetches cut gradient)
  ▼
WORKER_UPDATE_ACCEPTED
  │ (worker applies optimizer exactly once, presents delta via step.worker_update.submit)
  ▼
DELTA_VERIFIED
  │ step.commit  (server validates delta, signs receipt)
  ▼
COMMITTED
```

Terminal states: COMMITTED, ABORTED, EXPIRED

## Transitions

| From | To | Trigger | Note |
|---|---|---|---|
| OPEN | SERVER_ACTIVATION_READY | step.open | Server computes server_activation |
| SERVER_ACTIVATION_READY | WORKER_ACTIVATION_ACCEPTED | step.worker_forward.submit | Worker sends cut_activation |
| WORKER_ACTIVATION_ACCEPTED | CUT_GRADIENT_READY | (implicit) | Server computes loss + backward |
| CUT_GRADIENT_READY | WORKER_UPDATE_ACCEPTED | step.server_backward.fetch + step.worker_update.submit | Worker applies optimizer, sends delta |
| WORKER_UPDATE_ACCEPTED | DELTA_VERIFIED | (implicit) | Server validates delta |
| DELTA_VERIFIED | COMMITTED | step.commit | Server signs receipt |
| any | ABORTED | step.abort | Always valid |
| any | EXPIRED | Lease timeout | Automatic |

## Idempotency and Replay Protection

| Method | Idempotent | Replay protection | Notes |
|---|---|---|---|
| step.open | No | unit state check | Fresh unit needed |
| step.worker_forward.submit | Yes (same activations) | step_id + state | Returns same server result |
| step.server_backward.fetch | Yes | step_id + state | Returns same gradient |
| step.worker_update.submit | No (single optimizer step) | update_id | Duplicate update_id → existing receipt |
| step.commit | Yes | state check | Returns same receipt |
| step.abort | Yes | state check | Already aborted |

The `update_id` in `step.worker_update.submit` is a unique client-generated nonce.
On first call, the server applies the optimizer and stores the receipt.
On duplicate `update_id`, the server returns the stored receipt WITHOUT re-applying
the optimizer.
