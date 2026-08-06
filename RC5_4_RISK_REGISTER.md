# RC5.4 RISK REGISTER — R2 (addenda)

**Data**: 2026-08-06 | **Base**: d46dc1b

## Riscos nous identificats a la integració real (R2)

| ID | Risc | Severitat | Mitigació | Estat |
|---|---|---|---|---|
| RISK-17 | Journal cross-run/round/assignment/unit (binding incomplet) | CRÍTIC | Binding complet dels 8 camps a journal_set; ADV-R4-15..19, MUT-R4-13..17 | MITIGAT |
| RISK-18 | Escriptura amb lease RELEASED | ALT | Només ACTIVE/RENEWED escriuen; ADV-R4-20, MUT-R4-18 | MITIGAT |
| RISK-19 | Regressió d'estat del journal (COMMITTED->PREPARED) | CRÍTIC | Màquina d'estats estricta; ADV-R4-25, MUT-R4-19 | MITIGAT |
| RISK-20 | Receipt/update_id conflictius acceptats | CRÍTIC | Exactly-once conflictiu (REJECTED); ADV-R4-27/28, MUT-R4-20 | MITIGAT |
| RISK-21 | Reacquire després de RELEASED | ALT | acquire només després EXPIRED/ABORTED; ADV-R4-21, MUT-R4-21 | MITIGAT |
| RISK-22 | Expiració amb `<` (límit exacte no expira) | MITJÀ | `<=` a _expire_overdue; ADV-R4-23, MUT-R4-22 | MITIGAT |
| RISK-23 | expire sense fila modificada retorna èxit | MITJÀ | rowcount==1 obligatori; ADV-R4-23b, MUT-R4-23 | MITIGAT |
| RISK-24 | Pipeline operatiu sense lease | CRÍTIC | _require_lease_params a tots els mètodes; ADV-R4-29, MUT-R4-24 | MITIGAT |
| RISK-25 | Worker antic continua després de reassign | CRÍTIC | Lease EXPIRED no renovable ni contributiva; ADV-R4-30 | MITIGAT |
| RISK-26 | Delta no determinista entre processos (restart) | ALT | WorkerRuntime re-carregat d'artefactes reals; restart real PASS (delta idèntic) | MITIGAT |
| RISK-27 | Adapter crash != adapter no-crash | CRÍTIC | Comparació real a R8; adapter idèntic 13cb8687... | MITIGAT |
| RISK-28 | Mid-backward deixa delta parcial | CRÍTIC | Crash abans d'APPLIED -> cap delta; mid-backward real PASS | MITIGAT |

## Decisions de disseny R2

- **Restart real**: 2 processos OS reals (os._exit a la fase A) sobre la mateixa
  SQLite; el servidor HTTP es re-arrenca a la fase B; step.open és idempotent
  (mateixa activation) — verificat experimentalment.
- **Server exactly-once**: el servidor rebutja un segon forward.fetch amb
  "Same key, different payload" — el gradient es recupera de l'estat de la
  fase A (no es re-fetch).
- **Reassignació de micro_unit**: la clau lògica step.open és
  run:round:assignment:micro_unit; una nova revisió requereix un micro_unit
  nou (protocol correcte, no es reutilitza la clau).
