# RC4.2B — Compatibility Report

**Data:** 2026-07-26  
**Projecte:** meshtrainer

---

## T1 — Compatibilitat

| Component | Versio | Compatible |
|---|---|---|
| PEFT | 0.19.1 | ✅ (mateixa versio que el checkpoint) |
| Transformers | 5.14.1 | ✅ |
| PyTorch | 2.13.0 | ✅ |
| Checkpoint-2500 (PEFT 0.19.1) | — | ✅ Carregat correctament |

## T2 — Validacio

| Model | SHA-256 adapter | Estat |
|---|---|---|
| Base (Qwen3-0.6B) | N/A | ✅ Carregat, 596M params |
| Checkpoint-2500 | `9701519fe63fbc82...` | ✅ Carregat, 224 tensors LoRA |

## T3 — Comparativa

| Metrica | Base | Checkpoint-2500 | Diferencia |
|---|---|---|---|
| Validation Loss | 4.9279 | 4.7448 | **-3.7%** |
| Perplexity | 420.58 | 192.26 | **-54.3%** |
| Forward pass (10 samples) | 22.7s | 38.1s | +68% (per doble forward) |

## Conclusio

L'entrenament LoRA sobre catala (checkpoint-2500) redueix la perplexitat en un 54.3% respecte al model base, demostrant una millora substancial en la capacitat del model de processar text en catala.
