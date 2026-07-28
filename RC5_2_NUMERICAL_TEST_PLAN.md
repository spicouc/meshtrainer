# RC5.2 Numerical Test Plan (R2)

## Numerical Tests — N-SPLIT-01..16

| ID | Name | What it validates | Expected |
|---|---|---|---|
| 01 | model_state_identical | Monolithic and split loaded from same state dict | allclose |
| 02 | server_activation | Embedding + base layers output | allclose |
| 03 | worker_cut_activation | Worker forward + LoRA applied correctly | allclose |
| 04 | logits | Top layers + LM head output (causal shift) | allclose |
| 05 | loss | CrossEntropyLoss with shift_logits/shift_labels | allclose |
| 06 | cut_gradient | Grad of loss w.r.t. cut activation | allclose |
| 07 | lora_a_gradient | Grad of loss w.r.t. LoRA A | allclose |
| 08 | lora_b_gradient | Grad of loss w.r.t. LoRA B | allclose |
| 09 | base_weights_unchanged | optimizer params == LoRA only; base.grad is None; base state unchanged | all zero grad, state equal |
| 10 | lora_post_step | LoRA weights after optimizer.step() | allclose |
| 11 | delta_equivalent | LoRA delta (after - before) | allclose |
| 12 | receipt_sha_matches_delta | delta_sha256 in receipt matches actual delta bytes | exact SHA |
| 13 | two_consecutive_steps | Step N and N+1 produce different correct deltas | coherent |
| 14 | loss_decreases | Loss after 10 steps < loss after 1 step | decrease |
| 15 | deterministic_repeat | Same seed + same data → same results | exact match |
| 16 | resume_equivalence | Resume after COMMITTED step matches continuous run | allclose |

## Mutation Tests — MUT-N1..6

| ID | Mutation | Detected by |
|---|---|---|
| MUT-N1 | cut_gradient = randn | N-SPLIT-06 FAIL |
| MUT-N2 | loss = (output * dummy_grad).sum() | N-SPLIT-05 FAIL |
| MUT-N3 | worker_out = server_act.reshape(...) | N-SPLIT-03 FAIL |
| MUT-N4 | base params added to optimizer | N-SPLIT-09 FAIL |
| MUT-N5 | alter one byte of cut_gradient | N-SPLIT-06 FAIL or SHA mismatch |
| MUT-N6 | ReLU between LoRA A/B | N-SPLIT-10 or N-SPLIT-11 FAIL |

## Additional Tests

| Test | Description | Expected |
|---|---|---|
| protocol_version mismatch | Client sends wrong version | REJECTED |
| worker_model_hash mismatch | Wrong worker model | REJECTED |
| partition_schema_hash mismatch | Wrong partition schema | REJECTED |
| NaN/Inf in tensor | Send NaN data | REJECTED |
| wrong endianness | Non-C-contiguous or wrong byte order | REJECTED (byte_length mismatch) |
| duplicate update_id | Same update_id sent twice | Returns existing receipt, no double optimizer |

## Regression Tests (must still pass)

- rc5_tests.py: 45 tests
- r6_tests.py: 31 tests
- Total: 76 tests, 0 flaky, 0 skips
- Mutation gate: 5/5

## Gate Criteria

Normal gate:
- Run 1: all numerical (16) + regression (76) + extra (6) = 98 PASS
- Run 2: all PASS
- 0 flaky, 0 skips, 0 timeouts

Mutation gate:
- MUT-N1..6: 6/6 DETECTED
- RC5.1 mutation gate (5/5): must still pass unchanged
- 0 invalid, 0 runtime-invalid
