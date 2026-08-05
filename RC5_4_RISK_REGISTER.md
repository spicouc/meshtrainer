# RC5.4 RISK REGISTER

**Data**: 2026-08-05 | **Branch**: rc5.4-stagea | **Base**: 9c46f65

## Riscos identificats i mitigacions

| ID | Risc | Severitat | Mitigació | Estat |
|---|---|---|---|---|
| RISK-01 | Contribució acceptada després d'EXPIRED | CRÍTIC | Guard al journal_set: lease EXPIRED/ABORTED -> LeaseError; testejat ADV-04, MUT-R4-02 | MITIGAT |
| RISK-02 | Doble optimizer step en retry APPLIED | CRÍTIC | Exactly-once: primer delta guanya (CASE WHEN ... IS NULL); testejat REC-06, ADV-10, MUT-R4-07 | MITIGAT |
| RISK-03 | Receipt duplicat/diferent en retry COMMITTED | CRÍTIC | Exactly-once: primer receipt guanya; testejat REC-08, REC-08b, MUT-R4-08 | MITIGAT |
| RISK-04 | Doble contribució al FedAvg per reassignació | CRÍTIC | Una lease ACTIVE per unitat; reassignació només després d'EXPIRED/ABORTED; testejat REC-05, ADV-06 | MITIGAT |
| RISK-05 | Reassignació abans d'expiry | ALT | can_reassign només per EXPIRED/ABORTED; testejat ADV-07, MUT-R4-06 | MITIGAT |
| RISK-06 | Renewal aliena (worker/session no propietari) | ALT | Validació worker_id + session_id + lease_nonce; testejat ADV-02, ADV-05 | MITIGAT |
| RISK-07 | Stale lease nonce | ALT | Nonce validat a renew i journal_set; testejat ADV-05, MUT-R4-05 | MITIGAT |
| RISK-08 | Adapter stale acceptat en recovery | ALT | adapter_hash no-overwrite; testejat ADV-11, MUT-R4-09 | MITIGAT |
| RISK-09 | Commit de revisió antiga | ALT | journal resol a la lease més recent; testejat ADV-09, ADV-13, MUT-R4-11 | MITIGAT |
| RISK-10 | Expiry ignorat (TTL) | ALT | _expire_overdue transaccional; testejat ADV-14, MUT-R4-01 | MITIGAT |
| RISK-11 | Crash a mig backward recuperat com a segur | CRÍTIC | Mid-backward -> EXPIRED + reassign; testejat REC-05, MUT-R4-12 | MITIGAT |
| RISK-12 | Rellotge no determinista als tests | MITJÀ | Rellotge injectable (now fictici); testejat ADV-14 | MITIGAT |
| RISK-13 | EXPIRED inclòs al FedAvg | CRÍTIC | can_reassign exclou EXPIRED de contribució; testejat REC-05, MUT-R4-10 | MITIGAT |
| RISK-14 | Regressió del nucli congelat RC5.3 | ALT | Hashes verificats vs 9c46f65; regressions al gate combinat (pas 6) | MITIGAT |

## Riscos residuals acceptats

| ID | Risc | Justificació |
|---|---|---|
| RISK-15 | SQLite és l'única font de veritat de leases | Acceptat: transaccions BEGIN IMMEDIATE, una connexió, WAL no requerit |
| RISK-16 | El journal emmagatzema deltas deterministes, no grafs autograd | Acceptat: l'ordre exclou la persistència del graf (stop condition 1 no activada) |

## Decisions de disseny

- **Transaccions aniuables** (`_tx`): BEGIN IMMEDIATE només si no hi ha transacció
  oberta (`in_transaction`), COMMIT/ROLLBACK pel nivell extern. Evita
  "cannot start a transaction within a transaction".
- **Exactly-once per CASE WHEN ... IS NULL** (no COALESCE): el primer valor
  persisteix; un retry amb valor diferent no sobreescriu (COALESCE fallaria
  perquè excluded no és NULL).
- **Exit agregat del mutation gate = 1** si qualsevol suite falla (no suma):
  distingeix fallada d'assertió (exit 1) de timeout (124).
