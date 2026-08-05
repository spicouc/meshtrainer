# RC5.4 ADVERSARIAL REPORT

**Data**: 2026-08-05 | **Suite**: rc5_4_adversarial_tests.py | **Resultat: 27/27 PASS, 0 FAIL**

## Les 14 proves mínimes obligatòries (ordre del Supervisor)

1. **Lease aliena rebutjada** (ADV-01): un worker no propietari no pot usar la lease. PASS.
2. **Renewal aliena rebutjada** (ADV-02): només el worker/session propietari renova. PASS.
3. **Renewal després d'expiry rebutjada** (ADV-03): lease EXPIRED no es pot renovar. PASS.
4. **Contribució després d'expiry rebutjada** (ADV-04): journal_set amb lease EXPIRED llança LeaseError. PASS.
5. **Stale lease nonce rebutjat** (ADV-05): nonce incorrecte llança LeaseError. PASS.
6. **Doble lease ACTIVE rebutjada** (ADV-06): una unitat només pot tenir una lease ACTIVE. PASS.
7. **Reassignació abans d'expiry rebutjada** (ADV-07): can_reassign False amb lease ACTIVE. PASS.
8. **Receipt de la unitat expirada rebutjat** (ADV-08): journal_set post-expiry rebutjat; unit EXPIRED no és active per unit_key. PASS.
9. **Checkpoint d'una revision antiga rebutjat** (ADV-09): la revisió antiga es manté, la nova és ACTIVE. PASS.
10. **Doble recovery APPLIED no fa optimizer** (ADV-10): el delta no es sobreescriu. PASS.
11. **Recovery amb adapter stale rebutjat** (ADV-11): adapter_hash no es sobreescriu (exactly-once). PASS.
12. **Round close ignora EXPIRED i ABORTED** (ADV-12): can_reassign per a estats finals. PASS.
13. **Worker original no pot completar després de reassignació** (ADV-13): el journal resol a la lease més recent (B); el commit d'A és rebutjat. PASS.
14. **Clock boundary TTL determinista** (ADV-14): just abans d'expiry ACTIVE, just després EXPIRED (rellotge injectable). PASS.

## Checks complementaris (13 addicionals)
- Journal PREPARED persistit i recuperat (REC-02/ADV cross-checks)
- Delta no-overwrite amb valor diferent
- Receipt no-overwrite amb valor diferent
- Checkpoint no-overwrite amb valor diferent
- Adapter no-overwrite amb valor diferent
- Renewal de lease RENEWED
- Release de lease
- Abort de lease
- can_reassign en cada estat
- journal_get per lease_id
- journal_state transitions
- journal_lease_for_unit resolveix a la més recent
- TTL boundary exacte

Tots PASS.
