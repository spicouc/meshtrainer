# PORTABILITY_REPORT — MiniCPM5 Backend Portability Pilot

**Data:** 2026-08-14
**Executor:** DinDjarin (pve/Mandalor)
**Base certificada:** b146723af546b6bff73d4e0282cb4cb010f6d544 (GENERIC DISTRIBUTED CORE: CLOSED)
**Model:** openbmb/MiniCPM5-1B, revisió `4e9de7a0778dc1c362e983e6858f0e77542cbdca`
**Arquitectura:** LlamaForCausalLM (hidden 1536, 24 capes, 2 KV heads, vocab 130560, bfloat16 nadiu → carregat float32 a CPU)

---

## 1. CRITERI CLAU DE PORTABILITAT (punt 20)

Per passar de Qwen a MiniCPM5 **NO s'ha modificat cap component del core**:

| Component | Canvis |
|---|---|
| ModelRoundCoordinator | **0** |
| Training HTTP server | **0** |
| model_worker orchestration | **0** (només +2 línies: registre lazy + path per defecte) |
| FedAvg | **0** |
| Leases | **0** |
| Recovery | **0** |
| Admin boundary | **0** |
| Contribution lifecycle | **0** |
| Protocol | **0** |

## 2. REGISTRY (punt 5)

```
BACKENDS = {
    "dummy":    ("backends.dummy_backend",    "DummyBackend"),
    "qwen3":    ("backends.qwen3_backend",    "Qwen3Backend"),
+   "minicpm5": ("backends.minicpm5_backend", "MiniCPM5Backend"),   <- EXPECTED / ALLOWED
}
```

**Registry integration: + "minicpm5" entry — EXPECTED / ALLOWED.**

## 3. FITXERS NOUS DEL BACKEND

- `backends/minicpm5_backend.py` — plugin complet del contracte TrainingBackend
- `minicpm5_isolation_tests.py` — ISO-M1..M4
- `minicpm5_adapter_adversarial_tests.py` — ADV-A01..A07
- `minicpm5_adversarial_tests.py` — MADV-01..16

## 4. CANVIS ADDITIUS ALS DRIVERS DE TEST (no al core)

- `generic_distributed_run.py`: +branques `minicpm5` per `--model`/`--data-dir`/`choices` (35 línies additives)
- `generic_recovery_tests.py`: +branca `minicpm5` i `_model_path_for()` (26 línies additives)
- `model_worker.py`: +registre lazy `minicpm5` + path per defecte (4 línies)

Cap lògica existent modificada — només extensions condicionals.

## 5. RESULTATS DEL PILOT (execucions reals)

| Test | Resultat |
|---|---|
| ISO-M1..M4 backend isolation | **10/10 PASS** |
| ADV-A01..A07 adapter adversarials | **10/10 PASS** |
| Single-worker (loss finit, delta≠0, ETT>0, post≠pre) | PASS |
| Exactly-once (mateix delta; request_sha diferent REJECTED) | PASS |
| Recovery backend (pre-hash reprodueix, maxdiff 0.0) | PASS |
| Distributed Round 1 (workers A/B reals) | **19/19 PASS** |
| FedAvg R1 pel Coordinator | PASS (adapter_1 sha=3e0fc536, ETT=172) |
| Oracle R1 (tensor per tensor) | **maxdiff=0.00e+00** |
| Round 2 (adapter_1 baseline, workers A2/B2) | PASS |
| Oracle R2 | **maxdiff=0.00e+00** |
| MREC recovery (SIGKILL real, PIDs 33701→33735) | **11/11 PASS** |
| no-crash == recovered | **hash EXACTE 5d7f4299** |
| MADV-01..16 | **19/19 PASS** |

## 6. IDENTITAT DEL MODEL (punt 7)

```
backend_id:              minicpm5
model_id:                openbmb/MiniCPM5-1B
model_revision:          4e9de7a0... (pinnejada)
config_sha:              hash real del config.json
weight_manifest_sha:     hash real del safetensors
base_model_hash:         63d736ad11ad8f7e... (estable entre càrregues)
tokenizer_identity:      hash del tokenizer.json
trainable:               11.2M / 1.09B = 1.03%
optimizer:               AdamW (lr 1e-4)
dtype:                   float32 (CPU)
```

El Coordinator treballa només amb `base_model_hash` — no entén MiniCPM5.

## 7. LORA (punt 8)

```
r: 16, alpha: 32, dropout: 0.05
target_modules: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj
  (esquema Llama — NO reutilitzats els de Qwen; detectats del config)
bias: none, task_type: CAUSAL_LM
```

## 8. CONCLUSIÓ

**MULTI-MODEL PORTABILITY: PROVEN** — el core genèric va executar MiniCPM5
(Round 1 + Round 2 + FedAvg + oracle + recovery) sense cap modificació
funcional. El canvi es limita al plugin del backend + registre lazy + drivers
de test additius.

---

## 11. ADDENDUM R1.2 — SIMULTANEÏTAT REAL (2026-08-16)

### Canvis al DRIVER de test (no al core, no al backend)

| Canvi | Fitxer | Naturalesa |
|---|---|---|
| `run_workers_overlap()` — spawn A → monitoritzar stdout → a LEASE d'A spawn B (A.poll() is None) | generic_distributed_run.py | test-harness |
| PAR-01..04 (4/4): A_LEASE < B_LEASE < A_RELEASE (R1 i R2) amb timestamps explícits | generic_distributed_run.py | test-harness |
| PAR N/A per dummy (train_step instantani <1 ms — overlap físicament impossible; es verifica completesa) | generic_distributed_run.py | test-harness |
| fadvise(DONTNEED) + drop_caches del page cache dels safetensors (retorna 2.1 GB al kernel) | generic_distributed_run.py | test-harness |
| `RUN_OOM_DIAGNOSTIC.sh` + `RUN_R12_RESOURCE_MONITOR.sh` | root | scripts de diagnòstic |
| `OOM_DIAGNOSTIC.log`, `SWAP_R12_BEFORE.log`, `R12_RESOURCE_RUN.log` | evidence/minicpm5/ | evidències |

### Resultats reals (execució final)

- **PAR-01..04: 4/4 PASS** — A_LEASE=3680.1 < B_LEASE=3701.2 < A_RELEASE=3841.9 (overlap 140.7 s); A2_LEASE=3909.7 < B2_LEASE=3934.2 < A2_RELEASE=4069.8 (overlap 135.6 s)
- **PIDs**: A=46055 B=46090 A2=46142 B2=46180 (tots diferents, subprocessos reals)
- **Distributed R1.2: 25/25 PASS** · FedAvg R1 adapter_1 c57269df · oracle R1 maxdiff 0.0 · FedAvg R2 adapter_2 22a94a5c · oracle R2 maxdiff 0.0
- **Gate complet: 16/16 PASS, GATE_EXIT=0** · CE A/B PASS amb artefact integrity
- **Swap temporal CAS B**: +8 GB swapfile (UUID 8fa8b168) → peaks: RAM host 14.4 GB, swap host 13.5/20.5 GB, CT memory.current 12.4/16 GB, CT memory.swap.current 6136/6442 MB (95%, no superat); swapfile desactivat després (config original restaurada)
- memory.swap.max CT112 = 6 GB **intacte** · f32/model/dataset/seq_len/LoRA **intactes** · Core b146723 **inalterat**
