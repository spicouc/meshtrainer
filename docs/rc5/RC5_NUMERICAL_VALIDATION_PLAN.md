# RC5 — Numerical Validation Plan

**Document:** RC5_NUMERICAL_VALIDATION_PLAN.md
**Versio:** 1.1.0-draft

---

## 1. Objectiu

Verificar que el Split Server no introdueix errors numerics respecte
al pipeline v1.0.

## 2. Metodologia

Per a cada test:
1. Executar el mateix entrenament en v1.0 (Coordinator directe)
2. Executar el mateix entrenament via v1.1 (Split Server)
3. Comparar els deltes obtinguts

## 3. Tests

| Test | Descripcio | Criteri |
|---|---|---|
| T-SPLIT-1 | Mateixa seed, mateix dataset, 1 worker | assert_allclose(delta_v1, delta_v11) |
| T-SPLIT-2 | Mateixa seed, 2 workers, sequencial | Deltes identics |
| T-SPLIT-3 | Mateix dataset, workers diferents | FedAvg correcte |
| T-SPLIT-4 | Micro-unitats vs batches complets | Mateix resultat agregat |
| T-SPLIT-5 | Receipts vs calcul directe | Tokens efectius coincideixen |

## 4. Tolerancies

| Metrica | Tolerancia |
|---|---|
| Max error absolut (delta) | 1e-5 |
| Max error relatiu (delta) | 1e-5 |
| Diferencia de loss | 1e-4 |
| Diferencia de grad_norm | 1e-3 |
| Tokens efectius | 0 (han de coincidir exactament) |

## 5. Reproduibilitat

Tots els tests han de tenir seed fixa i dataset determinista.
Els resultats han de ser reproduibles en qualsevol maquina.
