# PRODUCT MVP PHASE 1 — FINAL REPORT

**Commit**: 55de8fe (+ E0 gate: 6f7a2b1)
**Data**: 2026-08-17
**Executor**: DinDjarin
**Base**: core b146723 CLOSED · multi-model a52580c autoritatiu · Phase 0 CLOSED

---

## 1. Resum

La Fase 1 del PRODUCT/APP MVP està implementada segons el disseny aprovat
(Phase 0) i verificada amb el FINAL GATE complet: APP-01..15, E0-01..04 i el
smoke REAL-QWEN-APP amb el model Qwen3-0.6B real a través exclusivament de
MeshTrainerService/JobRunner (cap driver de test).

## 2. Resultats del gate

| Gate | Resultat |
|---|---|
| pycompile app/ | PASS |
| APP-01..15 (acceptance de producte) | 17/17 PASS |
| E0-01 cancel·lació sense inventar core state | PASS |
| E0-02 dataset upload (UPLOAD-01..05) | PASS |
| E0-03 app restart / runner reconciliation | PASS (3 casos) |
| E0-04 SSE contract | PASS |
| REAL-QWEN-APP (qwen3 real e2e) | PASS |
| core diff vs a52580c | 0 canvis |
| drivers de certificació (generic_distributed_run, generic_recovery_tests) | 0 canvis |
| Dummy hidden in production | PASS |

## 3. E0-01 — Cancel·lació sense inventar core state

Confirmat en codi i test:
- El JobRunner, en cancel·lar, marca CANCELLED a la DB de l'APP.
- El core (training_http_server/Coordinator) NO rep cap operació de
  cancel·lació: no existeix cap mètode "round.cancel" al protocol; el
  run_id cancel·lat no es reutilitza (cada job té run-<job_id> únic).
- Les leases expiren pel TTL natural del LeaseManager (no es crea estat
  CANCELLED al core); no es creen noves assignments; les contribucions en
  vol no s'activen; no hi ha FedAvg de ronda abandonada.
- Test: verifica que la BD del core (core.db) no conté cap round amb
  status CANCELLED després d'una cancel·lació real.

## 4. E0-02 — Dataset upload

Implementat POST /api/datasets/upload (multipart/form-data, FastAPI
UploadFile):
- nom intern generat pel servidor (ds-<uuid>.jsonl); el nom del client NO
  determina el path final (anti path traversal);
- storage controlat (app_storage/uploads/);
- límit de mida configurable (APP_UPLOAD_MAX_MB, default 50 MB);
- SHA-256 calculat i guardat a la metadata;
- posterior DatasetAdapter validation (els 9 checks).

Tests UPLOAD-01..05: vàlid → registered; ../../x → neutralitzat; oversized
→ rejected; SHA metadata == bytes; invalid JSONL → upload possible,
validation FAIL controlat.

## 5. E0-03 — App restart / runner reconciliation

Persistits al job: runner_pid, run_id, runner_started_at, runner_identity
(nonce). A l'arrencada, MeshTrainerService.reconcile_runners():
- Cas A (PID viu + identity + cmdline = el nostre job_runner): reattach;
  el job continua fins COMPLETED.
- Cas B (runner mort): FAILED (no deixa el job RUNNING etern).
- Cas C adversarial (PID viu però identity/cmdline no coincideix): NO
  reattach → FAILED. No es confia només en el PID.

## 6. E0-04 — SSE contract

REST: {ok, data, error}. SSE: Content-Type text/event-stream, cada event
amb id (monotònic), event, data. NO s'embolica en l'envelope REST. Els IDs
són seqüencials per stream (estables/monotònics). Replay amb Last-Event-ID
no implementat (no blocker al MVP, documentat al disseny).

## 7. REAL-QWEN-APP — Smoke qwen3 real

Executat a través exclusivament de MeshTrainerService/JobRunner (cap driver
de test):

- backend: qwen3 (Qwen/Qwen3-0.6B, /root/qwen3_0_6b_snapshot)
- dataset mínim real (6 exemples JSONL), seq_len=32, 1 ronda, LoRA
  Safe/default (r=8, alpha=16, dropout=0.05), 2 workers, device cpu
- evidència: job READY → STARTING → RUNNING → COMPLETED
- workers: model_worker reals (PIDs w-A i w-B, subprocessos)
- adapter_0 present (9.209.691 B, real), adapter_1 present (9.209.694 B)
- ETT per worker > 0 (18 per shard al prepare; 36 totals a r1)
- contribution.submit/validate/activate presents; round.fedavg present
- SHA(bytes) == metadata per a tots els artefactes

Nota de política de recursos: el JobRunner espera que el worker A hagi
carregat (REGISTERED al seu log) abans de spawnar B — evita el doble pic de
LOAD dels safetensors (causa d'OOM global del host). NO serialitza: els dos
workers coexisteixen a la mateixa ronda (quòrum 2/2 del core certificat).

## 8. Core i drivers

- git diff a52580c -- core files: 0 canvis (model_round_coordinator,
  training_http_server, model_worker, training_backend, adapter_codec,
  rc5_4_leases, backends/*).
- generic_distributed_run.py i generic_recovery_tests.py: 0 canvis.

## 9. Riscos / pendents

- PERF-P1 (Phase 3 candidate): expected_ett torna a carregar el backend per
  shard/ronda (funcionalment correcte; no bloqueja).
- El smoke REAL-QWEN va requerir alliberar l'ollama (models "Forever" amb 0
  connexions, causa recurrent d'OOM global del host) — documentat.
- Phase 2 (Web UI): pendent d'autorització.

## 10. Veredicte (pendent de verificació del supervisor)

Si el gate complet és acceptat:
PRODUCT MVP PHASE 1: CLOSED
APPLICATION LAYER: CERTIFIED MVP
API SKELETON: PASS
CLI SKELETON: PASS
DATASET LAYER: PASS
JOB LIFECYCLE: PASS
REAL QWEN APP E2E: PASS
