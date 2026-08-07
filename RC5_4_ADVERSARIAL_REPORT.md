# RC5.4 ADVERSARIAL REPORT — R3 (51/51 + HTTP 17/17)

**Data**: 2026-08-07 | **Resultat: 51/51 (unit) + 17/17 (HTTP) PASS**

## Suite unitària (rc5_4_adversarial_tests.py): 51/51 PASS
ADV-01..14 originals + ADV-R4-15..30 (cross-run/round/assignment/unit/session,
write after RELEASED, reacquire after RELEASED, expire alien, exact expires_at,
negative TTL, COMMITTED->PREPARED, direct COMMITTED, SUBMITTED sense delta,
conflicting receipt/update_id, pipeline sense lease, old worker).

## Suite HTTP real (rc5_4_http_lease_tests.py): 17/17 PASS
Totes per HTTP real contra el dispatcher lease-gated (sense _FakePipeline):

| Test | Cas | Resultat |
|---|---|---|
| H-01 | step.open sense lease rejected (missing_lease_params) | PASS |
| H-02 | wrong lease nonce rejected | PASS |
| H-03 | wrong session rejected | PASS |
| H-04 | lease cross-run rejected | PASS |
| H-05 | lease cross-assignment rejected | PASS |
| H-06 | expired lease forward rejected | PASS |
| H-07 | expired lease backward rejected | PASS |
| H-08 | expired lease commit rejected | PASS |
| H-09 | expired lease upload rejected | PASS |
| H-10 | released lease rejected | PASS |
| H-11 | old worker rejected after reassign (lease de B, worker A) | PASS |
| H-12 | contribution registration sense lease rejected | PASS |
| H-12b | registration amb lease inexistent (unknown_lease) rejected | PASS |
| H-13 | backward.fetch sense lease rejected (dispatcher) | PASS |
| H-14 | step.commit sense lease rejected (dispatcher) | PASS |
| H-15 | checkpoint.upload sense lease rejected (dispatcher) | PASS |
| H-16 | lease cross-unit rejected | PASS |

## Nota de disseny
El gate de lease s'executa ABANS de la idempotència del dispatcher: un retry
idempotent amb lease expirada NO pot obtenir la resposta cachejada. Això es
va descobrir al mid-backward (el backward.fetch d'A tornava cache) i es va
corregir a ProtocolHandler54.handle.
