# RC5.4 FINAL REPORT — Stage A (Safe Recovery)

**Data**: 2026-08-05
**Branch**: rc5.4-stagea
**Base certificada**: 9c46f65ea288fcbb351d4744fa1cec4e2ec11d64 (RC5.3 R6.1)
**Autorització**: Finalització RC5.4 Stage A AUTHORIZED pel Supervisor

## Resum executiu

RC5.4 Stage A implementa **Safe Recovery** per al pipeline multiworker:
leases persistents, expiració/reassignació, exactly-once del journal
(PREPARED/APPLIED/SUBMITTED/COMMITTED), i crash matrix completa.
No requereix persistir el graf autograd; els deltas es regeneren de manera
determinista (seed derivada de run/round/assignment/unit/worker).

## Canvis respecte a la base 9c46f65 (additius, cap fitxer congelat modificat)

- `rc5_4_leases.py` (nou): LeaseManager + taula leases_r54 + recovery_journal_r54
- `rc5_4_tests.py` (nou): crash matrix REC-01..12 (24 checks)
- `rc5_4_adversarial_tests.py` (nou): 14 probes mínimes (27 checks)
- `mutants_r54/mut_r4_01..12.py` (nous): 12 mutants PATTERN/SUBST
- Runners nous: RUN_RC5_4_TESTS.sh, RUN_RC5_4_ADVERSARIAL_GATE.sh,
  RUN_RC5_4_MUTATION_GATE.sh, RUN_RC5_4_RESTART_GATE.sh,
  RUN_RC5_4_CONTROLLED_FAILURE.sh, RUN_RC5_4_CLEAN_EXTRACTION_GATE.sh,
  RUN_RC5_4_FINAL_GATE.sh
- `MANIFEST.sha256` regenerat: 224 fitxers coberts = 224 línies

## Resultats

| Gate | Resultat |
|---|---|
| Crash matrix | 24/24 PASS |
| Adversarial | 27/27 PASS |
| Mutation | 12/12 DETECTED (Invalid 0, Runtime 0, Not_applied 0, Timeouts 0) |
| Combined gate (17 passos) | **Overall PASS** |
| Restart real | PASS (PID_A != PID_B) |
| Controlled-failure RC5.4 | PASS (1 sola FAIL, 0 tracebacks, exit 1) |

## Integritat dels fitxers congelats

Els 6 fitxers del nucli (rc5_3_multiworker.py, rc5_3_tests.py,
rc5_3_adversarial_tests.py, rc5_2_worker_runtime.py, rc5_2_http_server.py,
rc5_2_phase2_tests.py) tenen hashes idèntics als del commit 9c46f65
(verificat amb sha256sum contra el MANIFEST de 9c46f65).

## Stop conditions

Cap condició d'aturada activada:
1. No cal persistir el graf autograd (journal de deltas deterministes) ✓
2. No s'ha modificat el kernel numèric congelat ✓
3. No es trenca RC5.3 (regressions PASS) ✓
4. Exactly-once en APPLIED garantit (testejat REC-07b, ADV-10) ✓
5. La reassignació no permet dues contribucions ACTIVE ✓
6. Rellotge/TTL determinista (rellotge injectable) ✓
