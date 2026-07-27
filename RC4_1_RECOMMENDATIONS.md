# RC4.1 — Recomanacions de configuracio

**Data:** 2026-07-26
**Projecte:** meshtrainer

---

## 1. Configuracio optima

Basada en els resultats de les proves amb 2, 4 i 8 workers:

| Configuracio | Adequat per | Limitacions |
|---|---|---|
| **2 workers (recomanat)** | Entrenament estandard en CPU (8GB RAM) | Progressio mes lenta |
| **4 workers** | Entrenament amb mes varietat de dades | Requereix ~12GB RAM o execucio sequencial |
| **8 workers** | Proves d'estres del coordinator | No recomanat en maquinari actual |

## 2. Per maquinari disponible (4 CPU, 8GB RAM)

Us recomanada:
- **2 workers** per entrenament regular
- Execucio sequencial dels workers (no paral·lela) per evitar OOM
- Coordinator en el mateix host que els workers

## 3. Per escalar

Per utilitzar 4 o 8 workers amb models reals:
- Augmentar RAM a 16GB+
- O distribuir workers en diferents maquines
- O utilitzar workers simulats per proves del coordinator

## 4. Limitacions del maquinari actual

| Recurs | Us amb 2 workers reals | Disponible |
|---|---|---|
| RAM | ~3.5 GB | 8 GB |
| CPU | ~200% (2 nuclis) | 400% (4 nuclis) |
| Disc | ~10 MB/ronda | 25 GB lliures |

## 5. Conclusio

**Configuracio recomanada: 2 workers, sequencial.** El coordinator escala correctament fins a 8 workers. La limitacio es hardware, no del software.
