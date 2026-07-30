# RC5.3 Risk Register

| Risk | Mitigation |
|------|-----------|
| Worker OOM | Calibration budget enforced |
| Asymmetric shards | ETT-weighted FedAvg |
| Network delay | Timeouts, retries |
| Adapter state divergence | Hash verification each step |
| Duplicate aggregation | Idempotency keys |
