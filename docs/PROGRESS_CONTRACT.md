# PROGRESS CONTRACT — v1.4.1 (P1)

El servidor és l'ÚNICA font autoritativa del progrés. La UI només
representa el valor rebut — mai el deriva ni l'estima.

## Camp `progress` (afegit a `GET /api/jobs/{id}`)

```json
{
  "overall_fraction": 0.0..1.0,
  "round_current": 3,
  "round_total": 4,
  "stage": "TRAINING_WORKERS",
  "workers_done": 1,
  "workers_total": 2
}
```

## Pes del progrés (fórmula documentada)

Per ronda completa (FedAvg fet) = `1 / round_total` de progrés.

| Estat del job | overall_fraction | stage |
|---|---|---|
| DRAFT / READY / STARTING | `0.0` | PREPARING |
| RUNNING (ronda en curs) | `rounds_done / round_total` | TRAINING_WORKERS |
| RECOVERING | `rounds_done / round_total` | ROUND_SETUP |
| CANCELLING | `rounds_done / round_total` | FINALIZING |
| COMPLETED | **`1.0` exacte** | COMPLETED |
| FAILED / CANCELLED | `rounds_done / round_total` (mai forçat a 1.0) | FINALIZING |

- `rounds_done` = nombre d'events `round.fedavg` persistits
- La fracció és **monòtona creixent** dins d'un mateix run (els events
  `round.fedavg` només s'afegeixen, mai es retiren)
- MAI disminueix en: browser reload, SSE reconnect, API refresh,
  application restart/reconciliation (recompte d'events persistent)

## Semàntica

- La barra representa **treball completat** (rounds finalitzats), NO temps
  estimat. No es mostra ETA.
- `workers_done`/`workers_total` provenen dels events `worker.spawn`/
  `worker.state` persistits (workers REALS).
- L'estadi per ronda (ROUND_SETUP → TRAINING_WORKERS → VALIDATING → FEDAVG)
  és una ampliació futura: v1.4.1 exposa el stage derivat de l'estat del job.
