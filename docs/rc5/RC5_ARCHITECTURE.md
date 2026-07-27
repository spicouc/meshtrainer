# RC5 — Architecture v1.1

**Versio:** 1.1.0-draft (corregit)
**Base:** v1.0 (meshtrainer classic)
**Estat:** Protocol Freeze

---

## 1. Topologia

```
                                      ┌──────────────────┐
                                      │   Coordinator    │
                                      │  (identitat,     │
                                      │   calibratge,    │
                                      │   assignacio,    │
                                      │   ledger,        │
                                      │   FedAvg,        │
                                      │   checkpoint)    │
                                      └────────┬─────────┘
                                               │
                          JSON-RPC (control)    │
                          + binari (tensors)    │
                                               │
                    ┌───────────────────────────┼──────────────────────────┐
                    │                           │                          │
         ┌─────────▼──────────┐     ┌──────────▼─────────┐    ┌──────────▼─────────┐
         │   Split Server     │     │  Browser Worker 1   │    │  Browser Worker N   │
         │  (embeddings,      │     │  (control → Coord,  │    │  (control → Coord,  │
         │   forward, loss,   │     │   activacions → SS, │    │   activacions → SS, │
         │   backward, cut,   │     │   gradients → SS)   │    │   gradients → SS)   │
         │   receipts, queue, │     └─────────────────────┘    └─────────────────────┘
         │   backpressure)    │
         └────────────────────┘
```

## 2. Responsabilitats

### Coordinator (unic responsable de:)

| Funcio | Descripcio |
|---|---|
| Registre i identitat dels workers | `worker.join`, `worker.leave` |
| Calibratge | `worker.calibration.start`, `.result` |
| Assignacio de treball | `work.request`, `work.assignment`, `work.accept` |
| Ledger de contribucions | RECEIVED → VALIDATED → ACTIVE → AGGREGATED |
| Validacio de checkpoints | `checkpoint.upload`, `.accept`, `.reject` |
| Seleccio de contribucions ACTIVE | Una per (run, round, assignment, worker) |
| FedAvg al tancament de ronda | Δ ponderat per effective_trainable_tokens |
| Checkpoint global | Generacio i verificacio |

### Split Server (limitat a:)

| Funcio | Descripcio |
|---|---|
| Embeddings | Tram servidor del model |
| Forward + loss | Computacio de forward i loss |
| Backward + cut gradient | Propagacio i emissio del gradient del cut |
| Recompte de tokens entrenables | Certificacio de effective_trainable_tokens |
| Generacio de receipts autenticats | HMAC, vinculats a run/round/unit/worker |
| Scheduler de micro-unitats | Cua i backpressure |
| Receipts contextuals | Autenticats, amb nonce i key_id |

**El Split Server NO agrega deltes ni tanca FedAvg.**

### Browser Worker (es comunica amb:)

- **Coordinator:** control, assignacions, checkpoints, contribucions
- **Split Server:** embeddings, activacions, gradients, receipts

## 3. Topologia congelada per run

Cada run te una topologia fixa. No es poden afegir ni treure components
durant l'execucio. El perfil numeric es congela al inici de la ronda:

| Parametre | Valor RC5.1 |
|---|---|
| precision_profile | fp32 |
| compute_dtype | fp32 |
| quantization_scheme | cap (no adaptativa) |
| backend | navegador fixat per la prova |
| loss_definition_hash | congelat |
| optimizer (lr, betas, weight_decay) | congelat |
| scheduler | congelat |

**Regla:** Un worker incompatible amb el perfil es rebutja (UNSUPPORTED).
No hi ha canvi automatic de backend dins d'una ronda.
Fallback CPU es una possibilitat futura, fora de l'abast del gate RC5.

## 4. Flux alt nivell

```
1. Worker join → Coordinator (worker.join)
2. Coordinator inicia calibratge (worker.calibration.start/result)
3. Worker sol·licita treball (work.request)
4. Coordinator assigna (work.assignment)
5. Worker accepta (work.accept)
6. Per cada step:
   a. Worker obre step (step.open)
   b. Worker envia text → Split Server (step.embedding)
   c. Split Server retorna embeddings
   d. Worker fa forward parcial
   e. Split Server fa cut_activation (step.cut_activation)
   f. Worker fa backward parcial
   g. SplitServer fa cut_gradient (step.cut_gradient)
   h. Ambdós fan commit (step.commit)
7. Worker completa (work.complete)
8. Split Server emet receipt autenticat
9. Worker envia contribucio → Coordinator (checkpoint.upload)
10. Coordinator valida i afegeix al ledger (checkpoint.accept/reject)
11. Al tancament de ronda: FedAvg sobre contribucions ACTIVE
12. Coordinator genera checkpoint global
```

## 5. Transport

| Tipus | Protocol | Us |
|---|---|---|
| Control | JSON-RPC 2.0 | Tots els missatges de protocol |
| Tensors | Binari sobre HTTP | Embeddings, activacions, gradients |

Per RC5.1 Tiny Split PoC: nomes JSON-RPC + base64 per tensors petits.
