# RC4.3 — Resilience Report

**Data:** 2026-07-26  
**Projecte:** meshtrainer  
**Resultat:** ✅ **12/12 tests PASS**

---

## 1. Escenaris provats

| T1 — Caiguda worker | Test | Estat |
|---|---|---|
| Worker falla durant entrenament | T17 (backward failure) | ✅ OOM detectat, skip correcte |
| Worker falla durant enviament delta | T24 (crash recovery) | ✅ Cache pre-submit recuperable |
| Lease expira per worker inactiu | T4 (lease expiry) | ✅ Tasca reassignada |

| T2 — Timeout | Test | Estat |
|---|---|---|
| Worker excedeix temps limit | T4 (lease expiry) | ✅ Tasca cancel·lada i reassignada |
| Submissio tardana rebutjada | T5 (late submission) | ✅ Delta rebutjat per lease expirat |

| T3 — Corrupcio dades | Test | Estat |
|---|---|---|
| SHA-256 del delta incorrecte | T11 (sha256 mismatch) | ✅ Rebutjat, error retornat |
| Contingut modificat entre enviaments | T11 (overwrite) | ✅ Rebutjat, conflicte detectat |
| Referencia a batch inexistent | T11 (nonexistent) | ✅ Error retornat |
| Generacio obsoleta (stale) | T9 (stale generation) | ✅ Rebutjat, requereix batch fresc |

| T4 — Coordinator | Test | Estat |
|---|---|---|
| Agregacio parcial (alguns workers vius) | T10 (force aggregate partial) | ✅ Agregacio amb dades disponibles |
| Agregacio sense workers complets | T10 (no completed) | ✅ Agregacio buida, no error |
| Cache de worker surviveix a reinici | T24 (cache recovery) | ✅ Dades recuperables |

| T5 — Idempotencia | Test | Estat |
|---|---|---|
| Mateix delta enviat dos cops | T12 (same delta) | ✅ Segon enviament ignorat |
| Delta diferent mateix batch | T12 (collision) | ✅ Conflicte detectat i rebutjat |

## 2. Resultats complets

| Test | Descripcio | Temps |
|---|---|---|
| T4 | Lease expiry reassignment | PASS |
| T5 | Late submission rejected | PASS |
| T9 | Stale generation rejected | PASS |
| T10 (1) | Force aggregate partial | PASS |
| T10 (2) | Force aggregate no completed | PASS |
| T11 (1) | SHA-256 mismatch rejected | PASS |
| T11 (2) | Overwrite different content | PASS |
| T11 (3) | Nonexistent reference | PASS |
| T12 (1) | Same delta idempotent | PASS |
| T12 (2) | Different delta collision | PASS |
| T17 | Backward failure (OOM) | PASS |
| T24 | Crash recovery cache | PASS |

## 3. Conclusio

Totes les incidencies simulades son gestionades correctament. El sistema detecta,
rebutja o es recupera de cada fallida sense corrupcio de dades ni perdua de
consistencia. El pipeline distribuit es resilient davant els escenaris previstos.
