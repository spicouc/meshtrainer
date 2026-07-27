# RC4 Production Plan

**Objectiu:** Preparar l'estructura documental per a la versio 1.0

---

## 1. Arquitectura definitiva

```
+------------------+     +------------------+     +------------------+
|   Worker 1       |     |   Worker 2       |     |   Worker N       |
| Qwen3 + LoRA     |     | Qwen3 + LoRA     |     | Qwen3 + LoRA     |
| train_local()    |     | train_local()    |     | train_local()    |
| compute_delta()  |     | compute_delta()  |     | compute_delta()  |
+--------+---------+     +--------+---------+     +--------+---------+
         |                         |                         |
         |  HTTP RPC               |  HTTP RPC               |  HTTP RPC
         |  submit_delta           |  submit_delta           |  submit_delta
         v                         v                         v
+------------------------------------------------------------------+
|                     Coordinator                                   |
|  JSON-RPC over HTTP                Port 8791                      |
|  FedAvgLoRA.aggregate()                                            |
|  checkpoint_hash()                                                  |
|  admin endpoints                                                     |
+------------------------------------------------------------------+
         |
         v
+------------------+
|   Checkpoint     |
| adapter_config   |
| adapter_model    |
| manifest         |
| metrics          |
+------------------+
```

## 2. Requisits minims

| Component | Minim | Recomanat |
|---|---|---|
| CPU | 4 nuclis | 8+ nuclis |
| RAM | 8 GB | 16+ GB |
| Disc | 10 GB lliures | 50 GB+ SSD |
| Sistema | Linux (Debian/Ubuntu) | Debian 13+ |
| Python | 3.11+ | 3.13+ |
| PyTorch | 2.0+ | 2.13+ |
| Node.js | 18+ | 20+ (per browser worker) |

## 3. Instal·lacio

```bash
# Clonar repositori
git clone <url> meshtrainer
cd meshtrainer

# Crear entorn Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Descarregar model base
python3 -c "from transformers import AutoModel; AutoModel.from_pretrained('Qwen/Qwen3-0.6B')"

# Instal·lar browser worker
cd browser-worker
npm install
npx playwright install chromium
cd ..
```

## 4. Configuracio

Fitxer `config.yaml`:
```yaml
coordinator:
  port: 8791
  db_path: ./coordinator.db
  admin: true

worker:
  model_path: ./models/qwen3_0_6b
  device: cpu
  seed: 42
  lora:
    r: 8
    alpha: 16
    dropout: 0.1
    target_modules: [q_proj, k_proj, v_proj, o_proj]

training:
  batch_size: 1
  grad_accumulation_steps: 1
  max_length: 512
  learning_rate: 2e-4
```

## 5. Desplegament

1. Instal·lar dependències
2. Configurar entorn (config.yaml)
3. Iniciar coordinator: `python3 rc3_coordinator.py --port 8791 --db ./coordinator.db --admin`
4. Iniciar workers: `python3 rc3_worker_real.py --coordinator http://localhost:8791`
5. Verificar estat: `curl http://localhost:8791/health`
6. Iniciar browser worker: `cd browser-worker && npx vite`

## 6. Manteniment

- Logs del coordinator: stdout amb nivell INFO
- Logs dels workers: stdout per proces
- Base de dades: SQLite, backup amb `cp coordinator.db coordinator.db.bak`
- Checkpoints: a `./checkpoints/` per ronda

## 7. Actualitzacions

- Backup de la base de dades abans d'actualitzar
- Backup dels checkpoints actius
- Aturar coordinator i workers
- Actualitzar codi
- Revisar canvis al manifest
- Re-iniciar coordinator i workers
- Verificar integritat dels checkpoints

## 8. Recuperacio davant desastres

1. Restaurar base de dades des de backup
2. Restaurar checkpoint mes recent
3. Re-iniciar coordinator amb `--admin`
4. Verificar SHA-256 dels checkpoints
5. Re-iniciar workers
6. Si el checkpoint es valid, es pot reprendre des de la ronda anterior
