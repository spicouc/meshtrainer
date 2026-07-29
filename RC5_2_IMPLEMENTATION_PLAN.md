# RC5.2 Implementation Plan (R3)

## Phase 1 — Monolithic Reference
1. Build `MonolithicModel`: embedding + 2 base + 2 worker + LoRA (target: local_layers.0.linear1) + 2 top + LM head
2. Causal LM: causal_mask on all layers, shift_logits/shift_labels
3. SGD optimizer (lr=0.01) on LoRA A/B only
4. State dict save/load helper
5. N-SPLIT-01

## Phase 2 — Split Server
6. `SplitServerModel`: embedding + base + top + LM head
7. `server_forward()` → activation bundle
8. `server_loss_and_backward(cut_activation, labels)` → cut_gradient bundle
9. N-SPLIT-02, 04, 05

## Phase 3 — Worker
10. `WorkerModel`: local_layers + LoRA A/B
11. `worker_forward(activation)` → cut_activation bundle
12. `worker_backward(gradient)` → optimizer.step (once) → delta bundle
13. Worker update journal (update_id, pre_hash, post_hash, delta)
14. N-SPLIT-03, 07, 08, 09, 10, 11

## Phase 4 — Protocol Handlers (1.2.0-rc5.2)
15. `step.open` — computes server_activation
16. `step.worker_forward.submit` — accepts cut_activation, returns loss
17. `step.server_backward.fetch` — returns cut_gradient
18. `step.worker_update.submit` — accepts delta bundle, validates, returns delta_id
19. `step.commit` — validates, signs receipt, transitions to COMMITTED
20. `step.abort`
21. tensor_bundle_v1 encode/decode

## Phase 5 — Integration
22. HTTP JSON-RPC server on Split Server
23. Python worker client (loop)
24. Single-step equivalence test
25. Multi-step deterministic test
26. Resume test (between steps)

## Phase 6 — Tests and Gate
27. N-SPLIT-01..16
28. Extra tests: version, byte_order, dtype, memory_order, NaN, update_id
29. MUT-N1..6 mutation runners
30. Regression: 76 RC5.1 tests + 5/5 mutation
31. Combined runner: 11 mutants total
32. Gate: 2 runs × (16+6+76) tests, 6 mutants, budget 300s
