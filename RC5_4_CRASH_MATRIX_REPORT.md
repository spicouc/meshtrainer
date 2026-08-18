# RC5.4 CRASH MATRIX REPORT

**Data**: 2026-08-05 | **Suite**: rc5_4_tests.py | **Resultat: 24/24 PASS, 0 FAIL**

## Metodologia

Mini-adaptador determinista (sense autograd): els deltas es regeneren amb seed
derivada de (run, round, assignment, unit, worker). La DB SQLite real es crea a
TMPDIR. Rellotge injectable (now fictici) per a proves de TTL deterministes.

## REC-01: crash abans de step.open
Espera: no hi ha lease; status(unit_key) is None. PASS.

## REC-02: crash després de step.open
Espera: lease ACTIVE persistida; retry reusa la mateixa lease. PASS.

## REC-03: crash després de server activation
Espera: lease ACTIVE, reutilitzable. PASS.

## REC-04: crash abans de backward
Espera: lease ACTIVE, retry segur. PASS.

## REC-05: crash a mig backward (el més crític)
Espera: la lease d'A s'expira, la unitat passa a EXPIRED, cap receipt ni
contribució d'A és acceptada, el Coordinator reassigna a B, B completa, i
només la contribució de B entra al FedAvg. PASS.

## REC-06: crash després de APPLIED
Espera: retry en APPLED retorna exactament el mateix delta; cap segon
optimizer.step. PASS.

## REC-07: crash després de SUBMITTED
Espera: retry retorna el mateix update_id. PASS.

## REC-08: crash després de COMMITTED
Espera: retry retorna el mateix receipt byte-for-byte. PASS.

## REC-09: crash després de checkpoint.upload
Espera: retry de checkpoint.upload retorna la mateixa resposta. PASS.

## REC-10: crash després d'ACTIVE
Espera: lease ACTIVE persistida; retry reusa la mateixa lease. PASS.

## REC-11: crash durant round.close
Espera: les leases de la ronda es marquen ABORTED i no bloquegen. PASS.

## REC-12: crash entre Round 1 i Round 2
Espera: les leases de la ronda nova són independents; no hi ha contaminació
entre rondes. PASS.

## Exactly-once addicional (REC-07b/08b/09b)
Un segon journal_set amb valors diferents NO sobreescriu update_id, receipt ni
checkpoint_response (primer valor guanya). PASS.

## REC-10b: unit_key retorna la lease més recent
Després de reassignar u10 a B, status(unit_key) retorna la lease de B
(worker_id == worker_b, ACTIVE). PASS.
