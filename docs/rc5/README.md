# RC5 — Documentation Index

**Directori:** docs/rc5/

## Documents

| Document | Descripcio |
|---|---|
| `RC5_ARCHITECTURE.md` | Visio general, topologia 3 capes, responsabilitats, flux |
| `RC5_WORK_UNIT_MODEL.md` | Micro-unitat, steps, tokens efectius, ledger de contribucions |
| `RC5_PROTOCOL_SPEC.md` | Metodes JSON-RPC, transport, receipts autenticats |
| `RC5_SPLIT_SERVER_SPEC.md` | Split Server: embeddings, forward/backward, cut, receipts |
| `RC5_SECURITY_MODEL.md` | Autenticacio, receipts signats, deteccio d'abusos |
| `RC5_NUMERICAL_VALIDATION_PLAN.md` | 12 tests T-SPLIT, tolerancies, perfil congelat |
| `RC5_GATE_SPECIFICATION.md` | Fases RC5.0 a RC5.4, criteris d'acceptacio |

## Dependencies

```
RC5_ARCHITECTURE.md
  ├── RC5_WORK_UNIT_MODEL.md (defineix micro-unitat + ledger)
  │     ├── RC5_PROTOCOL_SPEC.md (com es transmet)
  │     └── RC5_SPLIT_SERVER_SPEC.md (qui procesa embeddings/backward)
  ├── RC5_SECURITY_MODEL.md (receipts autenticats)
  ├── RC5_NUMERICAL_VALIDATION_PLAN.md (tests)
  └── RC5_GATE_SPECIFICATION.md (criteris)
```

## Mapa de responsabilitats

| Funcio | Coordinator | Split Server | Worker |
|---|---|---|---|
| Registre workers | ✅ | — | — |
| Calibratge | ✅ | — | ✅ |
| Assignacio treball | ✅ | — | — |
| Ledger contribucions | ✅ | — | — |
| FedAvg | ✅ | — | — |
| Checkpoint global | ✅ | — | — |
| Embeddings | — | ✅ | — |
| Forward + loss | — | ✅ | — |
| Backward + cut gradient | — | ✅ | — |
| Receipts autenticats | — | ✅ | — |
| Scheduler / backpressure | — | ✅ | — |
| Execucio steps | — | — | ✅ |
| Pujar contribucio | — | — | ✅ |
