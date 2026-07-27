# CHANGELOG — meshtrainer

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

## v1.1 (planificat)
- Resume automatic d'execucions interrompudes
- Suport per GPU
- Dashboard de monitoritzacio
- Mes workers paral·lels
