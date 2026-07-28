# RC5.1-R9 Report — Final Audit

**Commit:** 1755be30d4f206d82f441346f39e320a1301b6b0
**Branch:** rc5.1-p0-corrective
**Date:** 2026-07-28

## Normal Gate

| Suite | Assertions | Run 1 | Run 2 |
|---|---|---|---|
| Base (rc5_tests.py) | 45 | PASS | PASS |
| R9 (r6_tests.py) | 29 | PASS | PASS |
| **Total** | **74** | **74/74** | **74/74** |

Flaky: 0 | Skips: 0 | Timeouts: 0

## Mutation Gate

| Mutant | Status | Details |
|---|---|---|
| Baseline py_compile | PASS | |
| Baseline normal | PASS | |
| Baseline R6 | PASS | |
| MUT-01 zero delta | DETECTED | normal=0, r6=1 |
| MUT-02 no SUPERSEDED | DETECTED | normal=1, r6=1 |
| MUT-03 no SHA bytes | DETECTED | normal=1, r6=1 |
| MUT-04 transitions open | DETECTED | normal=0, r6=1 |
| MUT-05 ownership open | DETECTED | normal=0, r6=1 |
| **Score** | **5/5** | |
| Invalid | 0 | |
| Runtime-invalid | 0 | |
| Not applied | 0 | |
| Timeouts | 0 | |

## R9 Fixes

| Fix | Description |
|---|---|
| R9-01 | MUT-02 uses Python multiline regex to replace entire SUPERSEDED SQL block with `pass` |
| R9-02 | MUT-04/05 use `np.zeros((1, d_model))` valid payloads instead of `AAA=` |
| R9-03 | All 10 R6-03 negative cases check both `status` and `reason` with substring matching |
| R9-04 | Retry uses the same receipt and nonce for both attempts, verifies nonce not consumed on fail, nonce exists on success |
| R9-05 | New commit 1755be3, consistent SHA across all files |
| R9-06 | All deliverables in tarball |
| R9-07 | Headers updated to RC5.1-R9 |

## R9 Changes (from R8)

```
 r6_tests.py                 | 44 ++++++++-------
 RUN_RC5_1_MUTATION_GATE.sh  | 72 +++++++++++++++--------
 2 files changed, 138 insertions(+), 86 deletions(-)
```

## State

Protocol primitives: PARTIAL PASS
Receipt/delta integrity: PASS
Ledger revision semantics: PASS
Persistence: IMPLEMENTED AND TESTED
Tensor FedAvg: IMPLEMENTED, RC5 PATH VALIDATED
Numerical split learning: NOT VALIDATED
RC5.1 global gate: OPEN
RC5.2: NOT STARTED
