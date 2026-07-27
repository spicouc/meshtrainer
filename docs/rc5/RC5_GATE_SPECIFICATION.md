# RC5 — Gate Specification

**Document:** RC5_GATE_SPECIFICATION.md
**Versio:** 1.1.0-draft

---

## 1. Fases RC5

| Fase | Nom | Objectiu |
|---|---|---|
| RC5.0 | Protocol Freeze | Documentacio, no codi |
| RC5.1 | Tiny Split PoC | Primer Split Server funcional |
| RC5.2 | Browser Worker | Worker al navegador |
| RC5.3 | Integracio | Split + Coordinator + Workers |
| RC5.4 | Gate Final | Validacio completa |

## 2. Criteris RC5.0 (aquesta fase)

- [ ] 8 documents d'arquitectura creats
- [ ] Totes les decisions reflectides
- [ ] Terminologia consistent
- [ ] Sense contradiccions entre documents
- [ ] Report de protocol freeze generat

## 3. Criteris RC5.1 (Tiny Split PoC)

- [ ] Split Server escolta en un port
- [ ] Accepta connexions de workers
- [ ] Fragmenta un dataset en micro-unitats
- [ ] Assigna micro-unitats a workers
- [ ] Rep receipts
- [ ] Backpressure basic
- [ ] Test T-SPLIT-1 PASS
- [ ] Regressions v1.0 intactes

## 4. Criteris RC5.4 (Gate Final)

- Tots els tests T-SPLIT-1 a T-SPLIT-5 PASS
- Split Server + Coordinator + 2 workers E2E
- FedAvg identic a v1.0
- Calibratge funcionant
- Backpressure funcionant
- Security model implementat
- Documentacio completa
