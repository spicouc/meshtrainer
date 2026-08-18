# TROUBLESHOOTING — MeshTrainer

Common problems and what to do. If you see a raw Python traceback in the UI,
that's a bug — please report it (the UI is designed to show friendly errors).

## Python

**"Python not found / incompatible"** — `install.sh` needs Python 3.11+:
```bash
python3 --version   # must be 3.11, 3.12 or 3.13
```
On Debian/Ubuntu: `sudo apt install python3 python3-venv python3-pip`.

## Install

**"pip install failed"** — check network access to PyPI, or retry (idempotent):
```bash
./install.sh
```

**Port already in use** — another process holds 8000:
```bash
./meshtrainer status   # is an old instance running? then: ./meshtrainer stop
# or change the port in ~/.config/meshtrainer/config.toml
```

## RAM / performance

**"Not enough available memory" warning before starting a job** — the wizard
checks your real RAM. Options:
- lower `concurrency` to 1
- choose a smaller model
- shorten `max sequence length`
- close other heavy applications

Qwen3-0.6B needs roughly ~2.6 GB per worker; MiniCPM5-1B ~4.4 GB per worker.

## Model missing

**"Model not available locally"** — the certified backends (Qwen3, MiniCPM5)
need their model snapshot on disk. MeshTrainer never downloads models
silently. Point it to your model directory:

```bash
export QWEN3_MODEL=/path/to/qwen3_0_6b_snapshot
export MINICPM5_MODEL=/path/to/minicpm5_1b_snapshot
./meshtrainer restart
```

or put the snapshots in `~/.cache/meshtrainer/models/`.

## Dataset invalid

The Datasets page shows grouped errors (JSON errors, missing fields, empty
samples, oversized samples). A JSONL line must be valid JSON with at least an
`instruction`/`response` (chat) pair. Example of a valid line:

```json
{"instruction": "Hola", "response": "Hola, què tal?"}
```

## API unavailable / UI shows "Connection lost"

The web UI reconnects automatically (SSE + REST resync). If it persists:

```bash
./meshtrainer status     # is the server running?
./meshtrainer logs       # last errors
./meshtrainer restart
```

## Training failed

Open the job monitor → **Logs** section. Common causes:
- out of memory (see RAM above)
- model missing (see above)
- dataset invalid (see above)

The job is marked `Failed` with a friendly reason — the raw log stays in
`app_storage/logs/job-<id>/` for diagnosis.

## Still stuck?

Open an issue with:
- output of `./meshtrainer status`
- the relevant part of `./meshtrainer logs`
- your Python version and OS
