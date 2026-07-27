# meshtrainer — Final Audit Report v1.0

## 1. Resum executiu

meshtrainer es una plataforma d'entrenament federat LoRA per a Qwen3 que
implementa un pipeline complet: carrega de model, entrenament distribuit,
agregacio FedAvg, generacio de checkpoint, validacio, empaquetat i backup.

## 2. Arquitectura

- Coordinator (JSON-RPC over HTTP)
- Workers (simulats o reals amb PyTorch + PEFT)
- FedAvg ponderat per samples
- Checkpoints amb SHA-256
- Pipeline automatitzat (rc4_run.py)

## 3. Decisions tecniques

- Pytorch 2.13+ en CPU (GPU no testejada pero compatible)
- PEFT 0.19+ per LoRA
- SQLite per estat del coordinator
- HTTP per comunicacio coordinator-worker
- SyntheticTensorStore per proves sense model real

## 4. Limitacions

- CPU-only (no GPU testejada)
- Workers sequencials per RAM limitada (8GB)
- Sense resume automatic d'execucions
- No dashboard web de monitoritzacio

## 5. Riscos residuals

| Risc | Probabilitat | Impacte | Mitigacio |
|---|---|---|---|
| OOM amb 2+ workers paral·lels | Mitjana | Alt | Execucio sequencial |
| Pèrdua de checkpoint migracio | Baixa | Alt | Backup TGFS |
| Incompatibilitat PEFT futura | Baixa | Mig | Congelar versions |

## 6. Recomanacions futures (v1.1)

- Resume automatic d'execucions interrompudes
- Dashboard de monitoritzacio en temps real
- Suport per GPU
- Workers paral·lels en multiple maquina
- Tests E2E amb Playwright en CI
