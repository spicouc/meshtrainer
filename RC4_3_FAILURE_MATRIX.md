# RC4.3 — Failure Matrix

**Data:** 2026-07-26  
**Projecte:** meshtrainer

---

| Escenari | Test | Mecanisme | Resultat | Corrupcio? | Recuperable? |
|---|---|---|---|---|---|
| Worker falla durant entrenament | T17 | OOM detectat, skip | ✅ | No | Si (reintent) |
| Worker falla despres de computar delta | T24 | Cache pre-submit | ✅ | No | Si (cache) |
| Worker no envia heartbeat | T4 | Lease expiry | ✅ | No | Si (reassignacio) |
| Worker envia tard | T5 | Lease check | ✅ | No | No (cal nou batch) |
| Worker envia hash incorrecte | T11 | SHA-256 validation | ✅ | No | Si (reenviament) |
| Worker envia dades corruptes | T11 | Checksum | ✅ | No | Si (reenviament) |
| Worker referencia batch inexistent | T11 | Referential integrity | ✅ | No | Si (nou batch) |
| Worker envia generacio obsoleta | T9 | Generation counter | ✅ | No | Si (batch fresc) |
| Coordinator fa aggregacio parcial | T10 | Partial aggregation | ✅ | No | Si (completar despres) |
| Coordinator agrega sense dades | T10 | Empty aggregation | ✅ | N/A | N/A (no data) |
| Worker envia mateix delta dos cops | T12 | Idempotency check | ✅ | No | Si (ignorat) |
| Worker envia delta diferent mateix batch | T12 | Collision detection | ✅ | No | Si (rebutjat) |
| Worker falla per backward pass | T17 | Graceful skip | ✅ | No | Si (reintent) |

## Llegenda

- ✅: Gestionat correctament pel sistema
- Corrupcio?: Si la incidencia pot corrompre dades persistents
- Recuperable?: Si el worker o coordinator pot continuar despres de la incidencia
