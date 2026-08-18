# PRODUCT MVP PHASE 2 — FINAL REPORT (WEB UI)

**Commit funcional**: 0bdb926 (Web UI + API additiva + UI-01..25 + screenshots)
**Data**: 2026-08-18
**Executor**: DinDjarin (pve/Mandalor)
**Base**: core b146723 CLOSED/FROZEN · multi-model a52580c CERTIFIED/FROZEN
**Phase 1**: 4696d35 (tarball 004f7459) — CLOSED

---

## 1. Objectiu complert

Primera interfície web usable de MeshTrainer: un usuari obre el navegador i pot
veure l'estat del sistema, els backends (Qwen3/MiniCPM5), afegir/pujar datasets,
validar-los, crear un training job amb wizard de 5 passos (model → dataset →
config LoRA → workers → review), validar-lo, iniciar l'entrenament, veure'l
evolucionar en directe (SSE), cancel·lar-lo, veure logs i mètriques, i
descarregar els artefactes finals.

## 2. Arquitectura (complint la regla principal)

```
Browser (vanilla JS, zero toolchain)
   ↓ REST + SSE (contracte E0-04)
FastAPI (app/api/main.py)
   ↓
MeshTrainerService (app/services/)
   ↓
JobRunner (procés separat, parla JSON-RPC HTTP amb el core)
   ↓
Certified MeshTrainer Core (b146723 — INALTERAT)
```

La Web UI **no importa core Python**, no llegeix la SQLite del Coordinator, no
executa workers ni drivers, no llegeix fitxers interns arbitràriament. Només
parla amb l'Application API + SSE.

## 3. Canvis (tots additius a app/, zero canvis al core)

| Fitxer | Canvi |
|---|---|
| `app/api/main.py` | +`/api/system`, `/api/settings`, `/api/artifacts/{id}/download` (anti path traversal), `format=json` a events; serveix `app/web/` estàtic + rutes SPA |
| `app/services/mesh_trainer_service.py` | `get_system_info()`, `get_settings()` (sense secrets), `get_artifact_download()` (valida pertinença + path), camps de lectura derivats a `get_job` (rounds_progress, loss_history, ett_total, recoveries) |
| `app/web/` (14 fitxers nous) | index.html + css + 12 mòduls JS (api, state, ui, dashboard, datasets, jobs-wizard, monitor, logs, artifacts, settings, main) — vanilla, hash routing, sense framework |
| `app/tests/ui_e2e_tests.py` | Suite Playwright UI-01..25 |
| `app/tests/ui_screenshots.py` | Captura dels 10 screenshots reals |
| `app/tests/real_qwen_ui_test.py` | Smoke qwen3 real des del navegador + evidència |

## 4. Core congelat — verificació

```
git diff --quiet a52580c -- model_round_coordinator.py training_http_server.py \
    model_worker.py training_backend.py adapter_codec.py rc5_4_leases.py \
    backends/qwen3_backend.py backends/dummy_backend.py backends/minicpm5_backend.py \
    generic_distributed_run.py generic_recovery_tests.py
→ 0 canvis (verificat al gate)
```

## 5. Resultats del gate Phase 2

| Check | Resultat |
|---|---|
| pycompile app/ | PASS |
| APP-01..15 (Phase 1 regression) | 17/17 PASS |
| E0-01..04 (Phase 1 regression) | 31/31 PASS |
| UI-01..25 (Playwright, dummy APP_ENV=test) | 25/25 PASS |
| REAL-QWEN-UI (qwen3 real des del navegador) | PASS |
| core unchanged (b146723/a52580c) | PASS (0 canvis) |
| drivers unchanged | PASS (0 canvis) |
| Dummy hidden production | PASS |
| no secrets | PASS (cap token a localStorage/URL/logs; settings només "configured") |
| no external CDN dependency | PASS (tot local, offline) |

## 6. UI-01..25 — detall

