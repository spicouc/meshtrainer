# RC5.2 Implementation Plan (R2)

## Phase 1 — Monolithic Reference Model
1. Create `MonolithicModel` with: embedding, 2x base TransformerBlock,
   2x local TransformerBlock, LoRA attached to `local_layers[0].linear1`,
   2x top TransformerBlock, LM head
2. Single forward → causal shift → CrossEntropyLoss → backward
3. Optimizer (SGD, lr=0.01) on LoRA A/B only
4. Save state dict; `load_monolithic_state()` helper
5. `assert_no_base_gradients()` helper (ADR-R2-11)
6. Test: N-SPLIT-01

## Phase 2 — Split Server Model
7. Create `SplitServerModel`: embedding + base_layers + top_layers + LM head
8. Load from monolithic state dict
9. `server_forward(token_ids)` → `server_activation`
10. `server_loss_and_backward(cut_activation, labels)` → `cut_gradient`
11. Tests: N-SPLIT-02, 04, 05

## Phase 3 — Worker Model
12. Create `WorkerModel`: local_layers + LoRA A/B
13. Load from monolithic state dict
14. `worker_forward(server_activation)` → `cut_activation`
15. `worker_backward(cut_gradient)` → optimizer.step → delta
16. Tests: N-SPLIT-03, 07, 08, 09

## Phase 4 — Protocol Handlers (protocol 1.2.0-rc5.2)
17. `step.open` → server_forward, return activation
18. `step.worker_forward.submit` → accept activation, return loss
19. `step.server_backward.fetch` → return cut gradient
20. `step.worker_update.submit` → apply optimizer, store delta, return receipt
21. `step.commit` → validate receipt, finalize
22. `step.abort`
23. Update state machine (ADR-R2-04)

## Phase 5 — Integration
24. Wire handlers into HTTP JSON-RPC on Split Server
25. Write Python worker client (loop: open → forward → fetch → update → commit)
26. Single-step numerical equivalence test
27. Multi-step deterministic test
28. Resume test (between steps)

## Phase 6 — Tests and Gate
29. N-SPLIT-01..16
30. Additional tests: protocol_version, model_hash, schema_hash, NaN, endianness, update_id
31. MUT-N1..6 mutation runners
32. Regression: 76 RC5.1 tests + 5/5 mutation
33. Combined mutation runner covering 11 mutants (5 RC5.1 + 6 RC5.2)
34. Two-run gate validation
