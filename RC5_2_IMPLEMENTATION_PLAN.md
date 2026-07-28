# RC5.2 Implementation Plan

## Phase 1 — Monolithic Reference (estimated effort: medium)

1. Create `MonolithicModel` with embedding, base_layers, local_layers,
   LoRA A/B (no ReLU), top_layers, LM head
2. Single forward → logits → CrossEntropyLoss
3. Backward → optimizer.step → delta
4. Save state dict; create `load_monolithic_state()` function
5. Add `assert_no_base_gradients()` helper
6. Test: N-SPLIT-01 (model state identical)

## Phase 2 — Split Server Model

7. Create `SplitServerModel`: embedding + base_layers + top_layers + LM head
8. Load from monolithic state dict
9. Implement `server_forward(token_ids)` → activation tensor
10. Implement `server_loss_and_backward(cut_activation, labels)` → cut_gradient
11. Test: N-SPLIT-02 (server activation), N-SPLIT-04 (logits), N-SPLIT-05 (loss)

## Phase 3 — Worker Model

12. Create `WorkerModel`: local_layers + LoRA A/B
13. Load from monolithic state dict
14. Implement `worker_forward(server_activation)` → cut_activation
15. Implement `worker_backward(cut_gradient)` → optimizer.step → delta
16. Test: N-SPLIT-03 (cut activation), N-SPLIT-07/08 (gradients)

## Phase 4 — Protocol Handlers

17. Implement `step.server_forward` handler on Split Server
18. Implement `step.cut_activation` (receives worker output)
19. Implement `step.cut_gradient` (sends gradient, worker acts)
20. Implement `step.commit` (worker sends delta, server signs receipt)
21. Implement `step.abort`
22. Update state machine to new states

## Phase 5 — Integration

23. Wire Split Server handlers into HTTP JSON-RPC
24. Wire Worker as client (Python process, later browser)
25. Single-step equivalence: monolithic vs split
26. Multi-step deterministic run
27. Resume test

## Phase 6 — Tests and Gate

28. N-SPLIT-01..16
29. MUT-N1..6 (6 mutation runners)
30. Regression: 76 RC5.1 tests + 5/5 mutation
31. New mutation runner covering both RC5.1 and RC5.2 mutants
