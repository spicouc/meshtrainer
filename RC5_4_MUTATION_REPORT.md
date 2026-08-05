# RC5.4 MUTATION REPORT

**Data**: 2026-08-05 | **Gate**: RUN_RC5_4_MUTATION_GATE.sh
**Resultat: 12/12 DETECTED | Invalid 0 | Runtime 0 | Not_applied 0 | Timeouts 0 | PASS**

## Metodologia

Cada mutant és un script PATTERN/SUBST que aplica exactament 1 substitució
(replace(...,1)) a una còpia de treball. Es verifiquen: patró present,
applied_count=1, py_compile PASS, execució de la crash matrix i l'adversarial
des del directori mutat, i es registra l'assertion exacta que falla.

## Detall per mutant

| Mutant | Fitxer | applied | py_compile | Exit | Tracebacks | Assertion detectora | Classificació |
|---|---|---|---|---|---|---|---|
| MUT-R4-01 | mut_r4_01_ignore_expiry.py | 1 | PASS | 1 | 0 | FAIL ADV-03 renewal després d'expiry rebutjada | DETECTED |
| MUT-R4-02 | mut_r4_02_accept_expired_contribution.py | 1 | PASS | 1 | 0 | FAIL ADV-04 contribució després d'expiry rebutjada | DETECTED |
| MUT-R4-03 | mut_r4_03_double_active_lease.py | 1 | PASS | 1 | 0 | FAIL ADV-06 doble lease ACTIVE rebutjada | DETECTED |
| MUT-R4-04 | mut_r4_04_alien_renewal.py | 1 | PASS | 1 | 0 | FAIL ADV-02 renewal aliena rebutjada | DETECTED |
| MUT-R4-05 | mut_r4_05_ignore_nonce.py | 1 | PASS | 1 | 0 | FAIL ADV-05 stale lease nonce rebutjat | DETECTED |
| MUT-R4-06 | mut_r4_06_reassign_before_expiry.py | 1 | PASS | 1 | 0 | FAIL ADV-07 reassign abans expiry rebutjat | DETECTED |
| MUT-R4-07 | mut_r4_07_double_optimizer.py | 1 | PASS | 1 | 0 | FAIL ADV-10 no overwrite delta after APPLIED | DETECTED |
| MUT-R4-08 | mut_r4_08_different_receipt.py | 1 | PASS | 1 | 0 | FAIL REC-08b receipt no overwrite (byte-for-byte) | DETECTED |
| MUT-R4-09 | mut_r4_09_stale_adapter.py | 1 | PASS | 1 | 0 | FAIL ADV-11 adapter no overwrite (exactly-once) | DETECTED |
| MUT-R4-10 | mut_r4_10_expired_in_fedavg.py | 1 | PASS | 1 | 0 | FAIL REC-05 reassignable after EXPIRED | DETECTED |
| MUT-R4-11 | mut_r4_11_old_revision_commit.py | 1 | PASS | 1 | 0 | FAIL ADV-13 journal resol a lease més recent (B) | DETECTED |
| MUT-R4-12 | mut_r4_12_mid_backward_safe.py | 1 | PASS | 1 | 0 | FAIL REC-05 A renewal after expiry rejected | DETECTED |

## Nota MUT-R4-12 (investigació exigida per l'ordre)

El patró `if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):` apareix 2
cops a rc5_4_leases.py (renew, línia 201; release, línia 225). replace(...,1)
toca renew. Amb el mutant, renew no comprova l'estat -> pot renovar una lease
EXPIRED. Les dues suites fallen amb assertions normals: "FAIL REC-05 A renewal
after expiry rejected" (crash) i "FAIL ADV-03 renewal després d'expiry
rebutjada" (adv). Exit agregat 1 (no 2: el runner agrega exit no-zero a 1).
0 tracebacks. No és error d'arguments, import ni infraestructura: és una
fallada d'assertió normal del comportament de seguretat.
