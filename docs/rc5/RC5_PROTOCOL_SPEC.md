# RC5 — Protocol Specification

**Versio:** 1.1.0-draft (corregit)

---

## 1. Transport

| Tipus | Protocol | Us |
|---|---|---|
| Control | JSON-RPC 2.0 sobre HTTP | Tots els missatges de protocol |
| Tensors | Binari sobre HTTP (Content-Type: application/octet-stream) | Embeddings, activacions, gradients |

Per RC5.1 Tiny Split PoC: JSON-RPC + base64 per tensors petits.

## 2. Metodes de protocol

### Cicle de vida del worker

| Metode | Origen → Desti | Descripcio |
|---|---|---|
| `worker.join` | Worker → Coordinator | Registra worker, retorna worker_id + token |
| `worker.leave` | Worker → Coordinator | Baixa voluntaria del worker |
| `worker.calibration.start` | Coordinator → Worker | Inicia calibratge |
| `worker.calibration.result` | Worker → Coordinator | Resultat del calibratge |

### Cicle de treball

| Metode | Origen → Desti | Descripcio |
|---|---|---|
| `work.request` | Worker → Coordinator | Sol·licita una assignment |
| `work.assignment` | Coordinator → Worker | Assigna micro-unitat |
| `work.accept` | Worker → Coordinator | Accepta l'assignacio (entra ACTIVE) |
| `work.pause` | Coordinator → Worker | Pausa temporal |
| `work.resume` | Coordinator → Worker | Represa |
| `work.complete` | Worker → Coordinator | Worker completa l'assignacio |
| `work.abort` | Coordinator → Worker | Cancel·la l'assignacio |

### Steps (dins d'una micro-unitat)

| Metode | Origen → Desti | Descripcio |
|---|---|---|
| `step.open` | Worker → Split Server | Obre un step dins la micro-unitat |
| `step.embedding` | Worker → Split Server | Envia text, rep embeddings |
| `step.cut_activation` | Split Server → Worker | Envia activacio del cut al worker |
| `step.cut_gradient` | Worker → Split Server | Envia gradient del cut |
| `step.commit` | Ambdos | Confirmacio bilateral del step |

### Checkpoint i contribucions

| Metode | Origen → Desti | Descripcio |
|---|---|---|
| `checkpoint.prepare` | Coordinator → Worker | Prepara checkpoint per pujar |
| `checkpoint.upload` | Worker → Coordinator | Puja contribucio (delta + receipt) |
| `checkpoint.accept` | Coordinator → Worker | Contribucio validada, entra al ledger |
| `checkpoint.reject` | Coordinator → Worker | Contribucio rebutjada, worker pot reintentar |

## 3. Receipt contextual

Generat i **autenticat pel Split Server** (no pel worker).

### Camps obligatoris

| Camp | Descripcio |
|---|---|
| protocol_version | Versio del protocol |
| run_id | Identificador del run |
| round_id | Identificador de la ronda |
| assignment_id | Identificador de l'assignacio |
| micro_unit_id | Identificador de la micro-unitat |
| worker_id | Worker que ha executat |
| session_id | Sesio del worker |
| base_adapter_hash | Hash de l'adapter base |
| model_hash | Hash del model |
| adapter_schema_hash | Hash del schema de l'adapter |
| loss_definition_hash | Hash de la definicio de loss |
| precision_profile | Perfil de precisio usat |
| data_shard_hash | Hash del fragment de dades |
| effective_trainable_tokens | **Tokens entrenables certificats** |
| optimizer_steps | Nombre d'optimizer steps executats |
| first_step_id | Primer step de la micro-unitat |
| last_step_id | Ultim step de la micro-unitat |
| issued_at | Timestamp d'emissio |
| receipt_nonce | Nonce per protegir contra replay |
| receipt_key_id | Identificador de la clau usada per signar |

### Seguretat

- Serialitzacio canonica (JSON, keys sorted)
- HMAC o signatura digital sobre la serialitzacio
- Rotacio de claus (key_id permet canviar clau sense invalidar receipts anteriors)
- Expiracio (timestamp + finestra de validesa configurable)
- Proteccio contra replay: receipt_nonce + worker_id + round_id
