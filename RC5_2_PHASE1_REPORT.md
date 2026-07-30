# RC5.2 Phase 1 R4 — Final Closeout Report

**Commit:** pending
**Branch:** rc5.2-numerical-phase1
**Base:** meshtrainer-v1.1-rc5.1

## Corrections R4

| Bloquejador | Correccio |
|---|---|
| MUT-06 INVALID + monolithic modified | Python script `mut06_relu_worker.py`: ReLU between LoRA A/B only in WorkerNumericalModel |
| RUNTIME-INVALID classification | ANY traceback → RUNTIME-INVALID (regardless of assertions) |
| P1-N02: soft unexpected check | `actual_s_keys - server_expected` (real key sets, not derived from load) |
| P1-N20: incomplete | 7 comparisons: loss, 2xgrad, 2xpost, 2xdelta |
| Count inconsistency | Updated to real 35 assertions |
| Missing logs/reports | Added RUN_1/2 logs, mutation log, diff, mutation report |

## Numerical tests: 35/35 PASS (strict rtol=1e-5, atol=1e-6)
Run 1: PASS, Run 2: PASS
