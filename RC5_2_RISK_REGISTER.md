# RC5.2 Risk Register (R3)

| # | Risk | Mitigated (R3) | Deferred to |
|---|---|---|---|
| R01 | Numerical drift | Deterministic settings, tolerances 1e-5/1e-6 | — |
| R02 | Worker infers labels | Labels never leave server | RC5.6 |
| R03 | Autograd OOM | Payload limits (131 KB per tensor), small vocab | RC5.3 |
| R04 | Single-step =/ convergence | N-SPLIT-14 (10 steps) | — |
| R05 | WebGPU non-determinism | CPU gate first; tolerance comparison later | RC5.5 |
| R06 | RC5.2 breaks RC5.1 | 76 tests + 5/5 mutation must pass | — |
| R07 | Worker sends arbitrary delta | SHA+HMAC prove byte integrity only. Numerical honesty NOT proven. | RC5.6 |
| R08 | Worker model wrong | worker_model_hash links artifact; no remote attestation | RC5.6 |
| R09 | Double optimizer | Worker-side update journal + server update_id | — |
| R10 | Gradient replay | step_id + nonce | — |
| R11 | Protocol version collision | Routing by protocol_version | — |
| R12 | Serialization mismatch | tensor_bundle_v1 with strict checks | — |
| R13 | Crash after optimizer, before persist | NOT mitigated | RC5.4 |
| R14 | Activation inversion / model extraction | Documented | RC5.6 |
| R15 | NaN/Inf | Detected on all received tensors | — |
