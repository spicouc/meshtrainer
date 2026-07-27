# RC4 Baseline Plan

**Objectiu:** Definir la metodologia d'avaluacio de qualitat del model

---

## 1. Conjunt de validacio

Textos en catala separats del conjunt d'entrenament, versionats i congelats.

| Categoria | Exemples | Descripcio |
|---|---|---|
| Comprensió | 5 | Preguntes sobre fets en catala |
| Generacio | 5 | Instruccions obertes en catala |
| Resposta curta | 5 | Preguntes factuals |
| Resposta estructurada | 5 | Sol·licituds de llistats o formats |
| Ortografia | 5 | Textos amb ortografia correcta i incorrecta |
| Morfosintaxi | 5 | Frases amb estructures gramaticals diverses |

Total: 30 exemples.

## 2. Metriques

| Metrica | Metode | Valor esperat |
|---|---|---|
| Loss | Forward pass sobre validacio | < loss d'entrenament |
| Perplexity | exp(loss) | < 50000 (base) |
| Temps d'inferencia | Cronometratge per exemple | < 10s per exemple |
| Us de memoria | RSS del proces | < 4 GB |
| Taxa de resposta en catala | Recompte manual | > 80% |
| Compliment instruccions | Judici humà o automatitzat | > 60% |
| Repeticions | Deteccio de n-grames duplicats | < 10% |

## 3. Prompts d'inferencia en catala

1. "Quina es la capital de Noruega?"
2. "Explica el cicle de l'aigua en catala."
3. "Escriu una llista de tres coses que es poden fer per estalviar energia."
4. "Com es diu el proces pel qual les plantes fabriquen el seu propi aliment?"
5. "Corregeix la frase: 'El gos corren pel carrer'"
6. "Quines son les comarques de Girona?"
7. "Descriu breument que es la fotosintesi."
8. "Fes una frase amb la paraula 'ordinador'."
9. "Tradueix al catala: 'The sun rises in the east'."
10. "Qui va escriure El Quixot?"

## 4. Comparacions

Per a cada metrica, comparar:
- Model base sense LoRA (Qwen3-0.6B pur)
- Model amb LoRA inicial (checkpoint-2500)
- Model amb LoRA agregat (despres de FedAvg)
- Model amb LoRA final (despres de tot l'entrenament)

## 5. Metodologia

1. Carregar model tokenizer
2. Tokenitzar cada prompt
3. Forward pass (sense gradients)
4. Extreure loss i logits
5. Generar text (autoregressiu, max 128 tokens)
6. Avaluar qualitativament la resposta
7. Recollir metriques
8. Repetir per cada variant del model

## 6. Criteris PASS/FAIL

- PASS si loss de validacio < loss d'entrenament * 1.5
- PASS si perplexity < 2x la referencia
- PASS si taxa de resposta en catala > 80%
- FAIL si apareix NaN o Inf en qualsevol forward pass
