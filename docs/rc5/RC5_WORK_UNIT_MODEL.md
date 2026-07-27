# RC5 — Work Unit Model

**Versio:** 1.1.0-draft (corregit)

---

## 1. Jerarquia

```
Run
 └── Ronda (Round)
      └── Assignment (per worker, una unica ACTIVE)
           └── Micro-unitat (Work Unit)
                └── Steps (conjunt fix de passos transaccionals)
```

## 2. Micro-unitat com a atom transaccional

La micro-unitat es un conjunt fix de **steps transaccionals**.
No es defineix per la seva mida en tokens (32, 64, 128).

Cada micro-unitat conte:

| Camp | Descripcio |
|---|---|
| micro_unit_id | Identificador unic |
| assignment_id | Assignment a la que pertany |
| steps | Llista d'steps |
| data_shard_hash | Hash del fragment de dades |
| base_adapter_hash | Hash de l'adapter base |
| created_at | Timestamp |

## 3. Steps

Un step es una unitat de proces indivisible dins la micro-unitat:

| Step | Descripcio |
|---|---|
| step.open | Worker obre el step |
| step.embedding | Worker envia text → Split Server, rep embeddings |
| step.cut_activation | Split Server fa forward parcial, envia activacio del cut |
| step.cut_gradient | Worker fa backward parcial, envia gradient del cut |
| step.commit | Ambdos confirmen |

## 4. Tokens efectius com a atom estadistic

Els **effective_trainable_tokens** son certificats pel Split Server
dins del receipt. No son declarats pel worker ni derive from
longitud bruta del text.

S'utilitzen exclusivament com a pes a FedAvg:

```
Δ_global = Σ(ett_i × Δ_i) / Σ(ett_i)
```

on `ett_i = effective_trainable_tokens` certificats del receipt.

## 5. Contribucio unica ACTIVE per assignment

Per cada (run_id, round_id, assignment_id, worker_id) nomes pot existir
UNA contribucio ACTIVE.

Si un worker envia una nova contribucio per la mateixa assignment,
la nova substitueix l'anterior (SUPERSEDED).

El ledger registra:

- contribution_id
- contribution_revision
- supersedes_contribution_id
- checkpoint_id
- resume_state_id

Estats:

```
RECEIVED → VALIDATED → ACTIVE → AGGREGATED
                     → REJECTED
SUPERSEDED (quan una contribucio mes recent la substitueix)
```

No es poden agregar dues contribucions de la mateixa assignment.
FedAvg es calcula una unica vegada al tancament de ronda sobre
el ledger de contribucions ACTIVE.
