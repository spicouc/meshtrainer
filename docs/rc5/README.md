# RC5 — Documentation Index

**Directori:** docs/rc5/

| Document | Descripcio |
|---|---|
| `RC5_ARCHITECTURE.md` | Visio general, components, topologia, flux |
| `RC5_WORK_UNIT_MODEL.md` | Micro-unitats, tokens efectius, deltes |
| `RC5_PROTOCOL_SPEC.md` | Metodes JSON-RPC, receipts, calibratge |
| `RC5_SPLIT_SERVER_SPEC.md` | Split Server: fragmentacio, backpressure, estats |
| `RC5_SECURITY_MODEL.md` | Autenticacio, integritat, deteccio d'abusos |
| `RC5_NUMERICAL_VALIDATION_PLAN.md` | Tests de validesa numerica T-SPLIT-1 a 5 |
| `RC5_GATE_SPECIFICATION.md` | Fases RC5, criteris d'acceptacio |

## Dependencies entre documents

```
RC5_ARCHITECTURE.md (visio general)
  ├── RC5_WORK_UNIT_MODEL.md (defineix micro-unitat)
  │     └── RC5_PROTOCOL_SPEC.md (com es transmet)
  ├── RC5_SPLIT_SERVER_SPEC.md (qui la procesa)
  │     └── RC5_SECURITY_MODEL.md (com es protegeix)
  └── RC5_NUMERICAL_VALIDATION_PLAN.md (com es valida)
        └── RC5_GATE_SPECIFICATION.md (criteris d'acceptacio)
```
