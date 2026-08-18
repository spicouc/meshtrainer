# RC5.1 — Final Report — Formal Closeout

## Commit funcional auditat

9b9aac1153ca561708c114b10d748cf81ffe798c

## Commit documental

(SHA del commit actual)

## Resultats

| Gate | Resultat |
|---|---|
| Suite base | 45/45 PASS |
| Suite R10 | 31/31 PASS |
| Total per execucio | 76/76 PASS |
| Run 1 | PASS |
| Run 2 | PASS |
| Mutation gate | 5/5 |
| Mutants invalids | 0 |
| Mutants no aplicats | 0 |
| Timeouts | 0 |
| Flaky | 0 |
| Skips | 0 |

## Estats

| Aspecte | Estat |
|---|---|
| Protocol primitives | PASS |
| Receipt/delta integrity | PASS |
| Ledger revision semantics | PASS |
| Persistence | IMPLEMENTED AND TESTED |
| Tensor FedAvg | IMPLEMENTED, RC5 PATH VALIDATED |
| Numerical split learning | NOT VALIDATED — transferred to RC5.2 |
| RC5.1 implementation | PASS |
| RC5.1 protocol and integrity gate | PASS |

## Correccions implementades (P0)

- T-00: Paquet reproduible amb dependencies RC3
- T-01: Delta LoRA capturat abans de optimizer.step
- T-02: delta_sha256 al payload HMAC
- T-03: SUPERSEDED nomes a handle_activate_contribution
- T-04: or True eliminat, hash 64 chars
- T-05: Tests de seguretat complets
- T-06: COUNTS dict persistent
- T-07: Runner amb timeout
- T-08: Informe final

## R10 corrections

- Exact reason == checks (R6-03-1..8)
- Receipt sense delta_sha256 re-signat (R6-03-3)
- Mateix receipt+nonce per retry (R6-03-10a/b/c)
- Valid payloads per MUT-04/05
- MUT-02 Python multiline replacement
- SQL constraint detection in mutation runner

## Tag

meshtrainer-v1.1-rc5.1

## Dependencies

Totes les dependencies RC3 incloses al paquet.

## Estat del projecte

RC5.1: CLOSED
RC5.2: NOT STARTED — pending ADR

## Dades tècniques

Python: 3.13.5
Torch: 2.x
SQLite: 3.x
OS: Debian 13.1 (CT112)
