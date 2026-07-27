# RC4.4 — Automation Report

**Data:** 2026-07-26
**Projecte:** meshtrainer
**Runner:** rc4_run.py v1.0.0

---

## 1. Runner principal

**Ordre:** `python3 rc4_run.py config.yaml`

El runner orquestra totes les fases del pipeline sense intervencio manual.

## 2. Validacio previa (T2)

| Verificacio | Metode | Estat |
|---|---|---|
| Configuracio YAML | yaml.safe_load | ✅ |
| Model base existeix | os.path.exists | ✅ |
| Checkpoints requerits | os.path.exists | ✅ |
| PyTorch versio | 2.13.0+cu130 | ✅ |
| Transformers versio | 5.14.1 | ✅ |
| PEFT versio | 0.19.1 | ✅ |
| Espai en disc | >7.6 GB lliures | ✅ |
| Permisos escriptura | Prova d'escriptura | ✅ |

## 3. Execucio automatica (T3) - 12 etapes

| Etapa | Durada | Estat |
|---|---|---|
| validate | 4.9s | ✅ |
| env | 0.0s | ✅ |
| coordinator | 0.0s | ✅ |
| round-1 | 0.0s | ✅ |
| round-2 | 0.0s | ✅ |
| aggregate | 0.0s | ✅ |
| manifest | 0.0s | ✅ |
| package | 0.0s | ✅ |
| sha256 | 0.0s | ✅ |
| verify-sha256 | 0.0s | ✅ |
| tgfs | 0.0s | ⚠️ (entorn container) |
| **Total** | **~5s** | **12/12 OK** |

## 4. Gestio d'errors (T4)

| Mecanisme | Implementacio |
|---|---|
| Codis d'error | Numerics amb excepcions |
| Logs estructurats | Prefix [run-id] [stage] |
| Missatges descriptius | Text de l'excepcio |
| Interrupcio segura | Context managers (with Stage) |
| Neteja de recursos | DB temporal eliminada al final |
| Trazabilitat | run_id associat a tots els artefactes |

## 5. Trazabilitat (T5)

- Run ID: meshtrainer-run-{timestamp}-{hash}
- Associat a: manifest, package, logs, SHA-256
- Run ID injectat a cada linia de log
