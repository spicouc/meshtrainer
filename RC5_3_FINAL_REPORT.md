# RC5.3 Stage B R5 FINAL — Report d'execució

Branca: rc5.3-stageb
Base: a7b7cf87c9c05139b64cc956104063216d8a6ee6
Data: 2026-08-03
Estat: CERTIFIED (gate combinat Overall: PASS)

## Resum executiu

Paquet únic RC5.3 Stage B R5 FINAL. ServerModel_v1 vinculant, transaccions
atòmiques, idempotències (assignments i calibracions), lineage explícit de
rondes, contribucions segures, FedAvg amb oracle independent. RC5.2 R12
tancat formalment dins el mateix paquet.

## Resultats del gate combinat (RUN_RC5_3_FINAL_GATE.sh)

| Subgate | Resultat |
|---|---|
| dep check | PASS |
| py_compile | PASS |
| RC5.3 normal Run 1 | PASS (105/105) |
| RC5.3 normal Run 2 | PASS (105/105) |
| RC5.3 adversarial | PASS (33/33) |
| RC5.3 mutation | PASS (12/12, applied_count=1 x12) |
| RC5.2 normal Run 1 | PASS (49/49) |
| RC5.2 normal Run 2 | PASS (49/49) |
| RC5.2 adversarial | PASS (21/21) |
| RC5.2 mutation | PASS (12/12) |
| Phase 1 Run 1 | PASS (35/35) |
| Phase 1 Run 2 | PASS (35/35) |
| Phase 1 mutation | PASS |
| RC5.1 Run 1 | PASS (76/76) |
| RC5.1 Run 2 | PASS (76/76) |
| RC5.1 mutation | PASS (5/5) |
| restart subprocess | PASS |
| controlled-failure | PASS (suite FAIL detected) |
| manifest | PASS (ALL FILES OK) |
| clean extraction | NOT EXECUTED al working tree (s'executa al tarball) |
| Overall | PASS |

## Canvis principals

1. rc5_2_worker_runtime.py — delta real post−pre (un sol optimizer.step),
   journal per replay exactly-once.
2. rc5_3_multiworker.py — reescriptura completa amb ServerModel_v1 vinculant,
   transaccions atòmiques, request_sha, lineage explícit, contribucions
   segures, oracle independent.
3. rc5_2_http_server.py — step.open exigeix server_model_hash i reconstrueix
   el model des dels bytes persistits.
4. rc5_3_tests.py — 105/105, cobertura seccions 1–8, IDs sense col·lisions
   (state machine renombrada ST1/ST2).
5. rc5_3_adversarial_tests.py — 28 proves ADV-R3-01..28 (33/33 amb sub-checks).
6. rc5_2_phase2_tests.py — U10 expired amb unit COMMITTED real, U11 nonce
   reuse, U12 SHA mismatch, S1 específic del missatge de profile.
7. Runners: mutation RC5.3 amb applied_count real (regla !=1 → NOT APPLIED),
   mutation RC5.2 R12 amb traceback → RUNTIME-INVALID, FINAL GATEs amb
   controlled-failure per còpia temporal, neteja de DBs temporals entre mutants.
