# RC5.0 — Protocol Freeze Report (corregit)

**Data:** 2026-07-27
**Branca:** rc5.0-protocol-freeze

---

## 1. Resum de decisions

| Decisio | Opcio | Justificacio |
|---|---|---|
| Coordinator agrega | ✅ Coordinator fa FedAvg | Separacio neta de responsabilitats |
| Split Server no agrega | ✅ Limit a embeddings/backward/receipts | Evita duplicitat, Coordinator es la font de veritat |
| Receipts autenticats pel Split Server | ✅ Worker mai genera receipts | El worker es no-confiable |
| Una ACTIVE per assignment | ✅ Ledger: SUPERSEDED si es reemplaca | Evita dobles contribucions |
| FedAvg ponderat per ett | ✅ `effective_trainable_tokens` certificats | Tokens declarats pel worker no son fiables |
| Micro-unitat basada en steps | ✅ Conjunt fix de passos | Flexibilitat sense lligar mida a tokens |
| Perfil congelat per ronda | ✅ precision_profile, loss_hash, optimizer | Reproduibilitat |
| Transport: JSON-RPC + binari | ✅ Control per JSON, tensors per binari | Separacio de concerns |
| FP32 per RC5.1 | ✅ Sense quantitzacio adaptativa | Simplicitat al PoC |

## 2. Desviacions respecte v1.0

| Aspecte | v1.0 | v1.1 | Impacte |
|---|---|---|---|
| Worker | Python (sequencial) | Navegador (paral·lel) | Nou runtime |
| Intermediari | Coordinator directe | Split Server | Nova capa |
| Receipts | No | Si, autenticats | Auditoria |
| Ledger | No | Si, contribucions ACTIVE | Trazabilitat |
| Agregacio | Per batch | Per micro-unitat, ponderat per ett | Mes precis |
| Perfil | No congelat | Congelat per ronda | Reproduibilitat |

## 3. Riscos coneguts

| Risc | Probabilitat | Impacte | Mitigacio |
|---|---|---|---|
| WebGPU no disponible | Mitjana | Alt | Fallback CPU pla |
| Latencia Split Server → Worker | Mitjana | Mig | Batching d'embeddings |
| Receipt replay | Baixa | Alt | Nonce + key_id + expiracio |
| Ledger inconsistent | Baixa | Alt | Coordinator unic, base de dades |

## 4. Punts oberts per RC5.1

- [ ] Mida optima de la micro-unitat (en steps, no tokens)
- [ ] Estrategia de reassignacio (immediata vs backpressure)
- [ ] Rotacio de claus de receipts (cada quantes rondes?)
- [ ] Serialitzacio exacta dels tensors (base64 vs binari)
