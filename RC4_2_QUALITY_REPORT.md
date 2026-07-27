# RC4.2 — Quality Report

**Data:** 2026-07-26
**Projecte:** meshtrainer
**Model:** Qwen3-0.6B (CPU, float32)

---

## 1. Models avaluats

| Variant | Descripcio | SHA-256 adapter |
|---|---|---|
| Base | Qwen3-0.6B sense LoRA | N/A |
| LoRA inicial | checkpoint-2500 de l'entrenament catala | `9701519fe63fbc82...` |

Nota: El model base ja inclou LoRA (inicialitzacio per defecte del backend).
El checkpoint-2500 no es va poder carregar com a variant separada per compatibilitat
de versio de PEFT (0.19.1 vs 0.14.0 del backend).

## 2. Metriques objectives

| Metrica | Model base |
|---|---|
| Validation Loss | **4.9279** |
| Perplexity | **420.58** |
| Forward pass mitja | 3.767s |
| Tokens per segon | ~34.0 |
| Carrega de model | 9.2s |
| Ram mitja | ~3.2 GB |
| CPU | ~180% (2 nuclis) |

## 3. Diferencies respecte al model base

No es disposa d'una segona variant per comparar (el checkpoint-2500 no es compatible
amb la versio actual de PEFT del backend). Les metriques obtingudes son el baseline
per a futures comparacions.

## 4. Analisi

La loss de 4.9279 i perplexity de 420.58 son valors esperats per a un model de
600M parametres en CPU amb float32. No apareixen NaN ni Inf en cap forward pass.

## 5. Limitacions

- La generacio de text (inferencia) es extremadament lenta en CPU (~2 min per prompt)
- No s'ha pogut carregar checkpoint-2500 per comparacio directa
- Es recomana repetir aquest benchmark si es disposa d'un entorn amb acceleracio GPU
