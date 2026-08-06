# RC5.4 STAGE A R2 — FINAL REPORT (INTEGRACIÓ REAL)

**Data**: 2026-08-06 | **Branch**: rc5.4-stagea | **Base**: d46dc1b9384c8b4aa1646aed2d3c7d6d54a2bd18

## Resum executiu

R2 integra Safe Recovery amb el pipeline RC5.3/RC5.2 real: capa
RC54RecoveryCoordinator que exigeix lease vàlida (8 paràmetres) a totes les
operacions, binding complet del journal, estats autoritzats, màquina
d'estats estricta, exactly-once conflictiu (REJECTED), restart real de
processos i mid-backward real. Cap oracle sintètic (torch.randn, SHA de
strings, adapter inventat): tots els deltas/adapter provenen del pipeline
real.

## Canvis (additius, cap fitxer congelat modificat)

- `rc5_4_integration.py` (nou): RC54RecoveryCoordinator
- `rc5_4_restart_real.py` (nou): restart real de 2 processos
- `rc5_4_mid_backward_real.py` (nou): mid-backward real
- `rc5_4_leases.py`: binding complet, estats, màquina d'estats, exactly-once
  conflictiu, índex parcial únic, expire rowcount=1, TTL>0, expiració <=
- `rc5_4_tests.py` / `rc5_4_adversarial_tests.py`: adaptats + 16 ADV noves
- `mutants_r54/`: 12 mutants nous (MUT-R4-13..24), antics reajustats
- Runners: RUN_RC5_4_RESTART_REAL_GATE.sh, RUN_RC5_4_MID_BACKWARD_GATE.sh,
  RUN_RC5_4_TESTS.sh (normal x2 real)

## Resultats R2

| Gate | Resultat |
|---|---|
| Crash matrix | 24/24 PASS |
| Adversarial (16 noves incl.) | 51/51 PASS |
| Mutation (24 mutants) | 24/24 DETECTED (Invalid 0, Runtime 0, Not_applied 0, Timeouts 0) |
| Restart real (2 processos) | PASS (PID_A != PID_B, mateix delta, adapter igual) |
| Mid-backward real | PASS (A EXPIRED, A no contribueix, B guanya) |
| Normal ×2 real | PASS (Run1 PASS, Run2 PASS, Flaky 0) |
| Combined gate 18 passos | **Overall PASS** |
| Controlled-failure RC5.4 | PASS (1 FAIL, 0 tracebacks, exit 1) |

## Restart real (R8) — evidència

- PID_A=3849458 (procés A: Coordinator+HTTP, worker A fins APPLIED, crash real)
- PID_B=3849563 (procés B: recupera la mateixa SQLite, PID diferent)
- Journal recuperat: APPLIED amb delta 2c8a7dcc7334f53b...
- Delta re-derivat idèntic (determinisme real, cap segon optimizer)
- Adapter final = no-crash: 13cb86871456bbb95937221d6c127e4edb295ef97043b06aad0d3817681d8c1c

## Mid-backward real (R9) — evidència

- Worker A entra en backward, procés cau abans d'APPLIED
- Lease d'A EXPIRED, A no pot continuar, A no pot aportar cap delta parcial
- B adquireix revisió nova (ACTIVE), el seu delta es registra
- Cap delta parcial d'A al journal

## Integritat

- 6 fitxers congelats: hashes idèntics a 9c46f65 (0 diffs)
- Manifest: 241 fitxers coberts = 241 línies, sha256sum -c 0 errors
