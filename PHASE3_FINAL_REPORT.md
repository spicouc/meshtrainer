# PRODUCT MVP PHASE 3 — FINAL REPORT (R1 CORRECTIVE, DOCS COHERENT)

**Commit**: 7960e61 (branca product/phase3-hardening)
**Data**: 2026-08-18
**Executor**: DinDjarin (pve/Mandalor)
**Base**: main 1435209 · Phase 2 430cbdc · Core b146723 CLOSED · a52580c CERTIFIED
**Artifact**: `meshtrainer_product_mvp_phase3_r1_final_7960e61.tar.gz`
**SHA-256**: `101e5da892890b9a41592066efa93730d6a439b8bdb540f0bc4cb1fcd56e8ca4`
**Package**: autònom (core complet byte-identical a52580c) · 78 fitxers

---

## Objectiu complert

De "Web MVP certificat" a **"producte local instal·lable i utilitzable per
una persona normal"**: git clone → ./install.sh → ./meshtrainer start →
http://localhost:8000 → First Run Setup → train. Des d'una extracció neta
del package (sense /root/meshtrainer, sense CT112, sense PYTHONPATH):

```
PACKAGE → INSTALL → START → BACKENDS DISCOVERED (qwen3/minicpm5/dummy)
→ JOB → WORKERS (2) → FEDAVG → ARTIFACT (adapter + training_summary)
```

## Arquitectura (inalterada)

```
Browser → FastAPI REST + SSE → MeshTrainerService → JobRunner → Certified Core
```
La UI no importa core, no llegeix SQLite directa, no executa workers.
Core b146723 / backends Qwen3+MiniCPM5: **FROZEN, byte-identical a a52580c**.

## Lliurables Phase 3

| Element | Punt |
|---|---|
| `install.sh` — idempotent, auditable, sense root, amb deps de TRAINING (torch/transformers/peft via requirements-training.txt) | 3, R1-03 |
| `./meshtrainer` launcher — start/stop/restart/status/logs | 4 |
| First Run Setup (5 passos) + endpoint `/api/setup/status` | 5 |
| Hardware detection (`hardware.py`): CPU/RAM/swap/disk/GPU | 6 |
| Guidance COMFORTABLE/LIMITED/NOT RECOMMENDED | 7 |
| Auto-config recomanada per backend | 8 |
| Model paths configurables (env > MODELS_DIR > relatiu al repo) — **0 hardcodes** | 9, 26, R1-07 |
| Drag & drop datasets + errors agrupats | 11 |
| Safe Mode wizard (auto-config) + Custom + resource warning | 13, 16 |
| Human-readable states (Draft/Ready/Training/Cancelled/…) | 15 |
| Result screen "Training completed" + Download adapter/summary | 21 |
| `training_summary.json` generat per job (artifact verificat) | 22 |
| Config `~/.config/meshtrainer/config.toml` | 27 |
| Local-by-default 127.0.0.1 + warning si s'exposa xarxa | 28 |
| Versió **v1.4.0** | 29 |
| QUICKSTART.md (5-min) + TROUBLESHOOTING.md | 34 |
| **Package autònom**: core complet al package (R1-01) | R1-01 |
| **Gate portable** `RUN_P3_GATE_PORTABLE.sh` (R1-09) | R1-09 |

## Tests (execucions reals)

| Suite | Resultat |
|---|---|
| P3-01..30 (Phase 3 gate, incl. P3-01R fresh product install REAL + P3-30 launch REAL) | **39/39 PASS** |
| APP-01..15 (regressió) | **17/17 PASS** |
| E0-01..04 (regressió) | **31/31 PASS** |
| UI-01..25 (Playwright, regressió) | **27/27 PASS** |
| REAL-QWEN-UI (qwen3 real via Web UI) | **PASS** — adapter_0 `5c05f972` determinista, training_summary present |
| CE A (extracció independent, 24 passos) | **24/24 PASS** |
| CE B (extracció independent, 24 passos) | **24/24 PASS** |

## Core / regressions

- Core vs a52580c: **0 canvis** · backends: **0 canvis** (byte-identical)
- app/: canvis **additius** a la base 7960e61 (config/hardware/summary/setup/UI)
- cap formatter executat · cap modificació de semàntica certificada

## Seguretat

- Secret scan: **0** · weights: 0 · runtime DB: 0 · host logs: 0
- Path traversal: PASS (download valida pertinença) · HTML injection: PASS
  (escaped) · upload safety: PASS · local bind: PASS

## Incidències resoltes (R1)

1. Package no autònom: faltaven mòduls core (`qwen3_training_backend`,
   `rc5_2_*`) → resolució BFS de dependències + inclusió completa (R1-01)
2. `install.sh` només instal·lava FastAPI → afegit requirements-training.txt
   amb torch/transformers/peft + validació d'imports (R1-03)
3. P3-01 convertia clone FAIL en copytree → ara CLONE real PASS/FAIL (R1-06)
4. Hardcodes `/root/` (incl. `os.listdir("/root")` a registry.py) → resolució
   dinàmica per env/MODELS_DIR (R1-07)
5. P3-30 testava strings del README → execució real (R1-05)
6. CE executava install al host sense deps → validació al runtime real (R1-11)

## Estat

**PRODUCT MVP PHASE 3: CLOSED** — package autònom certificat i entregat.
**FINAL MVP PACKAGE: CERTIFIED**.
**STOP**: esperant auditoria / decisió de release del supervisor.
No crear Phase 4. No merge. No release GitHub.
