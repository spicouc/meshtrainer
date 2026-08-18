# RC5 — Gate Specification

**Versio:** 1.1.0-draft (corregit)

---

## 1. Fases RC5

| Fase | Nom | Objectiu |
|---|---|---|
| RC5.0 | Protocol Freeze | Documentacio, no implementacio |
| RC5.1 | Tiny Split PoC | Primer Split Server + 1 worker E2E amb model tiny |
| RC5.2 | Browser Worker | Worker real al navegador |
| RC5.3 | Integracio | Split + Coordinator + Workers complets |
| RC5.4 | Gate Final | Validacio completa, T-SPLIT-1 a 12 PASS |

## 2. Criteris RC5.0 (aquesta fase)

- [ ] 8 documents d'arquitectura creats i corregits
- [ ] Responsabilitats Coordinator vs Split Server clarament separades
- [ ] Coordinator: registre, calibratge, assignacio, ledger, FedAvg, checkpoint
- [ ] Split Server: embeddings, forward, backward, cut, receipts, queue, backpressure
- [ ] Split Server NO agrega deltes
- [ ] Receipts autenticats pel Split Server, no pel worker
- [ ] Ledger amb contribucio unica ACTIVE per assignment
- [ ] FedAvg ponderat per effective_trainable_tokens certificats
- [ ] Terminologia consistent entre documents
- [ ] Sense contradiccions
- [ ] Report de protocol freeze generat

## 3. Criteris RC5.1

- [ ] Split Server escolta en un port
- [ ] Accepta connexions de workers
- [ ] Implementa `step.open`, `.embedding`, `.cut_activation`, `.cut_gradient`, `.commit`
- [ ] Genera receipts autenticats
- [ ] Backpressure basic
- [ ] Coordinator implementa `worker.join`, `work.request/assignment/accept`
- [ ] Coordinator implementa ledger de contribucions
- [ ] Coordinator fa FedAvg al tancament de ronda
- [ ] T-SPLIT-1 PASS
- [ ] T-SPLIT-2 PASS
- [ ] Regressions v1.0 intactes

## 4. Per RC5.1 Tiny Split PoC

- FP32
- Model tiny fix
- Cut fix
- LoRA fix
- Sense quantitzacio adaptativa
- JSON-RPC + base64 per tensors
