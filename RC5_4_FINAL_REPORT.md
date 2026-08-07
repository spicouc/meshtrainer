# RC5.4 STAGE A R3 — FINAL REPORT (INTEGRACIÓ HTTP REAL)

**Data**: 2026-08-07 | **Branch**: rc5.4-stagea | **Base**: 086c030

## Resum executiu

R3 fa obligatòries les leases DINS del pipeline real: el servidor HTTP
(rc5_4_http_server.py) governa el dispatcher JSON-RPC de les 6 operacions
(step.open, worker_forward.submit, server_backward.fetch, worker_update.submit,
step.commit, checkpoint.upload) a través de RC54RecoveryCoordinator. Cap
operació sense lease o amb lease caducada arriba als handlers RC5.2:
REJECTED. Cap oracle sintètic (torch.randn, SHA de strings, adapter inventat).

## Canvis (additius, cap fitxer congelat modificat)

- `rc5_4_http_server.py` (nou): ProtocolHandler54 + RpcHandler54 + serve54 —
  dispatcher lease-gated; el gate de lease va ABANS de la idempotència
  (un retry idempotent no pot saltar-se el check de lease).
- `rc5_4_integration.py`: register_uploaded_contribution amb signatura nova
  (keyword-only, sempre _check_lease); connecta amb el dispatcher.
- `rc5_4_http_lease_tests.py` (nou): 17 probes HTTP reals (sense _FakePipeline).
- `rc5_4_leases.py`: columna `delta_bundle_b64` al journal (recovery del delta
  directament del journal, sense re-executar optimizer).
- `rc5_4_restart_real.py`: totes les peticions HTTP amb lease; fase B recupera
  el delta del journal; oracle de recomputació separat (mai comptat com a
  recovery); FedAvg real del coordinator.
- `rc5_4_mid_backward_real.py`: mid-backward real per HTTP; A rep REJECTED a
  backward/update/commit/upload amb lease expirada.
- `mutants_r54/`: 10 mutants nous MUT-R4-25..34 (dispatcher real).
- Runners: RUN_RC5_4_HTTP_LEASE_GATE.sh (nou).

## Resultats R3

| Gate | Resultat |
|---|---|
| Crash matrix | 24/24 PASS |
| Adversarial (51) | 51/51 PASS |
| HTTP lease adversarial (17) | 17/17 PASS |
| Mutation (34 mutants) | 34/34 DETECTED (Invalid 0, Runtime 0, Not_applied 0, Timeouts 0) |
| Restart real governat | PASS (PIDs diferents, delta del journal, adapter igual) |
| Mid-backward real HTTP | PASS (A REJECTED backward/update/commit/upload) |
| Gate combinat 20 passos | **Overall PASS** |
| Controlled-failure RC5.4 | PASS (1 FAIL, 0 tracebacks, exit 1) |

## Requisit central de l'ordre

"RC5.4 només quedarà tancada quan el servidor HTTP rebutgi realment qualsevol
operació sense lease o amb lease caducada."

VERIFICAT: el dispatcher HTTP (ProtocolHandler54.handle) valida la lease
ABANS d'executar l'operació i ABANS de la idempotència. Proves reals per HTTP:
H-01 sense lease rejected, H-06/07/08/09 expired lease forward/backward/
commit/upload rejected, H-10 released rejected, H-11 old worker rejected.

## Restart real governat — evidència

- PID_A=396501 (procés A: HTTP amb lease fins APPLIED, crash real)
- PID_B=396544 (procés B: recupera la MATEIXA SQLite, PID diferent)
- Delta recuperat DIRECTAMENT del journal (delta_bundle_b64), cap optimizer
  re-executat per recuperar-lo; oracle de recomputació separat i coincident
- Adapter final = no-crash: 13cb86871456bbb95937221d6c127e4edb295ef97043b06aad0d3817681d8c1c

## Mid-backward real — evidència

- A entra en backward, procés cau abans d'APPLIED; lease expirada pel TTL
- A rep REJECTED per HTTP: backward=True, update=True, commit=True, upload=True
- A no pot continuar ni aportar cap delta parcial; B completa amb lease nova

## Integritat

- 6 fitxers congelats: hashes idèntics a 9c46f65 (0 diffs)
- Manifest: 256 fitxers coberts = 256 línies, sha256sum -c 0 errors
