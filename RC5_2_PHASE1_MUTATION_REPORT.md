# RC5.2 Phase 1 — Mutation Report

## Runner: RUN_RC5_2_PHASE1_MUTATION_GATE.sh
**Commit:** 55da2448ef30a0c401feedafbfab2edaa49cdf01

| Mutant | Method | Result | Score |
|---|---|---|---|
| MUT-01 | `randn_like` cut_gradient | DETECTED | 25/35 |
| MUT-02 | server ignores real labels | DETECTED | 24/35 |
| MUT-03 | differentiable bypass (0.0 * sum) | DETECTED | 22/35 |
| MUT-04 | extra base param in optimizer | DETECTED | 34/35 |
| MUT-05 | alter one gradient element | DETECTED | 25/35 |
| MUT-06 | ReLU between LoRA A/B (worker only) | DETECTED | 27/35 |

**Score: 6/6, Invalid: 0, Runtime-invalid: 0, Not applied: 0, Timeouts: 0**
