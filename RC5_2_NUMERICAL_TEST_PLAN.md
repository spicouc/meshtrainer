# RC5.2 Test Plan (R4)

## Numerical Tests (N-SPLIT-01..16)
As R3 — unchanged.

## Additional Tests (R4-10)

| Test | Expected |
|---|---|
| duplicate forward_id + same payload | cached response |
| duplicate forward_id + different payload | REJECTED |
| duplicate update_id + same bundle | same delta_id |
| duplicate update_id + different bundle | REJECTED |
| committed → abort | REJECTED |
| duplicate tensor name in bundle | REJECTED |
| missing LoRA tensor in bundle | REJECTED |
| unknown LoRA tensor in bundle | REJECTED |
| trailing bytes in bundle | REJECTED |
| wrong numerical_profile_hash | REJECTED |
| wrong partition_schema_hash | REJECTED |
| wrong worker_model_hash | REJECTED |

## Delta Contract Tests

| Test | Expected |
|---|---|
| delta_bundle format OK | ACCEPTED |
| delta_bundle SHA mismatch | REJECTED |
| wrong adapter_schema_hash | REJECTED |
| bundle with wrong num tensors | REJECTED |
| delta = zeros (no training) | ACCEPTED (valid delta) |

## Mutation Tests (MUT-N1..6)
As R3 — unchanged.

## Regression (76 + 5)
As R3 — unchanged.
