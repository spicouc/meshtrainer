# MeshTrainer

**Distributed multi-model fine-tuning** — Plataforma d'entrenament distribuït (LoRA + FedAvg) per a models de llenguatge, amb Web UI, API REST + SSE, CLI i core distribuït certificat.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![GitHub top language](https://img.shields.io/github/languages/top/spicouc/meshtrainer)

---

## Què és?

MeshTrainer entrena models de llenguatge de forma distribuïda amb **múltiples workers independents**, agrega les contribucions amb **FedAvg** i genera **adapter LoRA** amb verificació criptogràfica (SHA-256 dels artefactes).

És multi-model: **Qwen3** i **MiniCPM5** estan certificats.

## Arquitectura

```
Browser (Web UI — vanilla JS)
  ↓  REST + SSE
FastAPI (app.api.main:app)
  ↓
MeshTrainerService (application layer)
  ↓
JobRunner (procés separat, JSON-RPC HTTP)
  ↓
Distributed Core (certificat)
  ↓
Workers (model_worker subprocessos)
  ↓
FedAvg
  ↓
LoRA adapter (artefacte verificat)
```

La Web UI **només parla amb l'Application API + SSE** — mai importa el core Python directament.

## Backends certificats

| Backend | Model | Estat |
|---|---|---|
| Qwen3 | `Qwen/Qwen3-0.6B` | **CERTIFICAT** |
| MiniCPM5 | `openbmb/MiniCPM5-1B` | **CERTIFICAT** |

## Quick Start (instal·lació i arrencada)

> 🚀 Nou? Comença per **[QUICKSTART.md](QUICKSTART.md)** (5 minuts) i consulta
> **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** si alguna cosa falla.

```bash
# 1. Instal·la (crea .venv, instal·la requirements, valida imports)
./install.sh

# 2. Arrenca el servei
./meshtrainer start        # → http://localhost:8000

# 3. Altres ordres
./meshtrainer status       # estat del servei
./meshtrainer logs         # últims logs
./meshtrainer stop         # atura el servei
```

Obre el navegador: **http://localhost:8000**

La primera vegada veuràs el **First Run Setup** (System Check → Storage →
Model availability → Recommended profile → Finish), basat en la detecció de
hardware real del teu equip.

Des del Dashboard pots:

- veure l'estat del sistema (CPU/RAM/swap/disc) i els backends disponibles;
- afegir o pujar un dataset JSONL i validar-lo;
- crear un training job amb el wizard (backend → dataset → config LoRA → workers → review);
- validar i iniciar l'entrenament;
- seguir-lo en directe (SSE: timeline, worker cards, mètriques, gràfica de loss);
- cancel·lar-lo;
- veure logs filtrats;
- descarregar els artefactes finals (adapters, SHA-256).

## CLI

```bash
# veure backends disponibles
python -m app.cli.main backend list

# dataset
python -m app.cli.main dataset add /path/to/train.jsonl
python -m app.cli.main dataset validate <dataset_id>

# job
python -m app.cli.main job create --backend qwen3 --dataset <dataset_id> ...
python -m app.cli.main job validate <job_id>
python -m app.cli.main job start <job_id>
python -m app.cli.main job status <job_id>
python -m app.cli.main job logs <job_id>
python -m app.cli.main job cancel <job_id>
python -m app.cli.main job artifacts <job_id>
```

## API (resum)

| Endpoint | Descripció |
|---|---|
| `GET /api/health` | estat del servei |
| `GET /api/backends` | backends disponibles (Qwen3/MiniCPM5) |
| `GET /api/system` | CPU/RAM/swap/disc |
| `POST /api/datasets` · `POST /api/datasets/upload` | registrar / pujar dataset (multipart, SHA-256) |
| `POST /api/datasets/{id}/validate` | validar dataset |
| `POST /api/jobs` · `GET /api/jobs` | crear / llistar jobs |
| `POST /api/jobs/{id}/validate` · `start` · `cancel` | cicle de vida del job |
| `GET /api/jobs/{id}/events` | SSE (id=seq rowid estable, event, data) |
| `GET /api/jobs/{id}/logs` · `/rounds` · `/metrics` · `/artifacts` | observabilitat |
| `GET /api/artifacts/{job_id}/{artifact_id}/download` | descàrrega segura d'artefactes |

## Tests

| Suite | Contingut | Estat |
|---|---|---|
| `app/tests/p3_gate_tests.py` | P3-01..30 (incl. fresh product install + launch reals) | 39/39 PASS |
| `app/tests/app_acceptance_tests.py` | APP-01..15 (application layer, dummy) | 17/17 PASS |
| `app/tests/e0_gate_tests.py` | E0-01..04 (cancel sense core state, upload, reconciliation, SSE contract) | 31/31 PASS |
| `app/tests/ui_e2e_tests.py` | UI-01..25 (Playwright, navegador real) | 27/27 PASS |
| `app/tests/real_qwen_ui_test.py` | REAL-QWEN-UI (qwen3 real des del navegador) | PASS |

```bash
bash RUN_P3_GATE_PORTABLE.sh   # gate P3 + APP + E0 (requereix venv amb deps)
bash RUN_UI_E2E.sh             # suite Playwright (requereix Playwright + chromium)
```

## Estat del projecte

Vegeu **[PROJECT_STATUS.md](PROJECT_STATUS.md)** per l'estat complet per fases.

- Generic Distributed Core — **CLOSED**
- Qwen3 Backend — **CERTIFIED**
- MiniCPM5 Backend — **CERTIFIED**
- Product MVP Phase 0/1/2/3 — **CLOSED**
- Application Layer — **CERTIFIED MVP**
- Web UI — **CERTIFIED MVP**
- Installer + Launcher — **CERTIFIED** (`install.sh` / `./meshtrainer`)

## Requisits

- Linux, 4 CPU, 8 GB RAM (12 GB recomanats), 10 GB disc
- Python 3.11+
- PyTorch, Transformers, PEFT (per als backends reals)
- FastAPI, Pydantic, Uvicorn, python-multipart (per a l'API/UI — vegeu `requirements-app.txt`)

## NOTA: entrypoints històrics

Els scripts `rc5_*.py` i els drivers de certificació (`generic_distributed_run.py`, `generic_recovery_tests.py`, etc.) són **codi històric/legacy** de les fases de certificació del core.

**CURRENT PRODUCT ENTRYPOINT: `app.api.main:app`** (Web UI + API) — i `app.cli.main` per a la CLI.

No s'ha d'usar cap driver de test com a API de producte.
