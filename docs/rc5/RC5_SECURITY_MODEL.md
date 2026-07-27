# RC5 — Security Model

**Versio:** 1.1.0-draft (corregit)

---

## 1. Principis

- El Coordinator es la font de veritat per a identitat, ledger i checkpoint
- El Split Server es la font de veritat per a receipts i tokens certificats
- El worker es no-confiable per defecte
- El Split Server signa els receipts; el worker mai
- Totes les contribucions porten receipt autenticat del Split Server

## 2. Autenticacio

| Component | Autenticacio | Token |
|---|---|---|
| Worker → Coordinator | worker_id + auth_token (generat a `worker.join`) | Rotable |
| Worker → Split Server | worker_id + session_id | Compartit pel Coordinator |
| Split Server → Coordinator | API Key | Compartida, rotable |

## 3. Receipts

- Generats i autenticats exclusivament pel Split Server
- Serialitzacio canonica (JSON, sorted keys)
- HMAC o signatura digital
- Rotacio de claus via `receipt_key_id`
- Nonce per protegir contra replay
- Expiracio temporal (finestra configurable)
- El worker no pot generar ni modificar receipts

## 4. Integritat

- Cada delta porta SHA-256 del contingut
- Cada contribucio porta receipt autenticat del Split Server
- El Coordinator verifica el receipt + hash abans d'acceptar
- El ledger de contribucions es immutable (només append)
- El checkpoint final porta SHA-256 de tots els artefactes

## 5. Deteccio d'abusos

| Abus | Deteccio | Accio |
|---|---|---|
| Worker envia receipt fals | Signatura invalida | REJECTED |
| Worker reutilitza receipt | Nonce duplicat o fora de context | REJECTED |
| Worker falseja tokens | effective_trainable_tokens no coincideixen amb el recompte del Split Server | REJECTED + marcar worker |
| Worker envia dues contribucions same assignment | Ledger: SUPERSEDED | La mes recent reemplaca l'anterior |
| Worker excedeix timeout | Heartbeat perdut | work.abort + reassignacio |
| Worker duplica nonce | Replay detection | REJECTED |
| key_id retirat | Receipt rebutjat per clau no vigent | Worker necessita nou receipt |
