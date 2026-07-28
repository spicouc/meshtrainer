# RC5.2 Numerical Test Plan

## Test Suites

### N-SPLIT-01..16 — 16 tests total

| ID | Name | What it validates | Level | Expected |
|---|---|---|---|---|
| 01 | model_state_identical | Monolithic and split models loaded from same state dict | A | allclose |
| 02 | server_activation | Embedding + base layers output | A | allclose |
| 03 | worker_cut_activation | Worker forward + LoRA applied | A | allclose |
| 04 | logits | Top layers + LM head output | A | allclose |
| 05 | loss | CrossEntropyLoss value | A | allclose |
| 06 | cut_gradient | Grad of loss w.r.t. cut activation | A | allclose |
| 07 | lora_a_gradient | Grad of loss w.r.t. LoRA A | A | allclose |
| 08 | lora_b_gradient | Grad of loss w.r.t. LoRA B | A | allclose |
| 09 | base_weights_unchanged | All base params have zero gradient | B | all zero |
| 10 | lora_post_step | LoRA weights after optimizer.step() | B | allclose |
| 11 | delta_equivalent | LoRA delta (after - before) | B | allclose |
| 12 | receipt_sha_matches_delta | delta_sha256 in receipt matches delta bytes | B | exact SHA match |
| 13 | two_consecutive_steps | Step N and N+1 produce different correct deltas | B | coherent |
| 14 | loss_decreases | Loss after 10 steps < loss after 1 step | B | decrease |
| 15 | deterministic_repeat | Same seed → same results | B | exact match |
| 16 | resume_equivalence | Restart mid-training produces same state as continuous | B | allclose |

### Mutation Tests — 6 tests

| ID | Mutation | Detection | Expected |
|---|---|---|---|
| MUT-N1 | cut_gradient = randn | N-SPLIT-06 fails | N-SPLIT-06 FAIL |
| MUT-N2 | loss = (output * dummy_grad).sum() | N-SPLIT-05 fails | N-SPLIT-05 FAIL |
| MUT-N3 | worker_out = server_act.reshape(...) | N-SPLIT-03 fails | N-SPLIT-03 FAIL |
| MUT-N4 | base params in optimizer | N-SPLIT-09 fails | N-SPLIT-09 FAIL |
| MUT-N5 | alter one byte of cut_gradient | SHA mismatch or N-SPLIT-06 | N-SPLIT-06 FAIL |
| MUT-N6 | ReLU between LoRA A/B | N-SPLIT-10 or 11 fails | N-SPLIT-10 FAIL |

### Regression — 76 + 5 tests (must still pass)

- rc5_tests.py: 45 tests
- r6_tests.py: 31 tests
- Mutation gate: 5/5

## Gate Criteria

Normal gate:
- Run 1: all numerical (16) + regression (76) PASS
- Run 2: all numerical (16) + regression (76) PASS
- 0 flaky, 0 skips, 0 timeouts

Mutation gate:
- MUT-N1 through MUT-N6: 6/6 DETECTED
- 0 invalid, 0 runtime-invalid
- RC5.1 mutation gate (5/5) must still pass unchanged
