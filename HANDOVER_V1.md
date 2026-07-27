# meshtrainer — Handover Document v1.0

**Data:** 2026-07-26
**Versio:** 1.0.0

---

## Resum executiu

meshtrainer v1.0 es una plataforma d'entrenament federat LoRA per a Qwen3,
completament funcional, reproduible, auditada, documentada i preparada per al
seu desplegament i evolucio futura.

## Arquitectura

Coordinator (JSON-RPC) + Workers (PyTorch/PEFT) + FedAvg + SHA-256 manifests.

## Decisions tecniques clau

- Pytorch 2.13+CPU (GPU compatible but no testejat)
- PEFT 0.19+ per LoRA
- SQLite per estat del coordinator
- Workers sintetics per proves escalables
- Pipeline automatitzat amb rc4_run.py

## Limitacions conegudes

- CPU nomes (GPU no testejada)
- Workers sequencials per RAM limitada (8GB)
- Sense resume automatic
- Sense dashboard web

## Riscos residuals

Veure FINAL_AUDIT_REPORT.md seccio 5.

## Recomanacions per a v1.1

1. Resume automatic d'execucions
2. Dashboard web
3. Suport GPU
4. Workers en multiple maquina
5. CI/CD amb tests automatitzats

## Roadmap v1.1

- Q1 2026: Resume + Dashboard
- Q2 2026: GPU + Multi-machine
- Q3 2026: Produccio

## Links

- TGFSbackup: canal -1003770431908, messages 10825-10853
- Release tarball: /tmp/meshtrainer_v1.0_host.tar.gz
- SHA-256: bf816ae02691b1489963bdd45264fb584412e34534f7427201a1ba4703c7e372
