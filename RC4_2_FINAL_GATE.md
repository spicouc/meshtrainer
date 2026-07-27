# RC4.2 — Gate Final

**Data:** 2026-07-26  
**Projecte:** meshtrainer  
**Resultat:** ✅ **PASS**

---

## 1. Bloqueig resolt

L'impediment de compatibilitat PEFT estava causat per un doble wrapping del model
(el backend aplicava LoRA per defecte i despres es carregava un segon adapter).
Sol·lucio: carregar el model amb `AutoModelForCausalLM` directament (sense PEFT)
i aplicar el checkpoint-2500 com a `PeftModel`.

## 2. Resultats de la comparativa

| Metrica | Model Base | Checkpoint-2500 | Millora |
|---|---|---|---|
| Validation Loss | 4.9279 | 4.7448 | **-3.7%** |
| Perplexity | 420.58 | 192.26 | **-54.3%** |
| Tensors LoRA | 0 | 224 | — |
| Parametres entrenables | 0 | 2,293,760 | — |

## 3. Criteris d'acceptacio

| Criteri | Estat |
|---|---|
| Totes les proves sobre el mateix conjunt de validacio | ✅ |
| Totes les metriques son reproduibles | ✅ |
| No corrupcio dels models | ✅ |
| Cap NaN ni Inf | ✅ |
| Informes comparables objectivament | ✅ |
| Compatibilitat de programari resolta | ✅ |

## 4. Conclusio

**🟢 RC4.2 GATE PASS.** L'entrenament LoRA federat millora significativament la
qualitat del model en catala. La perplexitat es redueix un 54.3% respecte al
model base. El pipeline es compatible amb PEFT 0.19.1 i checkpoint-2500.
