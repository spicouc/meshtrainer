# RC5.2 Phase 1 R2 — Report

**Commit:** pending mutation gate
**Branch:** rc5.2-numerical-phase1
**Base:** meshtrainer-v1.1-rc5.1

## Correccions R2 vs R1

| Bloquejador | Correccio |
|---|---|
| Cut gradient erroni | `cut_leaf` separat per `torch.autograd.grad(loss, cut_leaf)` |
| Tolerancies massa amples | Restaurades a rtol=1e-5, atol=1e-6 (totes) |
| Base params no congelats | Tots els params del worker amb `lora` al nom tenen `requires_grad=False` |
| Falta zero_grad | `optimizer.zero_grad(set_to_none=True)` abans de cada backward |
| P1-N20 asimetric | Dos steps simetrics amb zero_grad → forward → backward → step ×2 |
| P1-N02 incomplet | Mapatge explicit, verifica 0 missing, 0 unexpected keys |
| P1-N12 tautologic | Executat despres del backward |
| P1-N21 insuficient | Calcula oracle loss independent, verifica que canviar labels altera loss |
| P1-N23 tautologic | Test funcional: compara causal vs non-causal mask |
| 5 mutants incorrectes | 6 mutants nous: randn grad, ignore labels, bypass, extra param, alter grad, ReLU worker |
| Runner sense classificacio | DETECTED / INVALID / RUNTIME-INVALID / NOT APPLIED / TIMEOUT |

## Resultats Phase 1 R2

Suite: **26/26 PASS** (strict tolerances)  
Run 1: PASS, Run 2: PASS  
Mutation: pendent
