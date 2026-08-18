# RC5.3 Round Protocol

## Flow
1. Round.open(run_id, round_id)
2. Worker.calibration.start → result
3. Coordinator creates assignments based on budget
4. Each worker executes micro-units
5. Round.close: FedAvg over ACTIVE
6. Global adapter computed
7. Next round from global adapter

## Validation
- Calibration budget enforced
- Model hash verified
- Schema hash verified
- Stale adapter rejected
- Duplicate aggregation rejected
