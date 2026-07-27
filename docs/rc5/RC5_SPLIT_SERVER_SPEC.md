# RC5 — Split Server Specification

**Document:** RC5_SPLIT_SERVER_SPEC.md
**Versio:** 1.1.0-draft

---

## 1. Responsabilitats

El Split Server es el component nou central de v1.1. Les seves
responsabilitats son:

- Fragmentar el dataset en micro-unitats
- Assignar micro-unitats als workers (unica ACTIVE per worker)
- Rebre resultats i receipts
- Detectar fallides i reassignar
- Aplicar backpressure
- Agregar deltes per ronda
- Enviar deltes agregats al Coordinator

## 2. Fragmentacio

El dataset d'entrenament es fragmenta en micro-unitats de mida
configurable (ex: 32-128 tokens per unitat). Cada micro-unitat conte:

- Un fragment de text (ex: 1 frase)
- La metadada del fragment (dataset, index, longitud)
- La seed determinista per reproduir el calcul

## 3. Backpressure

El Split Server controla el flux d'assignacions:

- Si hi ha mes workers actius que micro-unitats disponibles,
  alguns workers esperen
- Si un worker es massa lent, rep menys assignacions
- Si un worker falla repetidament, es desconnecta
- El Split Server pot aturar temporalment les assignacions si
  el Coordinator encara no ha acabat d'agregar la ronda anterior

## 4. Estats d'una micro-unitat

```
PENDING → ASSIGNED → COMPLETED
                   → FAILED → PENDING (reassignacio)
                   → TIMEOUT → PENDING (reassignacio)
```

## 5. Estats d'un worker

```
REGISTERED → ACTIVE → IDLE
                    → ACTIVE (una altra assignacio)
                    → DISCONNECTED
                    → SUSPICIOUS (calibratge fallat)
```

## 6. Configuracio

| Parametre | Defecte | Descripcio |
|---|---|---|
| max_units_per_worker | 1 | Maxim ACTIVE per worker |
| unit_size_tokens | 64 | Tokens per micro-unitat |
| backpressure_limit | 100 | Micro-unitats en espera maxima |
| worker_timeout_s | 120 | Timeout per worker |
| calibration_threshold | 0.1 | Tolerancia declarat/observat |
