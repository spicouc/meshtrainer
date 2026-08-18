# RC5.4 MUTATION REPORT — R3.1 (37 mutants)

**Data**: 2026-08-07 | **Resultat: 37/37 DETECTED | Invalid 0 | Runtime 0 | Not_applied 0 | Timeouts 0 | PASS**

## MUT-R4-01..34 (R3, revalidats)
Tots DETECTED — vegeu l'informe R3.

## MUT-R4-35..37 (nous, R3.1 — idempotència del dispatcher)

| Mutant | Patró substituït | Detecció |
|---|---|---|
| MUT-R4-35 | eliminar _check_idem (cached=None) | DETECTED (H-18..H-22 fallen: retry no idempotent) |
| MUT-R4-36 | cache abans del lease gate | DETECTED (H-25 falla: retry cachejat acceptat) |
| MUT-R4-37 | acceptar same key + different payload (cached=None) | DETECTED (H-24 falla: segona operació creada) |

## Evidència per mutant

Cada mutant registra: patró, substitució, ocurrències, applied_count,
py_compile, crash=/adv=/http= (exits separats), tracebacks, TIMEOUT si
qualsevol == 124, i l'assertion detectora exacta (FAIL H-xx). Log complet:
evidence/RC5_4_MUTATION_GATE.log.

## Classificació (runner R3.1)

1. qualsevol exit == 124 -> TIMEOUT (no es converteix a 1)
2. tracebacks > 0 -> RUNTIME-INVALID
3. exit no-zero + assertion FAIL -> DETECTED
4. exit 0 -> NOT DETECTED
