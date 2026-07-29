# RC5.2 Risk Register (R4)

| # | Risk | Mitigated | Def. to |
|---|---|---|---|
| R01 | Numerical drift | Determinism + tolerances | — |
| R02 | Labels leak | Never leave server | RC5.6 |
| R03 | OOM | Small profile, 131KB/tensor | RC5.3 |
| R04 | Single-step ≠ convergence | 10-step test | — |
| R05 | WebGPU non-det. | CPU gate first | RC5.5 |
| R06 | RC5.1 regression | 76 + 5/5 must pass | — |
| R07 | Arbitrary delta | SHA+HMAC: byte integrity only | RC5.6 |
| R08 | Wrong worker model | worker_model_hash (no attestation) | RC5.6 |
| R09 | Double optimizer | Worker journal + update_id | — |
| R10 | Gradient replay | step_id + nonce | — |
| R11 | Version collision | protocol_version routing | — |
| R12 | Serialization mismatch | tensor_bundle_v1 | — |
| R13 | Crash after optimizer | NOT MITIGATED | RC5.4 |
| R14 | Activation inversion | Documented | RC5.6 |
| R15 | NaN/Inf | Detected | — |
