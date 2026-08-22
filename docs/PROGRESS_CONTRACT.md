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

## Pes del progrés (fórmula documentada — v1.4.1 R1)

El JobRunner emet events `progress.stage` (capa application) abans/després de
cada etapa; l'últim event mana (font autoritativa). Pesos deterministes:

| Stage | Fracció dins la ronda | overall_fraction |
|---|---|---|
| PREPARING | — | `0.0` |
| ROUND_SETUP | 5% | `(r-1 + 0.05) / R` |
| TRAINING_WORKERS (inici) | 10% | `(r-1 + 0.10) / R` |
| WORKERS_DONE | 75% | `(r-1 + 0.75) / R` |
| VALIDATING | 88% | `(r-1 + 0.88) / R` |
| FEDAVG | 97% | `(r-1 + 0.97) / R` |
| FINALIZING | — | `(R + 0.99) / R` (≈1.0) |
| COMPLETED | — | **`1.0` exacte** |

- `r` = ronda actual (1-based), `R` = rounds totals
- Amb 1 ronda: la barra passa per 0.05, 0.10, 0.75, 0.88, 0.97 abans de
  COMPLETED=1.0 — **mai un salt directe 0→100**
- `workers_total` = sempre 2 (workers certificats) en aquesta release
- MAI disminueix dins d'un mateix run (events persistits, ordre per seq)
- FAILED/CANCELLED: mai forçat a 1.0 (es manté l'últim valor ≤ 0.99)

## Camps derivats (fallback sense progress.stage)

- La barra representa **treball completat** (rounds finalitzats), NO temps
  estimat. No es mostra ETA.
- `workers_done`/`workers_total` provenen dels events `worker.spawn`/
  `worker.state` persistits (workers REALS).
- L'estadi per ronda (ROUND_SETUP → TRAINING_WORKERS → VALIDATING → FEDAVG)
  és una ampliació futura: v1.4.1 exposa el stage derivat de l'estat del job.
