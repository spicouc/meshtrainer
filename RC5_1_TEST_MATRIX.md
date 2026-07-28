# RC5.1 Test Matrix — 33 assertions obligatories

## Assertions per test

| Test | # | Assertions | Categoria |
|---|---|---|---|
| T-R2-01 | 5 | work.request no deadlock (5 iteracions) | Protocol |
| T-R2-02 | 1 | activation no deadlock | Protocol |
| T-R2-03 | 1 | unique ACTIVE per key | Ledger |
| T-R2-04 | 1 | unknown worker rejected | Security |
| T-R2-05 | 1 | wrong token rejected | Security |
| T-R2-06 | 1 | retired key verifies old receipt | Security |
| T-R2-07 | 1 | retired key cannot sign | Security |
| T-R2-08 | 2 | delta_b64 is valid base64, delta_sha256 is 64 hex | Protocol |
| T-R2-09 | 1 | wrong SHA rejected on upload | Integrity |
| T-R2-10 | 1 | nonce not consumed on failed upload | Integrity |
| T-R2-11 | 1 | missing delta blocks round.close | Integrity |
| T-R2-12 | 1 | corrupted delta blocks round.close | Integrity |
| T-R2-13 | 2 | FedAvg result, round.close handles empty | Numerical |
| T-R2-14 | 1 | base + delta | Numerical |
| T-R2-15 | 1 | hashes differ | Integrity |
| T-R2-16 | 1 | round.close returns COMPLETED | Protocol |
| T-R2-17 | 1 | double close rejected | Protocol |
| T-R2-18 | 1 | checkpoint persisted | Persistence |
| T-R2-19 | 3 | session restored, nonces restored, contributions restored | Persistence |
| T-R2-20 | 2 | round closed, 1 contribution aggregated | Protocol |
| R4-expired | 2 | status REJECTED, reason "receipt expired" | Security |
| R4-hash-evidence | 2 | upload accepted, file SHA matches our_hash | Integrity |
| **Total** | **33** | | |

## Categories

| Categoria | Count |
|---|---|
| Protocol | 12 |
| Security | 5 |
| Integrity | 6 |
| Numerical | 3 |
| Ledger | 1 |
| Persistence | 6 |
| **Total** | **33** |
