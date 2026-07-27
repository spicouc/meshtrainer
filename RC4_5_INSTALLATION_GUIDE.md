# meshtrainer — Installation Guide v1.0

## 1. Requisits de maquinari

| Component | Minim | Recomanat |
|---|---|---|
| CPU | 4 nuclis x86_64 | 8+ nuclis |
| RAM | 8 GB | 16+ GB |
| Disc | 10 GB lliures | 50 GB SSD |
| Xarxa | Connectivitat LAN | Gigabit |

## 2. Sistemes suportats

- Linux Debian 12+ / Ubuntu 22.04+
- No suportat: Windows, macOS (el codi pot funcionar pero no testejat)

## 3. Dependencies

- Python 3.11+
- PyTorch 2.0+
- Transformers 4.40+
- PEFT 0.14+
- Node.js 18+ (per browser worker, opcional)
- Playwright (per tests E2E, opcional)

## 4. Instal·lacio pas a pas

```bash
# 1. Clonar el projecte
git clone <repo-url> meshtrainer
cd meshtrainer

# 2. Crear entorn Python
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# 3. Instal·lar dependencies
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install transformers peft datasets numpy
pip install pyyaml httpx  # per al runner

# 4. Descarregar model base
python3 -c "from transformers import AutoModel; AutoModel.from_pretrained('Qwen/Qwen3-0.6B')"

# 5. Configurar (veure Configuration Reference)
cp config.example.yaml config.yaml
# Editar config.yaml segons l'entorn

# 6. Verificar instal·lacio
python3 -c "import torch, transformers, peft; print(f'OK: torch={torch.__version__}, transformers={transformers.__version__}, peft={peft.__version__}')"

# 7. Provar el runner
python3 rc4_run.py config.yaml
```

## 5. Instal·lacio browser worker (opcional)

```bash
cd browser-worker
npm install
npx playwright install chromium
```

## 6. Verificacio final

```bash
# Executar tests basics
python3 -m pytest rc3_integration_tests.py -k "T1 or T2" --no-header -v

# Executar pipeline de prova
python3 rc4_run.py config.yaml
```

## 7. Resolucio de problemes

| Problema | Solucio |
|---|---|
| ModuleNotFoundError | `pip install <module>` |
| CUDA not available | El sistema funciona en CPU, ignorar |
| Model download fails | Verificar connexió a internet o usar snapshot local |
