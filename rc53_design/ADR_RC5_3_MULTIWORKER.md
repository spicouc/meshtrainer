# RC5.3 ADR — Multi-Worker Distributed Round (Stage A Contract)

**Status:** Contract frozen
**Base:** RC5.2 Phase 2 R9 (RC5.2 tag meshtrainer-v1.1-rc5.2)
**Topology:** 1 Coordinator, 1 Split Server HTTP, 2 independent workers, distinct shards

## New Methods
- round.open
- worker.calibration.submit
- round.assignments.list
- round.status
- round.close

## Round States
DRAFT → OPEN → CALIBRATING → ASSIGNED → RUNNING → READY_TO_CLOSE → AGGREGATING → CLOSED
Terminals: CLOSED, ABORTED, EXPIRED

## Assignments
assignment_id, worker_id, shard_id, base_adapter_hash, worker_model_hash,
numerical_profile_hash, max_micro_batch, max_sequence_length, ett_target, status

## Calibration
backend, precision, available_memory_mb, observed_safe_budget_mb,
max_micro_batch, max_sequence_length, calibration_nonce, measured_at
Coordinator never assigns above min(profile limit, calibration, round policy).

## Round Close
Requires all mandatory assignments ACTIVE. Idempotent: same round + same contributions
→ same adapter + same hash. Different contributions after close → REJECTED.

## Stage B (authorized after Phase 2 R9 gate)
Two real workers, concurrent ThreadPoolExecutor over real HTTP, isolation checks,
ETT-weighted FedAvg order-invariant, revisions ACTIVE/SUPERSEDED, Round 2 from
global adapter, restart persistence, 12 RC5.3 mutants.
