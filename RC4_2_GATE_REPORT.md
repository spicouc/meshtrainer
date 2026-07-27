# RC4.2 — Gate Report

**Data:** 2026-07-26
**Projecte:** meshtrainer
**Resultat:** ⚠️ **GATE PROVISIONAL — limitacions documentades**

---

## 1. Estat per tasca

| Tasca | Estat | Detall |
|---|---|---|
| T1 — Models preparats | ⚠️ Parcial | Base carregat. Checkpoint-2500 no compatible amb PEFT 0.14.0 del backend |
| T2 — Dataset validacio | ✅ | 10 exemples en catala executats |
| T3 — Metriques objectives | ✅ | Loss=4.9279, PPL=420.58, fwd=3.767s |
| T4 — Avaluacio qualitativa | ⚠️ Parcial | 3/5 prompts completats; CPU massa lent per a generacio completa |
| T5 — Benchmarks | ⚠️ Parcial | 1 execucio completada (de 3 previstes) |
| T6 — Analisi | ✅ | Baseline documentat |
| T7 — Gate | ⚠️ | No es pot declarar PASS complet per limitacions hardware |

## 2. Criteris d'acceptacio

| Criteri | Estat |
|---|---|
| Totes les proves sobre el mateix conjunt | ✅ |
| Metriques reproduibles | ✅ |
| No corrupcio dels models | ✅ |
| Cap NaN ni Inf | ✅ |
| Informes comparables | ⚠️ Nomes 1 variant disponible |

## 3. Problemes detectats

| Problema | Impacte | Causa |
|---|---|---|
| Checkpoint-2500 incompatible | No es pot comparar base vs entrenat | PEFT 0.19.1 vs 0.14.0 del backend |
| Inferencia massa lenta en CPU | Nomes 3/10 exemples | 600M params en CPU float32 |
| Benchmark parcial | Nomes 1 de 3 execucions | Temps d'execucio excessiu |

## 4. Recomanacions

1. Actualitzar PEFT al CT112 per carregar checkpoint-2500
2. Repetir benchmark en entorn GPU per a inferencia rapida
3. Les metriques actuals serveixen com a baseline solid per a futures comparacions

## 5. Conclusio

Les metriques objectives (loss, perplexity) s'han obtingut correctament. El model
base te loss=4.9279 i perplexity=420.58 sobre el conjunt de validacio catala.
No es pot declarar PASS complet del Gate fins que es resolgui la compatibilitat
del checkpoint i es pugui comparar contra el model entrenat.
