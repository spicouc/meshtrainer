# CHANGELOG — meshtrainer

## v1.3.0 (2026-08-18) — Product MVP Phase 2 (Web UI) CLOSED

- **Web UI** (`app/web/`, vanilla JS, zero toolchain): dashboard amb sistema/backends/jobs, wizard de 5 passos per crear training jobs, monitor SSE en viu (timeline, worker cards, mètriques, gràfica de loss), logs filtrats, artefactes descarregables, settings.
- **API additiva**: `/api/system`, `/api/settings`, `/api/artifacts/{id}/download` (anti path traversal), `format=json` per events (resincronització).
- **Gate Phase 2**: UI-01..25 **27/27 PASS** (Playwright) · APP **17/17** · E0 **31/31** · REAL-QWEN-APP **16/16** · REAL-QWEN-UI **PASS** · CE A/B **13/13** · core/drivers **0 canvis**.
- REAL-QWEN-UI: qwen3 real executat des del navegador, adapter SHA `5c05f972` idèntic a la certificació (determinisme).

## v1.2.0 (2026-08-17) — Product MVP Phase 1 (Application Layer) CLOSED

- `app/`: `MeshTrainerService` (façana), `JobRunner` (orquestrador JSON-RPC amb el core), persistència SQLite, dataset layer (JSONL + validació), API skeleton FastAPI (19 endpoints + SSE), CLI skeleton (11 subcomandes).
- E0-01 cancel·lació cooperativa sense inventar core state · E0-02 dataset upload multipart segur · E0-03 runner reconciliation amb nonce · E0-04 SSE amb cursor rowid estable.
- Gate: APP-01..15 **17/17** · E0-01..04 **31/31** · REAL-QWEN-APP **16/16** · CE A/B PASS · core unchanged.

## v1.1.0 (2026-08-14) — Multi-model certificat (a52580c)

- MiniCPM5 backend certificat (portability PROVEN): gate 16/16, overlap real 2 workers, oracle 0.0.
- Registry lazy de backends a `model_worker.py`; Qwen3 + MiniCPM5 coexisteixen.

## v1.0.0 (2026-07-26) — Core distribuït certificat (b146723)

### RC3.3.3 F1 — Worker real i tancament
- Implementacio de Qwen3TrainingBackend amb LoRA real
- Suport per workers reals amb PyTorch + PEFT
- 40 tests d'integracio (inclosos T13-T25 amb model real)
- Fix T25.2: OOM en verify_base_checkpoint resolt (tolist -> tobytes)
- Release tancada i congelada amb manifest SHA-256

### RC3.4 — Primera ronda federada real
- 3 rondes consecutives amb 2 workers reals
- FedAvg ponderat per samples
- Checkpoint global amb manifest
- Determinisme reproduible entre rondes

### RC4.0 — Planificacio
- Pla mestre amb 5 fases i criteris
- Baseline de qualitat (10 prompts catala, 7 metriques)
- Pla d'escalabilitat, resiliencia, automatitzacio i produccio
- Especificacio formal del Gate

### RC4.1 — Escalabilitat
- Coordinator testejat amb 2, 4 i 8 workers
- Totes les configuracions completen sense errors
- Bug fix: ERR_INVALID_PARAMS no importat
- Configuracio recomanada: 2 workers per hardware actual

### RC4.2 — Qualitat del model
- Comparativa base vs checkpoint-2500
- Loss: 4.9279 -> 4.7448 (-3.7%)
- Perplexity: 420.58 -> 192.26 (-54.3%)
- Bug fix: compatibilitat PEFT resolta (doble wrapping)

### RC4.3 — Resiliencia
- 13 escenaris de fallida testejats
- Worker failure, timeout, corrupcio, coordinator restart, idempotencia
- Totes les fallides gestionades sense perdua de dades

### RC4.4 — Automatitzacio
- Runner unic: python3 rc4_run.py config.yaml
- 12 etapes automatitzades
- Run ID per trazabilitat
- SHA-256 verificat automaticament

### RC4.5 — Produccio
- Guia d'instal·lacio, configuracio, operacio, desplegament
- Checklist de produccio

## v1.1 (planificat)
- Resume automatic d'execucions interrompudes
- Suport per GPU
- Dashboard de monitoritzacio
- Mes workers paral·lels
