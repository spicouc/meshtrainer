# CHANGELOG — meshtrainer

## Unreleased — certified development state (2026-08-17)

### Generic distributed core
- Generic distributed training core closed and frozen (`b146723` lineage)
- Coordinator authority, leases, revisions, contribution validation and weighted FedAvg closed
- Safe crash recovery / exactly-once behavior closed
- Core remains model-agnostic and isolated from product UI concerns

### Multi-model portability
- Qwen3 backend certified
- MiniCPM5 backend certified
- Real two-worker overlap certified
- Multi-model portability proven (`a52580c` authoritative closeout lineage)
- Backend registry keeps model-specific behavior outside the distributed core

### Product MVP Phase 0 — CLOSED
- Product architecture approved
- API + Web UI + CLI defined over one shared application layer
- FastAPI + Pydantic + independent SQLite application state
- SSE selected for live server-to-browser events
- Dataset, job, worker, artifact, error and security contracts defined

### Product MVP Phase 1 — CLOSED
- `MeshTrainerService` application facade implemented
- Separate `JobRunner` process per training job
- JSONL dataset registration, controlled upload and validation
- FastAPI management API skeleton
- Scriptable CLI skeleton
- Persistent jobs, logs, metrics and artifacts
- Cooperative cancellation with no invented core round states
- Runner restart reconciliation using PID + job ID + nonce identity
- Stable SQLite-rowid SSE event cursor
- Dummy backend hidden in production
- Real Qwen3 E2E through the application layer passed
- APP acceptance: 17/17 PASS
- E0 closeout: 31/31 PASS
- REAL-QWEN-APP: 16/16 PASS
- Clean extraction A/B: 11/11 PASS each
- Functional Product Phase 1 lineage: `60ebd29`
- Final evidence-only closeout lineage: `4696d35`

### Product MVP Phase 2 — AUTHORIZED / NEXT
- Browser dashboard
- Training-job wizard
- Dataset management UI
- Worker/round monitoring through SSE
- Metrics and logs
- Safe cancellation UI
- Artifact download UI
- Playwright browser E2E suite
- Final real-Qwen smoke initiated from the Web UI

## v1.0.0 (2026-07-26)

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

## Earlier v1.1 plan (superseded by current roadmap)
- Resume automatic d'execucions interrompudes
- Suport per GPU
- Dashboard de monitoritzacio
- Mes workers paral·lels

The current roadmap is tracked in `PROJECT_STATUS.md`; several of the originally planned v1.1 items have since been replaced by the certified distributed-core and Product MVP milestones.
