# RC5.3 Stage B R5 — Mutation Report (CERTIFIED)

Runner: RUN_RC5_3_MUTATION_GATE.sh
Resultat: Score 12/12 · Invalid 0 · Runtime-invalid 0 · Not applied 0 · Timeouts 0 · Not detected 0
Regla: applied_count != 1 → NOT APPLIED o INVALID (aplicat als 12)

## Per mutant

| ID | Fitxer | applied_count | Assertion | exit | tracebacks | Classificació |
|---|---|---|---|---|---|---|
| MUT-R3-01 INCLUDE SUPERSEDED | rc5_3_multiworker.py | 1 | RV6 | 1 | 0 | DETECTED |
| MUT-R3-02 IGNORE ETT | rc5_3_multiworker.py | 1 | OR3 | 1 | 0 | DETECTED |
| MUT-R3-03 OMIT ONE ACTIVE WORKER | rc5_3_multiworker.py | 1 | OR3 | 1 | 0 | DETECTED |
| MUT-R3-04 APPLY TWICE | rc5_3_multiworker.py | 1 | OR3 | 1 | 0 | DETECTED |
| MUT-R3-05 ACCEPT STALE ADAPTER | rc5_3_multiworker.py | 1 | R3 | 1 | 0 | DETECTED |
| MUT-R3-06 IGNORE WORKER MODEL HASH | rc5_3_multiworker.py | 1 | R5 | 1 | 0 | DETECTED |
| MUT-R3-07 IGNORE BUDGET | rc5_3_multiworker.py | 1 | BE1 | 1 | 0 | DETECTED |
| MUT-R3-08 ROUND2 OLD ADAPTER | rc5_3_multiworker.py | 1 | OR9 | 1 | 0 | DETECTED |
| MUT-R3-09 CROSS WORKER | rc5_3_multiworker.py | 1 | RV4 | 1 | 0 | DETECTED |
| MUT-R3-10 CLOSE EARLY | rc5_3_multiworker.py | 1 | Q1 | 1 | 0 | DETECTED |
| MUT-R3-11 ORDER DEPENDENT | rc5_3_multiworker.py | 1 | OR3 | 1 | 0 | DETECTED |
| MUT-R3-12 ACCEPT DUPLICATE ASSIGNMENT | rc5_3_multiworker.py | 1 | AS3 | 1 | 0 | DETECTED |

## Regles del runner

- mutació aplicada exactament una vegada (replace count=1): aplicat
- py_compile PASS: sí
- baseline PASS: sí
- cap traceback: 0
- cap excepció inesperada: 0
- assertion detectora identificada per mutant: sí
- qualsevol traceback = RUNTIME-INVALID: 0

## Canvis als mutants

- Els 12 mutants reescrits amb PATTERN/SUBST explícits (no buits) i
  replace(..., 1) per garantir applied_count == 1.
- MUT-R3-08: patró amb 3 ocurrències al codi (parent branch, adapter_0 branch,
  oracle); amb count=1 muta exactament la primera (branca parent, la rellevant
  per "round2 old adapter").
- MUT-R3-12: patró actualitzat al control real de request_sha.
