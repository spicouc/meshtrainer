# meshtrainer

**Qwen3 Distributed LoRA Trainer** — Plataforma d'entrenament federat LoRA per a Qwen3 amb agregacio FedAvg.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Que es?

`meshtrainer` es un sistema d'entrenament federat per a models de llenguatge Qwen3
usant LoRA (Low-Rank Adaptation). Permet entrenar un model de forma distribuita amb
multiple workers, agregar els resultats amb FedAvg i generar checkpoints reproduibles
amb verificacio criptografica.

## Requisits

- Linux, 4 CPU, 8 GB RAM, 10 GB disc
- Python 3.11+
- PyTorch 2.0+
- PEFT 0.14+

## Instal·lacio rapida

```bash
git clone https://github.com/spicouc/meshtrainer.git
cd meshtrainer
python3 -m venv venv
source venv/bin/activate
pip install torch transformers peft pyyaml httpx

# Descarregar model base
python3 -c "from transformers import AutoModel; AutoModel.from_pretrained('Qwen/Qwen3-0.6B')"

# Executar pipeline
cp config.example.yaml config.yaml
# Editar config.yaml amb el path al model
python3 rc4_run.py config.yaml
```

## Us basic

```bash
# Executar pipeline complet (2 workers, 2 rondes)
python3 rc4_run.py config.yaml

# Executar tests
python3 -m pytest rc3_integration_tests.py -v

# Tests de res iliencia
python3 -m pytest rc3_integration_tests.py -k "T4 or T11 or T12" -v
```

## Arquitectura

```
Worker 1 ──┐
           ├── Coordinator (JSON-RPC) ── FedAvg ── Checkpoint
Worker 2 ──┘
```

## Documentacio

Tota la documentacio esta al repositori en formato Markdown:

| Document | Descripcio |
|---|---|
| `RC4_5_INSTALLATION_GUIDE.md` | Instal·lacio pas a pas |
| `RC4_5_CONFIGURATION_REFERENCE.md` | Tots els parametres de configuracio |
| `RC4_5_OPERATIONS_MANUAL.md` | Inici, monitoritzacio, errors |
| `RC4_5_DEPLOYMENT_GUIDE.md` | Escenaris de desplegament |
| `CHANGELOG.md` | Historial de versions |
| `RELEASE_NOTES.md` | Notes de la versio 1.0 |

## Tests

El projecte inclou 40 tests d'integracio:

- T1-T12: Tests de coordinator i workers simulats
- T13-T25: Tests amb model real Qwen3-0.6B

Resultats validats: 40/40 PASS, 24/24 Vitest, 21/21 Playwright.

## Limitacions

- Execucio en CPU (GPU no testejada)
- Workers sequencials per RAM limitada (8GB)
- Sense resume automatic d'execucions

## Llicencia

MIT
