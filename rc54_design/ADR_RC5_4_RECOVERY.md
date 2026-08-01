# RC5.4 ADR — Safe Recovery (Stage A: documentation only)

**Status:** Design Candidate — implementation NOT authorized
**Base:** RC5.3 Stage B R2

## Scope
Recovery WITHOUT autograd graph persistence. No mid-backward recovery.

## Safe Recovery Boundaries
- Between steps (after COMMITTED): fully recoverable (unit state + receipt + delta persisted).
- After step.commit: receipt byte-identical on retry; checkpoint idempotent by receipt_id+nonce.
- Worker journal: PREPARED/APPLIED/SUBMITTED/COMMITTED persisted in SQLite;
  APPLIED returns the same bundle on retry, no second optimizer.step.
- Round adapters: global_adapter stored per round; Round N+1 resumes from it.

## Leases
- Units acquire a lease at step.open (session_id + issued_at).
- Lease expiry: unit state -> EXPIRED after TTL; no partial write allowed.
- Renewal: explicit; a crashed worker's lease expires, coordinator reassigns.

## ABORTED / EXPIRED
- ABORTED: explicit abort; unit + nonce released.
- EXPIRED: lease TTL; contribution never accepted; no double aggregation.

## What can be recovered without autograd graph
- Server activations: persisted per unit (server_activation_b64).
- Cut gradients: persisted per unit (cut_gradient).
- Loss/ETT: persisted.
- Delta bundles: persisted in worker journal.
- Receipts: persisted; replay-safe.
- Global adapters: persisted per round.

## Test Matrix (Stage B+)
- crash before step.open -> clean retry
- crash after step.open -> lease expiry, reassign
- crash after commit -> receipt replay identical
- crash after upload -> checkpoint retry idempotent
- crash mid-backward -> NOT recoverable (documented), unit EXPIRED, no partial contribution

## Mutation Plan
- ignore lease expiry
- accept contribution after EXPIRED
- double-recover APPLIED
- recover with stale adapter
