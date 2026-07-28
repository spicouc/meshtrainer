# RC5.2 State Machine

```
OPEN
  │ step.server_forward
  ▼
SERVER_FORWARD
  │ (server sends activation to worker)
  ▼
WORKER_FORWARD
  │ (worker returns cut activation)
  ▼
SERVER_LOSS
  │ (server computes loss, backward)
  ▼
SERVER_BACKWARD
  │ (cut gradient sent to worker)
  ▼
WORKER_BACKWARD
  │ (worker backward + optimizer + delta)
  ▼
DELTA_READY
  │ step.commit
  ▼
COMMITTED
```

Terminal states: COMMITTED, ABORTED, EXPIRED

## Transitions

| From | To | Trigger | Valid |
|---|---|---|---|
| OPEN | SERVER_FORWARD | step.server_forward | always |
| SERVER_FORWARD | WORKER_FORWARD | step.cut_activation | after activation sent |
| WORKER_FORWARD | SERVER_LOSS | (implicit after loss) | automatic |
| SERVER_LOSS | SERVER_BACKWARD | (implicit after backward) | automatic |
| SERVER_BACKWARD | WORKER_BACKWARD | step.cut_gradient | gradient sent to worker |
| WORKER_BACKWARD | DELTA_READY | (implicit after optimizer) | automatic |
| DELTA_READY | COMMITTED | step.commit | once |
| any | ABORTED | step.abort | always |
| any | EXPIRED | lease expiry | timeout |

## Idempotency

| Method | Idempotent | Safe to retry |
|---|---|---|
| step.open | No | No |
| step.server_forward | Yes (same input) | Yes |
| step.cut_activation | Yes (same tensor) | Yes |
| step.cut_gradient | Yes | Yes (gradient already applied) |
| step.commit | Yes (looks up receipt) | Yes (returns same receipt) |
| step.abort | Yes | Yes |
