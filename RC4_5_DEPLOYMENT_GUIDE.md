# meshtrainer — Deployment Guide v1.0

## Escenari 1: Una sola maquina (CPU)

| Requisit | Valor |
|---|---|
| CPU | 4 nuclis |
| RAM | 8 GB |
| Disc | 10 GB |

```bash
# Instal·lar
python3 -m venv venv
source venv/bin/activate
pip install torch transformers peft pyyaml httpx

# Executar
python3 rc4_run.py config.yaml
```

## Escenari 2: Una sola maquina (GPU)

| Requisit | Valor |
|---|---|
| CPU | 4 nuclis |
| RAM | 16 GB |
| GPU | 8 GB VRAM |
| Disc | 20 GB |

```bash
pip install torch torchvision  # versio CUDA
python3 rc4_run.py config.yaml
```

## Escenari 3: Multiple machines

| Maquina | Rol | Requisits |
|---|---|---|
| Maquina A | Coordinator + Worker 1 | 4 CPU, 8 GB RAM |
| Maquina B | Worker 2 | 4 CPU, 8 GB RAM |

El coordinator i workers es comuniquen per HTTP. El coordinator ha de ser accessible
des dels workers per xarxa.

## Escenari 4: Produccio

| Component | Especificacio |
|---|---|
| Servidor | 8+ CPU, 32 GB RAM, SSD NVMe |
| Xarxa | Gigabit, firewall configurat |
| Backup | TGFS o similar |
| Monitoritzacio | Logs estructurats |

## Requisits xarxa

| Port | Servei | Default |
|---|---|---|
| 8791 | Coordinator HTTP | Configurable |
| 5173 | Browser worker (dev) | Configurable |

## Checklist desplegament

- [ ] Python 3.11+ instal·lat
- [ ] Entorn virtual creat
- [ ] Dependencies instal·lades
- [ ] Model base descarregat
- [ ] Config.yaml editat
- [ ] Permisos d'escriptura al directori de sortida
- [ ] Ports necessaris oberts (si multi-maquina)
- [ ] Test basic executat
- [ ] Pipeline executat sense errors
