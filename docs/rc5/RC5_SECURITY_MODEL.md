# RC5 — Security Model

**Document:** RC5_SECURITY_MODEL.md
**Versio:** 1.1.0-draft

---

## 1. Principis

- El Coordinator es la font de veritat
- El Split Server es el guardia d'acces
- Els workers son no-confiables per defecte
- Tots els deltes porten hash
- Tots els receipts porten firma opcional

## 2. Autenticacio

| Component | Autenticacio | Token |
|---|---|---|
| Split Server → Coordinator | API Key | Compartida, rotable |
| Worker → Split Server | worker_id + auth_token | Generat al registre |

## 3. Integritat

- Cada delta porta SHA-256 del contingut
- El Coordinator verifica el hash abans d'agregar
- El manifest de ronda conte tots els hashes dels deltes acceptats
- El checkpoint final porta SHA-256 de tots els artefactes

## 4. Deteccio d'abusos

| Abús | Deteccio | Accio |
|---|---|---|
| Worker envia deltes falsos | Calibratge declarat/observat | Marcar SUSPICIOUS |
| Worker duplica resultats | Idempotencia per unit_id | Ignorar duplicat |
| Worker excedeix timeout | Heartbeat perdut | Reassignar unitat |
| Worker envia molts errors | Ratio falles/exits | Desconnectar |

## 5. No inclos en v1.1

- Xifrat de deltes en transit (TLS opcional)
- Firma digital de receipts (opcional)
- Zero-knowledge proofs
- Homonorphic encryption
