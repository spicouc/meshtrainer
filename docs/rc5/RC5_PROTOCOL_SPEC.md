# RC5 — Protocol Specification

**Document:** RC5_PROTOCOL_SPEC.md
**Versio:** 1.1.0-draft

---

## 1. Canal de comunicacio

Totes les comunicacions son JSON-RPC 2.0 sobre HTTP.

| Origen | Desti | Protocol |
|---|---|---|
| Browser Worker | Split Server | JSON-RPC HTTP |
| Split Server | Coordinator | JSON-RPC HTTP |

## 2. Metodes

### Coordinator → Split Server

| Metode | Descripcio |
|---|---|
| `split.run.start` | Inicia un run, retorna run_id |
| `split.run.stop` | Atura un run |
| `split.round.start` | Inicia una ronda |
| `split.round.end` | Finalitza una ronda, retorna deltes |
| `split.worker.register` | Notifica registre d'un worker |
| `split.worker.unregister` | Notifica baixa d'un worker |

### Split Server → Coordinator

| Metode | Descripcio |
|---|---|
| `coord.run.ready` | Split Server preparat per rebre workers |
| `coord.round.complete` | Ronda completada, envía deltes agregats |
| `coord.worker.status` | Canvi d'estat d'un worker |

### Browser Worker → Split Server

| Metode | Descripcio |
|---|---|
| `worker.register` | Registra worker, retorna worker_id |
| `worker.heartbeat` | Heartbeat amb estat i progrés |
| `worker.request_unit` | Sol·licita micro-unitat |
| `worker.submit_unit` | Envia resultat + receipt de micro-unitat |

## 3. Receipt contextual

Cada micro-unitat completada genera un receipt amb:

```json
{
  "worker_id": "w-abc123",
  "unit_id": "u-000042",
  "assignment_id": "a-001",
  "run_id": "run-xyz",
  "round": 3,
  "tokens_processed": 512,
  "delta_sha256": "abc...",
  "loss": 2.45,
  "grad_norm": 1.23,
  "timestamp": "...",
  "signature": "..."  (firma opcional del worker)
}
```

El Coordinator utilitza els receipts per:
- Verificar completesa de la ronda
- Calcular tokens efectius totals
- Detectar workers maliciosos o erronics
- Generar el manifest final

## 4. Calibratge declarat vs observat

El Split Server compara:

- **Declarat:** tokens que el worker diu haver processat
- **Observat:** tokens mesurats per la micro-unitat (longitud del text)

Si la diferencia supera un llindar, el worker es marca com a sospitos
i la micro-unitat es re-assigna.
