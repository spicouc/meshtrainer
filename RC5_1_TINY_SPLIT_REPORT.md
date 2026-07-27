# RC5.1 — Tiny Split PoC Report

**Data:** 2026-07-27
**Projecte:** meshtrainer v1.1
**Branca:** rc5.1-tiny-split-poc
**Resultat:** T-SPLIT 26/26 PASS

---

## Implementacio

| Component | Fitxer | Estat |
|---|---|---|
| Receipts + HMAC | rc5_receipt.py | ✅ |
| Split Server (HTTP + tiny model) | rc5_split_server.py | ✅ |
| Coordinator extensions (worker.join, work.request, ledger, FedAvg) | rc5_coordinator_ext.py | ✅ |
| Tests T-SPLIT | rc5_tests.py | ✅ |

## Tests

| Test | Resultat | Detall |
|---|---|---|
| T-SPLIT-1 | 22/22 PASS | Worker E2E: join, request, accept, 5 steps, receipt, contribution, round close |
| T-SPLIT-2 | 4/4 PASS | Cumulative contribution, SUPERSEDED, FedAvg sobre ACTIVE nomes |
| **Total** | **26/26 PASS** | |

## Flux validat (T-SPLIT-1)

1. Worker join -> Coordinator (worker.join)
2. Work request -> Coordinator (work.request)
3. Work accept -> Coordinator (work.accept)
4. Step open -> Split Server (step.open)
5. Send text, receive embeddings -> Split Server (step.embedding)
6. Forward complete, send activation -> Split Server (step.cut_activation)
7. Backward, send gradient -> Split Server (step.cut_gradient)
8. Both commit -> Split Server (step.commit)
9. Receipt generated with HMAC, verified (step.commit returns receipt)
10. Contribution upload -> Coordinator (checkpoint.upload)
11. Round close -> FedAvg over ledger (admin.round.close)

## Receipt model

22 camps, HMAC-SHA256, canonical JSON, nonce + key_id, expiracio.
Todos els receipts verificats correctament.

## Backpressure

Step.open retorna error -32001 si el worker excedeix max ACTIVE per worker (1).
