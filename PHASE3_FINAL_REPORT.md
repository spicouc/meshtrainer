# PRODUCT MVP PHASE 3 — FINAL REPORT (HARDENING + UX POLISH + INSTALL/LAUNCH)

**Commit**: 5249f2a (f83a3b8 Phase 3 base) — pendent commit final d'evidència
**Data**: 2026-08-18
**Executor**: DinDjarin (pve/Mandalor)
**Branca**: product/phase3-hardening
**Base**: main 1435209 · Phase 2 430cbdc · Core b146723 CLOSED · a52580c CERTIFIED

---

## Objectiu complert

De "Web MVP certificat" a **"producte local instal·lable i utilitzable per
una persona normal"**: git clone → ./install.sh → ./meshtrainer start →
http://localhost:8000 → First Run Setup → train.

## Arquitectura (inalterada)

```
Browser → FastAPI REST + SSE → MeshTrainerService → JobRunner → Certified Core
```
La UI no importa core, no llegeix SQLite directa, no executa workers.
Core b146723 / backends Qwen3+MiniCPM5: **FROZEN, 0 canvis**.

## Lliurables Phase 3

| Element | Punt |
|---|---|
| `install.sh` — idempotent, auditable, sense root, sense models auto | 3 |
| `./meshtrainer` launcher — start/stop/restart/status/logs | 4 |
| First Run Setup (5 passos) + endpoint `/api/setup/status` | 5 |
| Hardware detection (`hardware.py`): CPU/RAM/swap/disk/GPU | 6 |
| Guidance COMFORTABLE/LIMITED/NOT RECOMMENDED | 7 |
| Auto-config recomanada per backend (workers/concurrency/seq/lora) | 8 |
| Model paths configurables (env > MODELS_DIR > repo) — **0 hardcodes** | 9, 26 |
| Drag & drop datasets + errors agrupats | 11 |
| Safe Mode wizard (auto-config) + Custom + resource warning | 13, 16 |
| Human-readable states (Draft/Ready/Training/Cancelled/…) | 15 |
| Result screen "Training completed" + Download adapter/summary | 21 |
| `training_summary.json` generat per job (artifact verificat) | 22 |
| Config `~/.config/meshtrainer/config.toml` | 27 |
| Local-by-default 127.0.0.1 + warning si s'exposa xarxa | 28 |
| Versió **v1.4.0** | 29 |
| QUICKSTART.md (5-min) + TROUBLESHOOTING.md | 34 |

## Tests

| Suite | Resultat |
|---|---|
| P3-01..30 (Phase 3 gate) | **30/30 PASS** (33 checks) |
| APP-01..15 (regressió) | **17/17 PASS** |
| E0-01..04 (regressió) | **31/31 PASS** |
| UI-01..25 (Playwright, regressió) | pendent al gate final |
| REAL-QWEN-UI (qwen3 real via Web UI) | pendent — inclou training_summary |

## Regressions / core

- Core vs a52580c: **0 canvis** · backends: **0 canvis**
- app/: canvis **additius** (config/hardware/summary/setup/UI) + fix de
  hardcodes de paths (Phase 3 punt 26)
- cap formatter executat

## Seguretat

- Secret scan: **0** · weights: 0 · runtime DB: 0 · host logs: 0
- Path traversal: PASS (download valida pertinença) · HTML injection: PASS
  (escaped) · upload safety: PASS · local bind: PASS

## Incidències resoltes

1. Launcher `ok()` no definit → rc=127; fix afegit.
2. `_api()` del test P3 retornava l'envelope en comptes de `data`; fix.
3. P3-01 fresh-install: el CT112 no té .git/arbre → fallback copytree.
4. Hardcodes `/root/qwen3_0_6b_snapshot` a registry/job_runner → resolució
   dinàmica (env > MODELS_DIR > repo > pare), mateix resultat al lab sense
   hardcodes (punt 26).

## Estat

PRODUCT MVP PHASE 3 — pendent de gate complet + CE A/B + packaging.
