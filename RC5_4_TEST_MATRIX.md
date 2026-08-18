# RC5.4 TEST MATRIX — cobertura completa

**Data**: 2026-08-05 | **Branch**: rc5.4-stagea | **Base**: 9c46f65

## Leases (rc5_4_leases.py)

| Test | Cas | Resultat |
|---|---|---|
| REC-01 | No lease abans de step.open | PASS |
| REC-02 | Crash després de step.open -> lease ACTIVE, retry reusa | PASS |
| REC-03 | Crash després de server activation | PASS |
| REC-04 | Crash abans de backward | PASS |
| REC-05 | Crash a mig backward -> EXPIRED + reassign a B, contribució de B només | PASS |
| REC-06 | Crash després de APPLIED -> mateix delta | PASS |
| REC-07 | Crash després de SUBMITTED -> mateix update_id | PASS |
| REC-08 | Crash després de COMMITTED -> receipt byte-for-byte | PASS |
| REC-09 | Crash després de checkpoint.upload -> mateixa resposta | PASS |
| REC-10 | Crash després d'ACTIVE | PASS |
| REC-11 | Crash durant round.close -> ABORTED | PASS |
| REC-12 | Crash entre Round 1 i Round 2 -> independència de rondes | PASS |
| REC-07b | update_id no overwrite (exactly-once) | PASS |
| REC-08b | receipt no overwrite (byte-for-byte) | PASS |
| REC-09b | checkpoint response no overwrite | PASS |
| REC-10b | unit_key retorna lease més recent (worker B) | PASS |

## Adversarial (rc5_4_adversarial_tests.py)

| Test | Cas | Resultat |
|---|---|---|
| ADV-01 | Lease aliena rebutjada | PASS |
| ADV-02 | Renewal aliena rebutjada | PASS |
| ADV-03 | Renewal després d'expiry rebutjada | PASS |
| ADV-04 | Contribució després d'expiry rebutjada | PASS |
| ADV-05 | Stale lease nonce rebutjat | PASS |
| ADV-06 | Doble lease ACTIVE rebutjada | PASS |
| ADV-07 | Reassignació abans d'expiry rebutjada | PASS |
| ADV-08 | Receipt de unitat expirada rebutjat | PASS |
| ADV-09 | Checkpoint de revisió antiga rebutjat | PASS |
| ADV-10 | Doble recovery APPLIED no fa optimizer | PASS |
| ADV-11 | Recovery amb adapter stale rebutjat | PASS |
| ADV-12 | Round close ignora EXPIRED i ABORTED | PASS |
| ADV-13 | Worker original no completa després de reassignació | PASS |
| ADV-14 | Clock boundary TTL determinista | PASS |
| +13 checks complementaris (27 total) | PASS |

## Mutation (mutants_r54)

| Mutant | Patró substituït | Detecció |
|---|---|---|
| MUT-R4-01 | ignorar expiry | DETECTED |
| MUT-R4-02 | acceptar contribution EXPIRED | DETECTED |
| MUT-R4-03 | doble lease ACTIVE | DETECTED |
| MUT-R4-04 | renewal aliena | DETECTED |
| MUT-R4-05 | ignorar lease_nonce | DETECTED |
| MUT-R4-06 | reassignar abans d'expiry | DETECTED |
| MUT-R4-07 | doble optimizer en APPLIED | DETECTED |
| MUT-R4-08 | receipt diferent després de restart | DETECTED |
| MUT-R4-09 | acceptar adapter stale | DETECTED |
| MUT-R4-10 | incloure EXPIRED al FedAvg | DETECTED |
| MUT-R4-11 | commit de revisió antiga | DETECTED |
| MUT-R4-12 | recuperar mid-backward com a segur | DETECTED |

**Score: 12/12 DETECTED | Invalid 0 | Runtime 0 | Not_applied 0 | Timeouts 0**

## Regressions congelades (gate combinat)

| Suite | Resultat |
|---|---|
| RC5.4 normal ×2 | PASS |
| RC5.4 adversarial | PASS |
| RC5.4 mutation | PASS |
| RC5.3 combined (105/105, adv 33/33, mut 12/12, RC5.2, Phase1, RC5.1) | PASS |
| RC5.2 Phase 2 | PASS |
| Phase 1 | PASS |
| RC5.1 | PASS |
| Restart subprocess real | PASS |
| Controlled-failure RC5.3 (W1) | PASS |
| Controlled-failure RC5.2 (J4) | PASS |
| Controlled-failure RC5.4 (REC-01) | PASS |
| Dependency probe | PASS |
| Manifest probe | PASS |
| Manifest complet (224 = 224, err 0) | PASS |
