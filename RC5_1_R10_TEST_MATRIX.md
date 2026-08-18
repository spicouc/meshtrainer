# RC5.1-R10 Test Matrix — 76 assertions

## Base Suite (rc5_tests.py) — 45 assertions

| Test | Count | Category |
|---|---|---|
| T-R2-01: work.request no deadlock | 5 | protocol |
| T-R2-02: activation no deadlock | 1 | protocol |
| T-R2-03: unique ACTIVE per key | 1 | ledger |
| T-R2-04: unknown worker rejected | 1 | security |
| T-R2-05: wrong token rejected | 1 | security |
| T-R2-06: retired key verifies | 1 | security |
| T-R2-07: retired key cannot sign | 1 | security |
| T-R2-08: delta_b64 format | 2 | protocol |
| T-R2-09: wrong SHA rejected | 1 | integrity |
| T-R2-10: nonce rollback | 1 | integrity |
| T-R2-11: no-delta blocks round.close | 1 | integrity |
| T-R2-12: corrupted delta blocks | 1 | integrity |
| T-R2-13: FedAvg numeric | 2 | numerical |
| T-R2-14: base + delta | 1 | numerical |
| T-R2-15: hashes differ | 1 | integrity |
| T-R2-16: round.close COMPLETED | 1 | protocol |
| T-R2-17: double close rejected | 1 | protocol |
| T-R2-18: checkpoint persisted | 1 | persistence |
| T-R2-19: restart recovery | 3 | persistence |
| T-R2-20: manifest correct | 2 | protocol |
| R4-expired: expiry rejection | 2 | security |
| R4-hash-evidence: SHA match | 2 | integrity |
| R5-08-fedavg: RC5 path | 6 | numerical |
| MUT-1: HMAC check | 1 | security |
| MUT-2: ownership check | 1 | security |
| MUT-3: nonce replay | 2 | security |
| MUT-4: delta SHA check | 1 | security |
| MUT-5: RC3 join check | 1 | security |
| **Total base** | **45** | |

## R10 Suite (r6_tests.py) — 31 assertions

| Test | Count | Category |
|---|---|---|
| R6-01: E2E Split Server flow | 9 | e2e |
| R6-02-A: v1 ACTIVE, v2 RECEIVED | 2 | ledger |
| R6-02-B: validate v2 | 2 | ledger |
| R6-02-C: activate v2 | 2 | ledger |
| R6-02-D: v3 not activated | 2 | ledger |
| R6-03-1: SHA diff | 1 | integrity |
| R6-03-2: bytes diff | 1 | integrity |
| R6-03-3: SHA absent | 1 | integrity |
| R6-03-4: 63 chars | 1 | integrity |
| R6-03-5: non-hex | 1 | integrity |
| R6-03-6: b64 absent | 1 | integrity |
| R6-03-7: bad b64 | 1 | integrity |
| R6-03-8: byte alter | 1 | integrity |
| R6-03-9: nonces preserved | 1 | integrity |
| R6-03-10a: first fail nonce | 1 | integrity |
| R6-03-10b: success nonce | 1 | integrity |
| R6-03-10c: retry OK | 1 | integrity |
| R6-05-1: alien -32003 | 1 | security |
| R6-05-2: illegal transition | 1 | security |
| **Total R10** | **31** | |

## Grand Total: 76 assertions

| Category | Count |
|---|---|
| protocol | 12 |
| security | 13 |
| integrity | 15 |
| numerical | 9 |
| ledger | 10 |
| persistence | 4 |
| e2e | 9 |
| **Total** | **76** |
