# RC5.3 ADR — Multi-Worker Distributed Round

**Status:** Design Candidate
**Base:** RC5.2 Phase 2 (commit 22d3d30)

## Topology
- 2 workers, each with own shard
- Coordinator assigns micro-units per calibration budget
- Each worker produces real LoRA deltas

## FedAvg
- Only ACTIVE contributions
- A and B aggregated separately
- Weighted by ETT
- Order-invariant

## Round Lifecycle
1. Open round
2. Assign workers
3. Each worker: step.open → forward → backward → update → commit → upload
4. Round.close: aggregate, create global adapter
5. Round 2 from global adapter

## Mutation Plan (RC5.3)
- Include SUPERSEDED in FedAvg
- Ignore ETT
- Lose one ACTIVE contribution
- Double-aggregate
- Stale base adapter
- Ignore model hash
- Ignore calibration budget
- Round 2 uses old adapter
