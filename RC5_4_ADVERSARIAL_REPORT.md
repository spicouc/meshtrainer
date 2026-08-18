# RC5.4 ADVERSARIAL REPORT — R3.1 (51/51 + HTTP 26/26)

**Data**: 2026-08-07 | **Resultat: 51/51 (unit) + 26/26 (HTTP) PASS**

## Suite unitària (rc5_4_adversarial_tests.py): 51/51 PASS
ADV-01..14 originals + ADV-R4-15..30 (R2).

## Suite HTTP real (rc5_4_http_lease_tests.py): 26/26 PASS
H-01..H-16 (R3, lease-gated) + H-18..H-25 (R3.1, idempotència):

| Test | Cas | Resultat |
|---|---|---|
| H-18 | step.open exact retry -> mateixa resposta | PASS |
| H-19 | forward exact retry -> mateix backward_id, no segon backward | PASS |
| H-20 | backward exact retry -> mateix gradient envelope | PASS |
| H-21 | update exact retry -> mateix delta_id | PASS |
| H-22 | commit exact retry -> receipt byte-for-byte | PASS |
| H-23 | upload exact retry -> mateixa contribution_id | PASS |
| H-24 | same logical key + different payload REJECTED | PASS |
| H-24b | no es crea una segona unitat (mateixa clau lògica) | PASS |
| H-25 | retry exacte després d'expirar -> REJECTED (lease gate BEFORE cache) | PASS |

## Nota de disseny (R3.1)
L'ordre LEASE -> IDEMPOTENCY CHECK -> EXECUTION -> IDEMPOTENCY SAVE garanteix
que un retry idempotent mai no pugui saltar-se el gate de lease (H-25) i que
un retry exacte amb lease vàlida torni la mateixa resposta (H-18..H-23).
