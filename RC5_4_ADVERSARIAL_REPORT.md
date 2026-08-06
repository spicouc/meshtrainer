# RC5.4 ADVERSARIAL REPORT — R2 (51/51 PASS)

**Data**: 2026-08-06 | **Suite**: rc5_4_adversarial_tests.py | **Resultat: 51/51 PASS, 0 FAIL**

## ADV-01..14 (originals, adaptats a la màquina d'estats estricta)
Totes PASS (lease aliena, renewal aliena, renewal post-expiry, contribució
post-expiry, stale nonce, doble ACTIVE, reassign pre-expiry, receipt
d'expirada, checkpoint revisió antiga, doble APPLIED, adapter stale, round
close, worker original, TTL determinista).

## ADV-R4-15..30 (noves, R2)

| Test | Cas | Resultat |
|---|---|---|
| ADV-R4-15 | cross-run journal rejected | PASS |
| ADV-R4-15b | índex parcial únic present (UNIQUE) | PASS |
| ADV-R4-16 | cross-round journal rejected | PASS |
| ADV-R4-17 | cross-assignment journal rejected | PASS |
| ADV-R4-18 | cross-unit journal rejected | PASS |
| ADV-R4-19 | wrong session journal rejected | PASS |
| ADV-R4-20 | write after RELEASED rejected | PASS |
| ADV-R4-21 | reacquire after RELEASED rejected | PASS |
| ADV-R4-22 | expire alien returns error (+ lease intacta) | PASS |
| ADV-R4-23 | exact expires_at is EXPIRED (now == expires_at) | PASS |
| ADV-R4-23b | expire sense lease -> error (rowcount guard) | PASS |
| ADV-R4-24 | negative TTL rejected + zero TTL rejected | PASS |
| ADV-R4-25 | COMMITTED to PREPARED rejected (regressió) | PASS |
| ADV-R4-26 | direct COMMITTED rejected (NULL -> COMMITTED) | PASS |
| ADV-R4-26b | SUBMITTED sense delta rejected | PASS |
| ADV-R4-27 | conflicting receipt rejected | PASS |
| ADV-R4-28 | conflicting update_id rejected | PASS |
| ADV-R4-29 | pipeline without lease rejected (RC54RecoveryCoordinator) | PASS |
| ADV-R4-30 | old worker rejected after reassign | PASS |
