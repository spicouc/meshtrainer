# RC5.2 Implementation Plan (R4)

## Phase 1 — Monolithic Reference
1. `MonolithicModel` with all components
2. Causal LM + CrossEntropyLoss
3. `build_reference_state(seed=42)` — single canonical init
4. State mapping: monolithic keys → split keys
5. N-SPLIT-01

## Phase 2 — Split Server
6. `SplitServerModel` from reference state
7. `server_forward()` → activation bundle
8. `server_loss_and_backward()` → cut gradient
9. N-SPLIT-02, 04, 05

## Phase 3 — Worker
10. `WorkerModel` from reference state
11. `worker_forward()` → cut activation
12. `worker_backward()` → optimizer.step(once) → delta
13. Worker update journal (pre_hash, post_hash, delta_hash)
14. N-SPLIT-03, 07-11

## Phase 4 — Protocol (1.2.0-rc5.2)
15. `step.open` with idempotency key + cached activation
16. `step.worker_forward.submit` with forward_id
17. `step.server_backward.fetch` with backward_id
18. `step.worker_update.submit` with update_id + bundle
19. `step.commit` with delta_id → receipt
20. `step.abort`
21. tensor_bundle_v1: magic, header, payload, SHA

## Phase 5 — Integration
22. HTTP JSON-RPC server
23. Python worker client
24. Single-step equivalence
25. Multi-step + resume

## Phase 6 — Tests
26. N-SPLIT-01..16
27. R4-10 additional (12 tests)
28. MUT-N1..6
29. Regression: 76 RC5.1 + 5/5 mutation
30. Combined runner: 11 mutants
31. Gate: 2 runs × (16+6+12+76) tests, budget 300s
