# RC5.2 State Machine (R3)

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
  │ step.server_backward.fetch (first call)
  ▼
CUT_GRADIENT_DELIVERED
  │ step.worker_update.submit (worker presents validated delta)
  ▼
DELTA_VERIFIED
  │ step.commit (server signs receipt)
  ▼
COMMITTED
```

Terminals: `COMMITTED`, `ABORTED`, `EXPIRED`

| From | To | Trigger | Idempotent |
|---|---|---|---|
| OPEN | SERVER_ACTIVATION_READY | step.open | yes (same activation) |
| SERVER_ACTIVATION_READY | CUT_ACTIVATION_ACCEPTED | step.worker_forward.submit | yes |
| CUT_ACTIVATION_ACCEPTED | CUT_GRADIENT_READY | server backward (internal) | — |
| CUT_GRADIENT_READY | CUT_GRADIENT_DELIVERED | step.server_backward.fetch (first) | fetch yes, transition once |
| CUT_GRADIENT_DELIVERED | DELTA_VERIFIED | step.worker_update.submit | yes (same update_id) |
| DELTA_VERIFIED | COMMITTED | step.commit | yes (same receipt) |
| any | ABORTED | step.abort | yes |

No state implies server-side optimizer execution.
Optimizer runs exclusively at the worker (see R3-03).
