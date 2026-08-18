# RC5.4 STAGE A R3.1 — FINAL REPORT (IDEMPOTÈNCIA + JOURNAL ÚNIC)

**Data**: 2026-08-07 | **Branch**: rc5.4-stagea | **Base**: R3 candidat (f726742)

## Resum executiu

R3.1 corregeix exclusivament: (1) idempotència del dispatcher HTTP RC5.4,
(2) journal RC54 persistent amb UNA sola font de veritat, (3) mutation runner
HTTP, (4) evidència final. Cap fitxer congelat modificat.

## 1. Idempotència del dispatcher (ProtocolHandler54.handle)

Ordre obligatori restaurada:

    LEASE VALIDATION -> IDEMPOTENCY CHECK -> EXECUTION -> IDEMPOTENCY SAVE

- El lease gate SEMPRE va abans de consultar la cache: lease expirada + retry
  cachejat -> REJECTED (H-25).
- Retry exacte (mateix logical_key + mateix SHA) -> mateixa resposta exacta
  (H-18..H-23).
- Mateix logical_key + SHA diferent -> REJECTED (H-24), sense crear una
  segona unitat (H-24b).
- S'usen les funcions certificades _logical_key()/_check_idem()/_save_idem()
  (cap duplicació).

## 2. Tests d'idempotència HTTP: 26/26 PASS

H-01..H-16 (R3) + H-18 step.open exact retry, H-19 forward exact retry
(mateix backward_id), H-20 backward exact retry (mateix gradient), H-21 update
exact retry (mateix delta_id), H-22 commit exact retry (receipt byte-for-byte),
H-23 upload exact retry (mateixa contribution_id), H-24 same key + different
payload REJECTED, H-24b sense segona unitat, H-25 retry exacte post-expiració
REJECTED (certifica lease gate BEFORE cache).

## 3. Journal autoritatiu únic

- journal_r54 (integration.py) DEPRECAT i eliminat del schema.
- Tots els mètodes (journal_write/journal_read/journal_state) DELEGUEN a
  recovery_journal_r54 (LeaseManager.journal_set/journal_get).
- UNA sola state machine autoritativa (transicions + exactly-once conflictiu).

## 4. _tx() corregit

Disciplina provada de LeaseManager: outer = conn.in_transaction abans del
BEGIN; COMMIT/ROLLBACK explícits només si no érem outer. La versió anterior
no feia COMMIT (in_transaction era True després del BEGIN).

Prova REC-13 (crash matrix, 27/27):
- 13a: PREPARED visible des d'una segona connexió SQLite
- 13b: PREPARED sobreviu a tancar/reobrir el procés
- 13c: rollback davant error -> zero escriptures parcials

## 5. Mutation runner RC5.4

- RS_CRASH / RS_ADV / RS_HTTP capturats independentment.
- Regla prioritària: qualsevol == 124 -> TIMEOUT (mai convertit a 1).
- tracebacks > 0 -> RUNTIME-INVALID.
- exit no-zero + assertion FAIL identificada -> DETECTED.
- L'assertion es busca també a run_http.log.
- Report: crash=<exit> adv=<exit> http=<exit> + assertion detectora (H-xx).

## 6. Mutants nous d'idempotència

| Mutant | Patró | Demostra |
|---|---|---|
| MUT-R4-35 | eliminar _check_idem | el retry exacte no és idempotent (H-18..22 fallen) |
| MUT-R4-36 | cache abans del lease gate | retry cachejat amb lease expirada acceptat (H-25 falla) |
| MUT-R4-37 | acceptar same key + different payload | payload conflictiu crea una segona operació (H-24 falla) |

**Mutation gate: 37/37 DETECTED, Invalid 0, Runtime 0, Not_applied 0, Timeouts 0.**

## 7. Restart regression

El restart real manté: recupera el delta APPLIED DIRECTAMENT del journal
(delta_bundle_b64), no executa cap segon optimizer, i ara comprova
explícitament que el journal autoritatiu és visible des del procés B amb una
connexió SQLite FRESCA (independent de la del procés A que va morir amb
os._exit).

## 8. Regressions (gate combinat 18 passos)

| Pas | Resultat |
|---|---|
| dependency + py_compile | PASS |
| RC5.4 normal x2 | PASS |
| RC5.4 adversarial | PASS |
| RC5.4 mutation | 37/37 PASS |
| HTTP lease adversarial | 26/26 PASS |
| RC5.3 combined | Overall PASS |
| RC5.2 Phase 2 / Phase 1 / RC5.1 | PASS |
| restart real / mid-backward real | PASS |
| CF RC5.3 / RC5.2 / RC5.4 | PASS |
| dependency probe / manifest probe | PASS |
| manifest | PASS (260=260, err=0) |

## Integritat

- 6 fitxers congelats: hashes idèntics a 9c46f65 (0 diffs).
- Manifest: 260 fitxers coberts = 260 línies, sha256sum -c 0 errors.
