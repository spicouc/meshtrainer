# RC5.2 Phase 1 R5 — Test Matrix (35 assertions)

| Test | Assertions | Description |
|---|---|---|
| P1-N01 | 1 | Profile hash deterministic |
| P1-N02 | 1 | All state identical, 0 missing/unexpected |
| P1-N03 | 1 | Server activation equivalent |
| P1-N04 | 1 | Cut activation equivalent |
| P1-N05 | 1 | Logits equivalent |
| P1-N06 | 1 | Loss equivalent |
| P1-N07 | 1 | Cut gradient equivalent |
| P1-N08 | 1 | LoRA-A gradient equivalent |
| P1-N09 | 1 | LoRA-B gradient equivalent |
| P1-N10 | 1 | Optimizer == LoRA only |
| P1-N11 | 1 | Worker base requires_grad False |
| P1-N12 | 1 | Worker base grad None after backward |
| P1-N13 | 1 | Worker base weights unchanged |
| P1-N14 | 1 | LoRA-A post-step equivalent |
| P1-N15 | 1 | LoRA-B post-step equivalent |
| P1-N16 | 1 | Delta-A equivalent |
| P1-N17 | 1 | Delta-B equivalent |
| P1-N18 | 1 | Delta non-zero |
| P1-N19 | 1 | Deterministic repeat |
| P1-N20 | **7** | Step 2: loss, grad-A, grad-B, LoRA-A, LoRA-B, delta-A, delta-B |
| P1-N21 | 2 | Loss matches oracle + label change alters loss |
| P1-N22 | 1 | ETT == 127 |
| P1-N23 | 3 | Early positions unaffected, late differ, mask check |
| P1-N24 | 1 | Monolithic base requires_grad False |
| P1-N25 | 1 | Monolithic base grad None after backward |
| P1-N26 | 1 | Monolithic base weights unchanged |
| **Total** | **35** | |
