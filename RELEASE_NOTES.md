# meshtrainer v1.0.0 — Release Notes

**Data:** 2026-07-26  
**Projecte:** meshtrainer — Qwen3 Distributed LoRA Trainer  

## Resum

meshtrainer v1.0 es una plataforma d'entrenament federat LoRA per a Qwen3,
completament funcional, reproduible, auditada, documentada i preparada per al
seu desplegament i evolucio futura.

## Novetats

- Pipeline complet automatitzat (una unica ordre)
- Entrenament federat amb FedAvg
- Workers reals amb LoRA (PEFT)
- Tests d'integracio (40 tests, inclosos 18 amb model real)
- Escalabilitat provada fins a 8 workers
- Resiliencia testejada (13 escenaris de fallida)
- Qualitat validada (-54.3% perplexity vs model base)
- Documentacio completa d'instal·lacio, operacio i desplegament

## Requisits

- Linux, 4 CPU, 8 GB RAM, 10 GB disc
- Python 3.11+, PyTorch 2.0+, PEFT 0.14+

## Instal·lacio

```bash
git clone <url> meshtrainer
cd meshtrainer
python3 -m venv venv
source venv/bin/activate
pip install torch transformers peft pyyaml httpx
python3 rc4_run.py config.yaml
```

## Limitacions

- Execucio en CPU (GPU no testejada)
- Workers sequencials per limitacio de RAM
- Sense resume automatic d'execucions interrompudes

## Links

- TGFSbackup: canal -1003770431908
