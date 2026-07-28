# RC5.2 Risk Register

| # | Risk | Impact | Likelihood | Mitigation | Owner |
|---|---|---|---|---|---|
| R01 | Numerical drift between split and monolithic paths | Gate failures | Medium | Strict deterministic settings, tolerance 1e-5/1e-6, CPU gate | Executor |
| R02 | Worker infers training data from activations | Privacy leak | Low | Labels never leave server; documented in ADR-12 | Supervisor |
| R03 | Autograd graph OOM on large models | Runtime crash | Medium | Gradient checkpointing, chunked sequences, payload limits | Executor |
| R04 | Single-step equivalence ≠ training convergence | False gate confidence | Medium | N-SPLIT-14 (multi-step) and N-SPLIT-16 (resume) required | Supervisor |
| R05 | WebGPU non-determinism breaks SHA comparison | False failures | High (WebGPU) | Tolerance-based comparison; documented exception for WebGPU | Executor |
| R06 | RC5.2 changes break RC5.1 tests | Regression | Low | 76 existing tests + 5/5 mutation gate must still pass | Executor |
| R07 | Coordinator delta store conflicts with RC5.1 delta format | Data corruption | Low | Compatible NPZ format; separate directory per run | Executor |
| R08 | Unstable gradients during first split backward | NaN/Inf in tensors | Medium | NaN/Inf detection on all received tensors; configurable gradient clipping | Executor |
| R09 | Optimizer state diverges between monolithic and split | False negatives in comparison | Medium | Same optimizer class, lr, weight_decay; verify state_dict | Executor |
| R10 | Memory pressure from retaining activations for backward | OOM | Medium | Free tensors after use; detach early; explicit lifecycle documented | Executor |
