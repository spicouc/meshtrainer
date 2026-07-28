# RC5.1-R8 Report

**Commit:** 2f4f5f97c4bc49a142b79b0a3a3b9e5ad0e5c8a9
**Branca:** rc5.1-p0-corrective
**Data:** 2026-07-28

## Resultats

| Suite | Assertions | Run 1 | Run 2 |
|---|---|---|---|
| Normal (rc5_tests.py) | 45 | 45/45 PASS | 45/45 PASS |
| R8 (r6_tests.py) | 29 | 29/29 PASS | 29/29 PASS |
| **Total** | **74** | **74/74** | **74/74** |

## Mutation Gate

| Mutant | Status |
|---|---|
| Baseline py_compile | PASS |
| Baseline normal | PASS |
| Baseline R6 | PASS |
| MUT-01 zero delta | DETECTED |
| MUT-02 no SUPERSEDED | DETECTED |
| MUT-03 no SHA bytes | DETECTED |
| MUT-04 transitions open | DETECTED |
| MUT-05 ownership open | DETECTED |
| Mutation score | 5/5 |
| Invalid mutants | 0 |
| Not applied | 0 |
| Timeouts | 0 |

## Estat del gate

- Protocol primitives: PARTIAL PASS
- Receipt/delta integrity: PASS
- Ledger revision semantics: PASS
- Persistence: IMPLEMENTED AND TESTED
- Tensor FedAvg: IMPLEMENTED, RC5 PATH VALIDATED
- Numerical split learning: NOT VALIDATED
- RC5.1 global gate: OPEN
- RC5.2: NOT STARTED

## Assertions per categoria (R8 suite)

- e2e: 8 (R6-01 E2E Split Server flow)
- ledger: 8 (R6-02 Revisions A-D)
- integrity: 10 (R6-03 negative receipt-delta cases)
- security: 2 (R6-05 Split Server direct)
- protocol: 0*
- numerical: 0*
- persistence: 0*

*Les 45 assertions de rc5_tests.py cobreixen protocol, numerical, security, persistence.

## Dependencies RC3 incloses

- rc3_coordinator.py
- rc3_state.py
- rc3_train_state.py
- rc3_worker_sim.py
- aggregation.py
- synthetic_tensor_store.py
