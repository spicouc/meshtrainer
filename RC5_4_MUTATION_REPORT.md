# RC5.4 MUTATION REPORT — R3 (34 mutants)

**Data**: 2026-08-07 | **Resultat: 34/34 DETECTED | Invalid 0 | Runtime 0 | Not_applied 0 | Timeouts 0 | PASS**

## MUT-R4-01..24 (R2, revalidats amb el dispatcher real)
Tots DETECTED (24/24) — vegeu l'informe R2.

## MUT-R4-25..34 (nous, R3 — dispatcher real / check_lease)

| Mutant | Patró substituït | Detecció |
|---|---|---|
| MUT-R4-25 | bypass lease a step.open (dispatcher) | DETECTED (H-01) |
| MUT-R4-26 | bypass lease a backward (dispatcher) | DETECTED (H-13) |
| MUT-R4-27 | bypass lease a commit (dispatcher) | DETECTED (H-14) |
| MUT-R4-28 | bypass lease a upload (dispatcher) | DETECTED (H-15) |
| MUT-R4-29 | bypass lease a contribution registration | DETECTED (H-12b) |
| MUT-R4-30 | acceptar EXPIRED (check_lease) | DETECTED (H-06/07/08/09) |
| MUT-R4-31 | acceptar RELEASED (check_lease) | DETECTED (H-10) |
| MUT-R4-32 | ignorar session (check_lease) | DETECTED (H-03) |
| MUT-R4-33 | ignorar revision (check_lease) | DETECTED (H-16) |
| MUT-R4-34 | acceptar old worker després de reassignació | DETECTED (H-11) |

## Evidència per mutant

Cada mutant registra: patró, substitució, ocurrències, applied_count,
py_compile, exit per suite (crash/adv/http), tracebacks i l'assertion exacta
que falla. Log complet: evidence/RC5_4_MUTATION_GATE.log.
