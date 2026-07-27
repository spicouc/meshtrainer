# RC5.0 — Protocol Freeze Report

**Data:** 2026-07-27
**Projecte:** meshtrainer v1.1
**Branca:** rc5.0-protocol-freeze

---

## 1. Resum de decisions

| Decisio | Opcio escollida | Alternativa descartada |
|---|---|---|
| Topologia | Coordinator + Split Server + Workers | Coordinator directe (v1.0) |
| Worker | Browser Worker (JavaScript/TypeScript) | Worker Python (v1.0) |
| Atom transaccional | Micro-unitat | Batch sencer (v1.0) |
| Atom estadistic | Token efectiu | Mostra per batch |
| Assignacio | Unica ACTIVE per worker | Multiple ACTIVE |
| Agregacio | FedAvg sobre deltes | Mateix que v1.0 |
| Receipts | Contextuals (run, round, unit, tokens) | Sense receipts (v1.0) |
| Calibratge | Declarat vs observat | No calibratge (v1.0) |
| Backpressure | Des de Split Server | No backpressure (v1.0) |
| Comunicacio | JSON-RPC 2.0 HTTP | Mateix que v1.0 |

## 2. Justificacio tecnica

**Split Server:** Necessari per escalar a desenes de workers al navegador
sense sobrecarregar el Coordinator.

**Micro-unitat:** Permet distribucio granularda i reassignacio rapida
en cas de fallida d'un worker.

**Unica ACTIVE:** Simplifica la maquina d'estats i evita conflictes
de concorrencia al worker.

**Receipts:** Proporcionen auditabilitat i permeten calibratge entre
el declarat i l'observat.

## 3. Desviacions respecte v1.0

| Aspecte | v1.0 | v1.1 | Impacte |
|---|---|---|---|
| Worker runner | Python | Navegador | Cal implementar WebGPU |
| Fragmentacio | Batch | Micro-unitat | Mes RPCs, mes granulariat |
| Estats | 3 (pending, running, done) | 5+ (pending, assigned, active, completed, failed, timeout) | Mes complexitat |
| Config | 10 parametres | 15+ parametres | Mes flexibilitat |

## 4. Riscos coneguts

| Risc | Probabilitat | Impacte | Mitigacio |
|---|---|---|---|
| WebGPU no disponible | Mitjana | Alt | Fallback a CPU |
| Latencia RPC massa alta | Mitjana | Mig | Batch de micro-unitats |
| Worker malicios | Baixa | Alt | Calibratge + signatures |
| Complexitat d'estats | Mitjana | Mig | Maquina d'estats formal |

## 5. Punts oberts per RC5.1

- [ ] Mida optima de la micro-unitat (32, 64, 128 tokens?)
- [ ] Estrategia de reassignacio (immediata vs esperar)
- [ ] Llindar de calibratge (0.05, 0.1, 0.2?)
- [ ] Format exacte del receipt (camps opcionals?)
- [ ] Firewall entre Split Server i Coordinator
