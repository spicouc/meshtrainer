# RC5 — Numerical Validation Plan

**Versio:** 1.1.0-draft (corregit)

---

## 1. Objectiu

Verificar que la pipeline v1.1 (amb Split Server) produeix resultats
numerics equivalents a v1.0.

## 2. Tests obligatoris

| ID | Test | Criteri |
|---|---|---|
| T-SPLIT-1 | Mateixa seed, mateix dataset, 1 worker, split vs directe | assert_allclose(delta_v1, delta_v11) |
| T-SPLIT-2 | Contribucio cumulativa substitueix l'anterior | Ledger: 1 ACTIVE, 1 SUPERSEDED. FedAvg nomes sobre ACTIVE |
| T-SPLIT-3 | Receipt reutilitzat en una altra ronda | REJECTED per nonce + round mismatch |
| T-SPLIT-4 | Receipt reutilitzat per un altre worker | REJECTED per worker_id mismatch |
| T-SPLIT-5 | Nonce repetit | REJECTED per replay detection |
| T-SPLIT-6 | key_id retirat | REJECTED per clau no vigent |
| T-SPLIT-7 | Recompte de tokens falsificat | REJECTED per mismatch amb Split Server |
| T-SPLIT-8 | Precisio incorrecta (FP16 vs FP32 declarat) | REJECTED per precision_profile mismatch |
| T-SPLIT-9 | Reinici del Coordinator abans de round close | Ledger recuperable, FedAvg identic despres de reconstruir |
| T-SPLIT-10 | FedAvg identic despres de reconstruir ledger | assert_allclose(abans, despres) |
| T-SPLIT-11 | Micro-unitat incompleta no agregada | No apareix al ledger |
| T-SPLIT-12 | Represa sense reenviar contribucio ja acceptada | Coordinator retorna checkpoint_id existent |

## 3. Tolerancies numeriques

| Metrica | Tolerancia |
|---|---|
| Max error absolut (delta) | 1e-5 |
| Max error relatiu (delta) | 1e-5 |
| FedAvg v1.0 vs v1.1 | assert_allclose(rtol=1e-5, atol=1e-5) |

## 4. Perfil congelat per test

Tots els tests usen el perfil congelat de RC5.1:

- FP32
- Model tiny fix
- Cut fix
- LoRA fix (r=8, alpha=16)
- Sense quantitzacio adaptativa
- Optimizer: AdamW (lr=2e-4, betas=[0.9,0.999], weight_decay=0.01)
- Loss: CrossEntropyLoss
- Seed fixa
