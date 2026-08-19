# QUICKSTART — MeshTrainer in 5 minutes

MeshTrainer is a local, installable distributed fine-tuning tool with a web UI.
This guide takes you from clone to your first training run. Training time
depends on your hardware — MeshTrainer won't promise you a fixed duration.

## Requirements

- Linux (or macOS/WSL) with Python 3.11+
- ~4 GB free RAM for Qwen3-0.6B (more for MiniCPM5-1B)
- ~10 GB free disk
- **A local model snapshot** (see "Models" below) — MeshTrainer never
  downloads models automatically.

## 1. Install

```bash
git clone https://github.com/spicouc/meshtrainer.git
cd meshtrainer
./install.sh
```

`install.sh` creates a local `.venv`, installs the app requirements plus the
training dependencies (torch/transformers/peft via `requirements-training.txt`),
validates imports and creates runtime folders. It never downloads models,
never touches your firewall, never needs root.

```
MeshTrainer installed successfully.
```

## 2. Start

```bash
./meshtrainer start
```

You'll see `MeshTrainer en marxa: http://127.0.0.1:8000`.

## 3. Open the browser

Go to **http://127.0.0.1:8000**

First run shows the **First Run Setup** (System Check → Storage → Model
availability → Recommended profile → Finish). It reads your real hardware and
suggests a safe training configuration.

## 4. Models

The certified backends (Qwen3-0.6B, MiniCPM5-1B) need their model snapshot
available locally. Point MeshTrainer to it in any of these ways:

```bash
# 1. environment variables
export QWEN3_MODEL=/path/to/qwen3_0_6b_snapshot
export MINICPM5_MODEL=/path/to/minicpm5_1b_snapshot

# 2. or put snapshots in the models directory (default)
#    ~/.cache/meshtrainer/models/
```

or set `models_dir` in `~/.config/meshtrainer/config.toml`.

## 5. Train

1. **Datasets** → add a local JSONL file (or drag & drop one)
2. **New Training Job** → pick a model (Qwen3 or MiniCPM5) → pick your dataset
   → keep the recommended (safe) configuration → **Start**
3. Watch the job in real time (SSE): rounds, workers, loss, timeline
4. When it finishes: **Training completed** — download the final adapter and
   the training summary

## Commands

| Command | What it does |
|---|---|
| `./meshtrainer start` | Start the web UI |
| `./meshtrainer status` | Is it running? |
| `./meshtrainer logs` | Show recent logs |
| `./meshtrainer stop` | Stop it cleanly |
| `./install.sh` | Install/repair the environment (idempotent) |

## Configuration (optional)

`~/.config/meshtrainer/config.toml`:

```toml
host = "127.0.0.1"      # local-only by default
port = 8000
# storage_dir = "/path/to/storage"
# models_dir = "/path/to/models"
# environment = "production"
```

Never expose the server on `0.0.0.0` unless you trust the network — the API
has no built-in authentication.

## Troubleshooting?

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
