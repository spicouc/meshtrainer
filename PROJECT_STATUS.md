# PROJECT STATUS — MeshTrainer

**Última actualització**: 2026-08-18 (Product MVP Phase 2 CLOSED)

---

## Estat per components

| Component | Estat |
|---|---|
| Generic Distributed Core | **CLOSED** (b146723) |
| Qwen3 Backend | **CERTIFIED** |
| MiniCPM5 Backend | **CERTIFIED** |
| Real Two-Worker Overlap | **CERTIFIED** |
| Multi-Model Portability | **PROVEN** (a52580c autoritatiu) |
| Product MVP Phase 0 (disseny) | **CLOSED** |
| Product MVP Phase 1 (application layer) | **CLOSED** (4696d35) |
| Product MVP Phase 2 (Web UI) | **CLOSED** (430cbdc) |
| Application Layer | **CERTIFIED MVP** |
| Web UI | **CERTIFIED MVP** |
| Real Qwen App E2E | **PASS** |
| Real Qwen via Web UI | **PASS** |

**NEXT: Product MVP Phase 3** — Hardening + UX polish + install/launch + final MVP package.

---

## Gates (execucions reals)

### Core (b146723 CLOSED)
- Core distribuït certificat: coordinador, servidor HTTP JSON-RPC, workers, FedAvg, leases, recovery, adapter codec.
- Gate: mutation tests, adversarial tests, restart recovery, two-worker real overlap.

### Multi-model (a52580c CERTIFIED)
- MiniCPM5 portability certificada: gate 16/16, PAR 4/4 amb overlap real de leases (140s), oracle maxdiff 0.0, recovery hash exacte.
- Qwen3 i MiniCPM5 coexistint com a backends del mateix core.

### Product MVP Phase 1 (4696d35 CLOSED)
- Application Layer: `MeshTrainerService` + `JobRunner` + persistència SQLite + dataset layer + API skeleton + CLI skeleton.
- Gate: APP-01..15 **17/17 PASS** · E0-01..04 **31/31 PASS** · REAL-QWEN-APP **16/16 PASS** · CE A/B PASS · core unchanged.

### Product MVP Phase 2 (430cbdc CLOSED)
- Web UI vanilla (zero toolchain frontend) servida per FastAPI; wizard de 5 passos; monitor SSE en viu; logs; mètriques; artefactes descarregables.
- Gate: APP **17/17** · E0 **31/31** · UI-01..25 **27/27 PASS** (Playwright) · REAL-QWEN-APP **16/16** · REAL-QWEN-UI **PASS** · CE A **13/13** · CE B **13/13** · core/drivers **0 canvis**.

---

## Detalls tècnics

- **Core**: `model_round_coordinator.py`, `training_http_server.py`, `model_worker.py`, `training_backend.py`, `adapter_codec.py`, `rc5_4_leases.py`.
- **Backends**: `backends/qwen3_backend.py`, `backends/minicpm5_backend.py`, `backends/dummy_backend.py` (test només).
- **Producte**: `app/api/main.py` (FastAPI + SSE), `app/services/mesh_trainer_service.py`, `app/services/job_runner.py`, `app/web/` (UI), `app/cli/main.py`.
- **SSE contracte (E0-04)**: `id:` = seq (rowid SQLite, estable i monotònic), `event:` + `data:` per esdeveniment; mai timestamp en segons com a cursor.
- **Cancel·lació (E0-01)**: cooperativa; el core mai rep estat de ronda CANCELLED; leases invalidades; run_id no reutilitzat.
- **Dataset upload (E0-02)**: multipart, nom intern generat pel servidor, límit configurable, SHA-256, anti path traversal.
- **Runner reconciliation (E0-03)**: PID + runner_identity (nonce) persistits; reattach només si cmdline exacta i identity coincideixen.

## Exclòs deliberadament del repo

- Model weights / snapshots locals (`/root/qwen3_0_6b_snapshot`, `/root/minicpm5_1b_snapshot`)
- Adapters generats, checkpoints, SQLite runtime, logs de producció
- Secrets / tokens / credentials

## Historial de commits autoritatius

| Commit | Significat |
|---|---|
| `b146723` | Core distribuït certificat (CLOSED) |
| `a52580c` | Multi-model autoritatiu (Qwen3 + MiniCPM5 certificats) |
| `4696d35` | Product MVP Phase 1 closeout (application layer) |
| `430cbdc` | Product MVP Phase 2 final (Web UI) |
