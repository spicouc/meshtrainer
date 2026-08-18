# RC5.2 State Machine (R4)

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

### Terminal states
- **COMMITTED** — immutable. No further transitions.
- **ABORTED** — immutable. No further transitions.
- **EXPIRED** — reserved for RC5.4. Not implemented in RC5.2.

### Transitions

| From | To | Trigger |
|---|---|---|
| OPEN | SERVER_ACTIVATION_READY | step.open |
| SERVER_ACTIVATION_READY | CUT_ACTIVATION_ACCEPTED | step.worker_forward.submit |
| CUT_ACTIVATION_ACCEPTED | CUT_GRADIENT_READY | internal server backward |
| CUT_GRADIENT_READY | CUT_GRADIENT_DELIVERED | step.server_backward.fetch (first) |
| CUT_GRADIENT_DELIVERED | DELTA_VERIFIED | step.worker_update.submit |
| DELTA_VERIFIED | COMMITTED | step.commit |
| any non-terminal | ABORTED | step.abort |

Terminal → any: prohibited.
