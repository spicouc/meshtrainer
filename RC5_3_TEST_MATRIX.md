# RC5.3 Stage B R5 — Test Matrix

Total: 105/105 PASS (extret del log d'execució real, 0 duplicats)

| ID | Test | Categoria | Verifica |
|---|---|---|---|
| SM1 | server_model_v1_binding | Secció 1 | real server hash accepted |
| SM2 | server_model_v1_binding | Secció 1 | zero missing/unexpected (validation passes) |
| SM3 | server_model_v1_binding | Secció 1 | reconstruction deterministic |
| SM4 | server_model_v1_binding | Secció 1 | fake server hash rejected |
| SM5 | server_model_v1_binding | Secció 1 | altered server bytes rejected |
| SM6 | server_model_v1_binding | Secció 1 | different seed -> different artifact |
| SM7 | server_model_v1_binding | Secció 1 | different server models -> different activations |
| SM8 | server_model_v1_binding | Secció 1 | same bytes -> identical activations |
| SM9 | server_model_v1_binding | Secció 1 | step.open fake server_model_hash rejected |
| SM10 | server_model_v1_binding | Secció 1 | step.open real server_model_hash accepted |
| AT1 | atomic_registration | Secció 2 | valid model registered |
| AT2 | atomic_registration | Secció 2 | inconsistent adapter REJECTED |
| AT3 | atomic_registration | Secció 2 | original server_model_hash intact |
| AT4 | atomic_registration | Secció 2 | original adapter_0_hash intact |
| AT5 | atomic_registration | Secció 2 | original worker_model_hash intact |
| AT6 | atomic_registration | Secció 2 | no partial round_adapter row |
| AS1 | assignment_idempotency | Secció 3 | new assignment ACCEPTED |
| AS2 | assignment_idempotency | Secció 3 | same id + same payload -> same response |
| AS3 | assignment_idempotency | Secció 3 | changed shard REJECTED |
| AS4 | assignment_idempotency | Secció 3 | changed ETT REJECTED |
| AS5 | assignment_idempotency | Secció 3 | changed worker REJECTED |
| AS6 | assignment_idempotency | Secció 3 | changed hashes REJECTED |
| AS7 | assignment_idempotency | Secció 3 | request_sha + created_at persisted |
| CA1 | calibration_idempotency | Secció 4 | calibration accepted |
| CA2 | calibration_idempotency | Secció 4 | same key + same payload -> same response |
| CA3 | calibration_idempotency | Secció 4 | same nonce + different payload REJECTED |
| CA4 | calibration_idempotency | Secció 4 | no nonce / defaults REJECTED |
| CA5 | calibration_idempotency | Secció 4 | uncalibrated worker rejected |
| LN1 | round_lineage_explicit | Secció 5 | round 1 index |
| LN2 | round_lineage_explicit | Secció 5 | round 2 index |
| LN3 | round_lineage_explicit | Secció 5 | round 10 index (explicit parent) |
| LN4 | round_lineage_explicit | Secció 5 | missing parent rejected |
| LN5 | round_lineage_explicit | Secció 5 | non-CLOSED parent rejected |
| LN6 | round_lineage_explicit | Secció 5 | parent_round_id persisted |
| LN7 | round_lineage_explicit | Secció 5 | round2 wrong parent adapter REJECTED |
| LN8 | round_lineage_explicit | Secció 5 | parent_adapter_hash == parent global |
| SC1 | secure_contributions | Secció 6 | fake cid rejected |
| SC2 | secure_contributions | Secció 6 | real contribution accepted |
| SC3 | secure_contributions | Secció 6 | receipt mismatch rejected |
| SC4 | secure_contributions | Secció 6 | source_request_sha persisted |
| SC5 | secure_contributions | Secció 6 | same cid + same source -> same response |
| IW1 | adapter_worker_base_invariants | Secció 7 | worker A adapter == worker B adapter |
| IW2 | adapter_worker_base_invariants | Secció 7 | worker adapter == round.base_adapter_hash |
| IW3 | adapter_worker_base_invariants | Secció 7 | worker adapter == round.adapter_0_hash |
| IW4 | adapter_worker_base_invariants | Secció 7 | base_adapter_hash == adapter_0_hash |
| IW5 | adapter_worker_base_invariants | Secció 7 | worker A base == worker B base |
| IW6 | adapter_worker_base_invariants | Secció 7 | worker base invariant across rounds |
| IW7 | adapter_worker_base_invariants | Secció 7 | round2 base_adapter == parent global adapter |
| IW8 | adapter_worker_base_invariants | Secció 7 | round2 parent_adapter_hash == adapter_0 |
| IW9 | adapter_worker_base_invariants | Secció 7 | worker adapter != adapter_0 rejected |
| IW10 | adapter_worker_base_invariants | Secció 7 | different worker base between rounds rejected |
| IW11 | adapter_worker_base_invariants | Secció 7 | stale parent adapter rejected |
| IW12 | adapter_worker_base_invariants | Secció 7 | server model != registered rejected |
| OR1 | fedavg_oracle_independent | Secció 8 | exactly 2 ACTIVE contributions |
| OR2 | fedavg_oracle_independent | Secció 8 | all ETT > 0 |
| OR3 | fedavg_oracle_independent | Secció 8 | oracle adapter_1 == Coordinator adapter_1 |
| OR4 | fedavg_oracle_independent | Secció 8 | oracle order-invariant |
| OR5 | fedavg_oracle_independent | Secció 8 | weighted != simple average (ETT-dependent) |
| OR8 | fedavg_oracle_independent | Secció 8 | fedavg excludes SUPERSEDED |
| OR9 | fedavg_oracle_independent | Secció 8 | oracle adapter_2 == Coordinator adapter_2 |
| W1 | two_real_workers_full_flow | Secció 7 | two workers completed |
| W2 | two_real_workers_full_flow | Secció 7 | distinct ETT (real shards) |
| W3 | two_real_workers_full_flow | Secció 7 | distinct receipts |
| W4 | two_real_workers_full_flow | Secció 7 | distinct backward_ids |
| W5 | two_real_workers_full_flow | Secció 7 | real delta hashes |
| W6 | two_real_workers_full_flow | Secció 7 | ETT derived from labels |
| W7 | two_real_workers_full_flow | Secció 7 | quorum met |
| W8 | two_real_workers_full_flow | Secció 7 | worker A adapter hash == worker B |
| W9 | two_real_workers_full_flow | Secció 7 | worker adapter == round.base_adapter_hash |
| W10 | two_real_workers_full_flow | Secció 7 | worker adapter == round.adapter_0_hash |
| W11 | two_real_workers_full_flow | Secció 7 | round.base_adapter_hash == adapter_0_hash |
| S1 | shards_real_data | Secció 7 | token content differs |
| S2 | shards_real_data | Secció 7 | label content differs |
| S3 | shards_real_data | Secció 7 | ETT A |
| S4 | shards_real_data | Secció 7 | ETT B |
| F1 | fedavg_ett_weighted_real | Secció 8 | FedAvg A == offline oracle |
| F2 | fedavg_ett_weighted_real | Secció 8 | deltas are REAL (non-zero) |
| F3 | fedavg_ett_weighted_real | Secció 8 | FedAvg order-invariant |
| R1 | round2_lineage_real | Secció 5 | round1 closed |
| R2 | round2_lineage_real | Secció 5 | round2 adapter == adapter1 + delta2 |
| R3 | round2_lineage_real | Secció 5 | stale adapter rejected |
| R4 | round2_lineage_real | Secció 5 | adapter1 != adapter0 (real training) |
| R5 | round2_lineage_real | Secció 5 | wrong worker_model_hash rejected |
| R6 | round2_lineage_real | Secció 5 | duplicate worker assignment rejected |
| R7 | round2_lineage_real | Secció 5 | worker base invariant across rounds |
| R8 | round2_lineage_real | Secció 5 | different worker base in round 2 rejected |
| BE1 | budget_enforced | Secció 4 | over-budget assignment rejected |
| BE2 | budget_enforced | Secció 4 | no partial over-budget assignment persisted |
| RV1 | revisions_real | Secció 6 | rev1 ACTIVE |
| RV2 | revisions_real | Secció 6 | rev2 ACTIVE after full flow |
| RV4 | revisions_real | Secció 6 | rev1 SUPERSEDED after rev2 ACTIVE |
| RV5 | revisions_real | Secció 6 | only rev2 ACTIVE |
| RV6 | revisions_real | Secció 6 | FedAvg excludes SUPERSEDED |
| Q1 | quorum_exact_set | Secció 8 | quorum not met with 1/2 |
| Q2 | quorum_exact_set | Secció 8 | close before quorum rejected |
| ST1 | state_machine_real | Secció 2 | calibration before OPEN rejected |
| ST2 | state_machine_real | Secció 2 | assignment before calibration rejected |
| P1 | process_restart_real | Secció 1 | worker A process OK (COMMITTED) |
| P2 | process_restart_real | Secció 1 | worker B process OK (after restart) |
| P3 | process_restart_real | Secció 1 | receipt recovered byte-for-byte |
| P4 | process_restart_real | Secció 1 | upload idempotent after restart |
| P5 | process_restart_real | Secció 1 | round closed after restart |
| P6 | process_restart_real | Secció 1 | restart adapter == no-restart adapter |
| I1 | isolation_real | Secció 6 | alien backward_id rejected |
| I2 | isolation_real | Secció 6 | alien contribution rejected |

Suma: 10+6+7+5+8+5+12+7+11+4+3+8+2+5+2+2+6+2 = 105

Adversarial: 28 proves ADV-R3-01..28 + 5 sub-checks (15b/16a/16b/16c/20b/25b) = 33/33 PASS