UI-01 dashboard loads · UI-02 backends visible · UI-03 Dummy hidden production
· UI-04 datasets page · UI-05 upload valid JSONL · UI-06 invalid dataset errors
· UI-07 create job wizard · UI-08 cannot start invalid job · UI-09 valid job
can start · UI-10 job monitor receives SSE · UI-11 worker cards update · UI-12
timeline updates · UI-13 logs isolated · UI-14 cancel workflow · UI-15 CANCELLED
displayed · UI-16 completed artifacts displayed · UI-17 SHA displayed · UI-18
artifact download · UI-19 SSE reconnect no duplicate · UI-20 same-second events
all displayed · UI-21 API failure visible · UI-22 backend unavailable visible ·
UI-23 mobile viewport usable · UI-24 keyboard navigation basic · UI-25 XSS
payload rendered as text.

## 7. REAL-QWEN-UI — evidència

Job COMPLETED amb qwen3 real creat/validat/executat DES DEL NAVEGADOR:
submit+validate+activate+fedavg presents, ETT [16, 20] (coincideix amb el
previst), adapter_0 SHA 5c05f972 (idèntic a la certificació Phase 1 —
determinisme reproduït), adapter_1 d1fb41e4, UI mostra l'artifact final.
Evidència completa a `evidence_phase2/REAL_QWEN_UI.log` + `.json`.

## 8. Screenshots reals (10)

01_dashboard · 02_new_job_model · 03_dataset_validation · 04_training_config ·
05_job_running · 06_workers · 07_metrics · 08_logs · 09_completed_artifacts ·
10_mobile — tots capturats de la UI implementada (dummy, APP_ENV=test).

## 9. Incidències resoltes

1. **OOM global del host (recurrent)**: el CT112 tenia límit de memòria 16 GB
   en un host de 15,7 GB físics → el smoke qwen3 real (prepare + expected_ett +
   2 workers f32) va fer pic i Proxmox va aturar el CT112. **Fix**: `pct set 112
   -memory 12288` (12 GB) perquè el cgroup aturi el procés excessiu abans que
   el host; + swap del host com a coixí. Lliçó R1.2 aplicada al producte.
2. **SSE no arribava a la UI**: el servidor emetia `event: round.create` (nom
   propi) que EventSource no entrega a `onmessage` (no hi ha catch-all).
   **Fix**: emetre `event: message` i portar el tipus dins del data (el
   contracte E0-04 id/event/data continua complint-se).
3. **Cancel des de la UI**: el refresh post-cancel passava l'objecte del job en
   lloc del job_id → `GET /api/jobs/[object Object]`. **Fix**: `refresh(jobId)`
   + `renderHeader(j)` per actualitzar el badge d'estatus en viu.
4. **Wizard pas 2**: el botó Continue no s'habilitava en seleccionar dataset →
   fix `enableNext()` al change del select.
5. **Selector Playwright UI-22**: `text=available, text=unavailable` no és un
   selector vàlid → espera separada.
6. **Bug SSL CT112** (SSL_CERT_FILE inexistent) → `unset` als scripts d'E2E.
7. **UI-03 fals positiu**: el servidor de production compartia la DB amb els
   jobs dummy del test → DB separada per al check.

## 10. Riscos oberts / notes

- `expected_ett` del JobRunner carrega el backend per shard/ronda → registrat
  com a PERF-P1 / Phase 3 candidate (no optimitzat, com va ordenar el
  supervisor).
- El worker_pids de l'evidència REAL-QWEN-UI s'extreu dels logs dels workers
  (format `[w-A] <pid> REGISTERED`).
- La UI no és font de veritat: en reconnectar SSE fa resincronització via REST.

## 11. Estat

Amb tot PASS: **PRODUCT MVP PHASE 2: CLOSED · WEB UI: CERTIFIED MVP ·
BROWSER → API → APPLICATION → CORE: PASS · REAL QWEN VIA WEB UI: PASS**
→ autoritza **PRODUCT MVP PHASE 3 (HARDENING + UX POLISH + INSTALL/LAUNCH +
FINAL MVP PACKAGE)**.
