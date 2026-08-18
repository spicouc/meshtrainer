# RC5.3 Stage B R5 — Adversarial Report

Execució: python3 rc5_3_adversarial_tests.py → 33/33 PASS (28 ADV-R3 + 5 sub-checks)

| ID | Prova | Resultat |
|---|---|---|
| ADV-R3-01 | server artifact hash real | PASS |
| ADV-R3-02 | fake server hash rejected | PASS |
| ADV-R3-03 | altered server bytes rejected | PASS |
| ADV-R3-04 | server artifact restart exact | PASS |
| ADV-R3-05 | shared worker base A/B | PASS |
| ADV-R3-06 | shared base adapter A/B/round | PASS |
| ADV-R3-07 | failed model registration atomic | PASS |
| ADV-R3-08 | assignment same payload idempotent | PASS |
| ADV-R3-09 | assignment changed shard rejected | PASS |
| ADV-R3-10 | assignment changed ETT rejected | PASS |
| ADV-R3-11 | assignment changed worker rejected | PASS |
| ADV-R3-12 | calibration same payload idempotent | PASS |
| ADV-R3-13 | calibration changed payload rejected | PASS |
| ADV-R3-14 | uncalibrated worker rejected | PASS |
| ADV-R3-15 | explicit parent adapter | PASS |
| ADV-R3-16 | r10/r2 lineage not lexical | PASS |
| ADV-R3-17 | contribution not from upload rejected | PASS |
| ADV-R3-18 | contribution receipt mismatch rejected | PASS |
| ADV-R3-19 | same contribution changed payload rejected | PASS |
| ADV-R3-20 | SUPERSEDED excluded | PASS |
| ADV-R3-21 | exact assignment quorum | PASS |
| ADV-R3-22 | restart full adapter equality | PASS |
| ADV-R3-23 | journal survives restart | PASS |
| ADV-R3-24 | no second optimizer after restart | PASS |
| ADV-R3-25 | CLOSED immutable | PASS |
| ADV-R3-26 | adapter Round 2 oracle | PASS |
| ADV-R3-27 | alien backward rejected | PASS |
| ADV-R3-28 | ETT derived from labels | PASS |

Nota: ADV-R3-15b, 16a/b/c, 20b, 25b són sub-comprovacions addicionals
dins les mateixes proves (5 extra).
