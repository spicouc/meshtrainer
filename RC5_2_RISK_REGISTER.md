# RC5.2 Risk Register (R2)

| # | Risk | Impact | Likelihood | Mitigation (RC5.2) | Deferred to |
|---|---|---|---|---|---|
| R01 | Numerical drift between split and monolithic | Gate failures | Medium | Deterministic settings, strict tolerances (1e-5/1e-6), CPU gate | — |
| R02 | Worker infers labels from activations | Privacy leak | Low | Labels never leave server; documented trust boundary | RC5.6 |
| R03 | Autograd OOM on large models | Runtime crash | Medium | Payload limits (100 MB), gradient checkpointing | RC5.3 |
| R04 | Single-step equivalence ≠ convergence | False gate confidence | Medium | N-SPLIT-14 (multi-step) required | — |
| R05 | WebGPU non-determinism | False failures | High (WebGPU) | Tolerance comparison, CPU gate first | RC5.5 |
| R06 | RC5.2 breaks RC5.1 tests | Regression | Low | 76 existing tests + 5/5 mutation gate must pass | — |
| R07 | Worker returns arbitrary delta | Invalid contribution | Medium | SHA-256 + HMAC receipt; coordinator re-validates | — |
| R08 | Worker model incorrect | Wrong activations | Medium | worker_model_hash verification before round | — |
| R09 | Double optimizer step | Weight corruption | High | update_id nonce prevents duplicate apply | — |
| R10 | Gradient replay (old gradient re-sent) | Incorrect update | Medium | step_id + nonce per step gradient | — |
| R11 | Protocol version collision | Wrong handler | Low | Routing by protocol_version field | — |
| R12 | Canonical serialization mismatch (byte order, padding) | False negative in SHA check | Low | Strict byte_length == product*4; no padding | — |
| R13 | Crash after optimizer, before commit | Lost delta, inconsistent state | High | NOT mitigated in RC5.2 | RC5.4 |
| R14 | Activation inversion attack / model extraction | Privacy | Low | Documented; no specific mitigation in RC5.2 | RC5.6 |
| R15 | NaN/Inf injected via gradient | Training instability | Medium | NaN/Inf detection on all received tensors | — |
