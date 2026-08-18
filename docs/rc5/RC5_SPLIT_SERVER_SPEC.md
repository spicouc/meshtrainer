# RC5 — Split Server Specification

**Versio:** 1.1.0-draft (corregit)

---

## 1. Abast

El Split Server te responsabilitats limitades i especifiques.
**NO agrega deltes. NO tanca FedAvg.** Aixo es responsabilitat exclusiva del Coordinator.

## 2. Responsabilitats

| Funcio | Descripcio |
|---|---|
| Embeddings | Tram servidor del model: calcula embeddings a partir del text rebut |
| Forward + loss | Computa forward pass i loss |
| Backward + cut gradient | Propaga gradients i emet el gradient del cut cap al worker |
| Recompte de tokens entrenables | Certifica `effective_trainable_tokens` per receipt |
| Receipts autenticats | Genera, signa i emet receipts contextuals |
| Scheduler de micro-unitats | Cua de micro-unitats pendents, assignacio ordenada |
| Backpressure | Control de flux: limita micro-unitats simultànies per worker |

## 3. El Split Server NO fa

- Agregacio de deltes
- FedAvg
- Ledger de contribucions
- Validacio de checkpoints
- Seleccio de contribucions ACTIVE
- Checkpoint global

## 4. Receipts

El Split Server es el **unic** que pot emetre receipts autenticats.
El worker mai genera receipts.

El receipt es genera despres de `step.commit` i conte tots els camps
especificats a RC5_PROTOCOL_SPEC.md, incloent:

- `effective_trainable_tokens` — certificats pel Split Server
- `receipt_nonce` — nonce criptografic
- `receipt_key_id` — clau usada per HMAC/signatura

## 5. Estats de la micro-unitat (al Split Server)

```
PENDING → ASSIGNED → STEP_OPEN → STEP_EMBEDDING → STEP_CUT_ACTIVATION
                  → STEP_CUT_GRADIENT → STEP_COMMIT → COMPLETED
                  → FAILED → PENDING (reassignacio)
                  → TIMEOUT → PENDING (reassignacio)
```

## 6. Configuracio

| Parametre | Defecte | Descripcio |
|---|---|---|
| max_active_per_worker | 1 | Maxim d'assignacions ACTIVE per worker |
| backpressure_limit | 100 | Micro-unitats en cua maxima |
| worker_timeout_s | 120 | Timeout per step |
| receipt_key_rotation_days | 30 | Rotacio de claus de receipts |

## 7. Per RC5.1 Tiny Split PoC

- FP32 (no quantitzacio adaptativa)
- Model tiny fix (no canvi de model dins la ronda)
- Cut fix
- LoRA fix
- Sense canvi de backend dins la ronda
- JSON-RPC + base64 per tensors (binari opcional per fases posteriors)
