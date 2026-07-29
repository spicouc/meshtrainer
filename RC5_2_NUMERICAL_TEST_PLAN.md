# RC5.2 Numerical Test Plan (R3)

## N-SPLIT Tests (16)

| ID | Name | Expected |
|---|---|---|
| 01 | model_state_identical | allclose |
| 02 | server_activation | allclose |
| 03 | worker_cut_activation | allclose |
| 04 | logits (causal shift) | allclose |
| 05 | loss (CE ignore_index=-100) | allclose |
| 06 | cut_gradient | allclose |
| 07 | lora_A_gradient | allclose |
| 08 | lora_B_gradient | allclose |
| 09 | base_weights_unchanged (R3-10) | optimizer_ids == lora_ids; base.grad is None; base state equal |
| 10 | lora_post_step | allclose |
| 11 | delta_equivalent | allclose |
| 12 | receipt_sha_matches_delta | exact SHA |
| 13 | two_consecutive_steps | coherent |
| 14 | loss_decreases (10 steps) | loss[10] < loss[0] |
| 15 | deterministic_repeat | exact match |
| 16 | resume (between COMMITTED steps) | allclose |

## MUT-N Tests (6)

| ID | Mutation | Detected by |
|---|---|---|
| N1 | cut_gradient = randn | N-SPLIT-06 FAIL |
| N2 | loss = dummy product | N-SPLIT-05 FAIL |
| N3 | worker_out = reshape | N-SPLIT-03 FAIL |
| N4 | base params in optimizer | N-SPLIT-09 FAIL |
| N5 | alter one byte of gradient | N-SPLIT-06 or SHA FAIL |
| N6 | ReLU between LoRA A/B | N-SPLIT-10/11 FAIL |

## Additional Tests (6)

| Test | Expected |
|---|---|
| protocol_version mismatch | REJECTED |
| byte_order != "little" | REJECTED |
| dtype != "<f4" | REJECTED |
| memory_order != "C" | REJECTED |
| NaN/Inf in tensor | REJECTED |
| duplicate update_id (same delta) | returns same delta_id |

## Regression (76 + 5)

- rc5_tests.py: 45 PASS
- r6_tests.py: 31 PASS
- RC5.1 mutation gate: 5/5

## Gate Budget

Normal gate: ~30 s (max 300 s)
Mutation gate: ~90 s (max 300 s)
