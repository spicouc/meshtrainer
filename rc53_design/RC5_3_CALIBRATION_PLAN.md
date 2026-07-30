# RC5.3 Calibration

## Methods
- worker.calibration.start -> returns budget
- worker.calibration.result -> observed metrics

## Budget Fields
- observed_safe_budget_mb
- max_micro_batch
- max_sequence_length
- precision
- backend

Coordinator must not assign micro-units exceeding budget.
