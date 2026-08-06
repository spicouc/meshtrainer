# RC5.4 MUTATION REPORT — R2 (24 mutants)

**Data**: 2026-08-06 | **Resultat: 24/24 DETECTED | Invalid 0 | Runtime 0 | Not_applied 0 | Timeouts 0 | PASS**

## MUT-R4-01..12 (originals, reajustats als patrons del codi endurit)

| Mutant | Patró substituït | Detecció |
|---|---|---|
| MUT-R4-01 | ignorar expiry (`now = self._now_f() - 1e9`) | DETECTED (ADV-03) |
| MUT-R4-02 | acceptar contribució EXPIRED (status EXPIRED permès) | DETECTED (ADV-04) |
| MUT-R4-03 | doble lease ACTIVE (índex únic eliminat) | DETECTED (ADV-R4-15b) |
| MUT-R4-04 | renewal aliena | DETECTED (ADV-02) |
| MUT-R4-05 | ignorar lease_nonce | DETECTED (ADV-05) |
| MUT-R4-06 | reassignar abans d'expiry | DETECTED (ADV-07) |
| MUT-R4-07 | doble optimizer (SUBMITTED sense delta) | DETECTED (ADV-R4-26b) |
| MUT-R4-08 | receipt diferent acceptat | DETECTED (ADV-R4-27) |
| MUT-R4-09 | adapter stale acceptat | DETECTED (ADV-11) |
| MUT-R4-10 | EXPIRED al FedAvg | DETECTED (REC-05) |
| MUT-R4-11 | commit revisió antiga | DETECTED (ADV-13) |
| MUT-R4-12 | mid-backward com a segur (no expira mai) | DETECTED (REC-05) |

## MUT-R4-13..24 (nous, R2)

| Mutant | Patró substituït | Detecció |
|---|---|---|
| MUT-R4-13 | eliminar run binding | DETECTED (ADV-R4-15) |
| MUT-R4-14 | eliminar round binding | DETECTED (ADV-R4-16) |
| MUT-R4-15 | eliminar assignment binding | DETECTED (ADV-R4-17) |
| MUT-R4-16 | eliminar unit binding | DETECTED (ADV-R4-18) |
| MUT-R4-17 | eliminar session binding | DETECTED (ADV-R4-19) |
| MUT-R4-18 | permetre RELEASED al journal | DETECTED (ADV-R4-20) |
| MUT-R4-19 | permetre state regression | DETECTED (ADV-R4-25) |
| MUT-R4-20 | acceptar conflicting receipt | DETECTED (ADV-R4-27) |
| MUT-R4-21 | acquire després de RELEASED | DETECTED (ADV-R4-21) |
| MUT-R4-22 | expiry amb `<` en lloc de `<=` | DETECTED (ADV-R4-23) |
| MUT-R4-23 | ignorar rowcount d'expire | DETECTED (ADV-R4-23b) |
| MUT-R4-24 | pipeline sense lease | DETECTED (ADV-R4-29) |
