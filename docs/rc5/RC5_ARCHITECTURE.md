# RC5 — Architecture v1.1

**Document:** RC5_ARCHITECTURE.md
**Versio:** 1.1.0-draft
**Base:** v1.0 (meshtrainer classic)
**Estat:** Protocol Freeze — documentacio, no implementacio

---

## 1. Visio general

RC5 introdueix el **Split Server** com a nou component central, convertint
l'arquitectura coordinator + workers en una topologia de tres capes:

```
Browser Worker 1 ──┐
Browser Worker 2 ──┼── Split Server ── Coordinator
Browser Worker N ──┘
```

El Coordinator delega la gestio dels workers al Split Server, que actua com
a intermediari especialitzat en la fragmentacio i distribucio de micro-unitats
de treball.

## 2. Components

| Component | Rol | Novetat v1.1 |
|---|---|---|
| Coordinator | Orquestracio global, FedAvg, checkpoint | Mateix que v1.0 |
| Split Server | Fragmentacio, assignacio, receipts, backpressure | **Nou** |
| Browser Worker | Execucio de micro-unitats al navegador | Substituix el worker Python |

## 3. Topologia congelada per run

Cada run te una topologia fixa que no canvia durant l'execucio:

- 1 Coordinator
- 1 Split Server (per run)
- N Browser Workers (registrats al inici)

Un cop comencen les rondes, no es poden afegir ni treure workers.

## 4. Flux alt nivell

```
1. Coordinator inicia run
2. Split Server es conecta al Coordinator
3. Browser Workers es registren al Split Server
4. Split Server fragmenta el dataset en micro-unitats
5. Split Server assigna micro-unitats als workers
6. Workers executen i retornen resultats
7. Split Server agrega receipts i retorna deltes al Coordinator
8. Coordinator aplica FedAvg
9. Coordinator genera checkpoint
```

## 5. Canvis respecte v1.0

| Aspecte | v1.0 | v1.1 |
|---|---|---|
| Workers | Python (sequencial) | Navegador (paral·lel) |
| Fragmentacio | Batch sencer | Micro-unitats |
| Intermediari | Coordinator directe | Split Server |
| Transaccionalitat | Per batch | Per micro-unitat |
| Receipts | No | Si, contextuals |
| Backpressure | No | Si (des de Split Server) |
