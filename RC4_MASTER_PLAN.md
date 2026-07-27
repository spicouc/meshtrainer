# RC4 Master Plan

**Projecte:** meshtrainer  
**Data:** 2026-07-26  
**Base:** RC3 (tancat i certificat)  
**Objectiu:** Convertir el prototip validat en una plataforma d'entrenament federat preparada per a us continuat

---

## 1. Objectius de RC4

| Objectiu | Descripcio |
|---|---|
| Escalabilitat | Demostrar que el sistema funciona amb 4 i 8 workers |
| Qualitat | Mesurar l'impacte real de l'entrenament amb metriques objectives |
| Res iliencia | Garantir recuperacio sense perdre consistencia davant fallades |
| Automatitzacio | Una sola ordre per executar el pipeline complet |
| Produccio | Documentacio, guies i requisits per a la versio 1.0 |

## 2. Fases

| Fase | Dependencia | Durada estimada | Criteri d'acceptacio |
|---|---|---|---|
| RC4.0 Planificacio | — | 1 dia | 7 plans aprovats |
| RC4.1 Escalabilitat | RC4.0 | 2-3 dies | 4 i 8 workers PASS sense regressions |
| RC4.2 Qualitat | RC4.1 | 2-3 dies | Baseline + benchmarks documentats |
| RC4.3 Resiliencia | RC4.2 | 2 dies | Tots els casos de fallida recuperables |
| RC4.4 Automatitzacio | RC4.3 | 1-2 dies | Ordre unica funcional |
| RC4.5 Produccio | RC4.4 | 2 dies | v1.0 documentada i empaquetada |

## 3. Dependencies

RC4.1 → RC4.0 (plans)
RC4.2 → RC4.1 (workers escalats necessaris per entrenar models mes grans)
RC4.3 → RC4.2 (sistema estable abans de provar fallades)
RC4.4 → RC4.3 (pipeline robust abans d'automatitzar)
RC4.5 → RC4.0-4.4 (totes les fases anteriors)

## 4. Cronograma orientatiu

Dia 1: RC4.0 (7 plans)
Dies 2-4: RC4.1 (escalar de 2 a 4 a 8 workers)
Dies 5-7: RC4.2 (benchmarks, perplexity, qualitat)
Dies 8-9: RC4.3 (proves de res iliencia)
Dies 10-11: RC4.4 (ordre unica d'automatitzacio)
Dies 12-13: RC4.5 (documentacio, guies, v1.0)

Total estimat: 13 dies.

## 5. Riscos

| Risc | Probabilitat | Impacte | Mitigacio |
|---|---|---|---|
| OOM amb 8 workers a 8GB RAM | Alta | Alt | Execucio sequencial o particio per lots |
| Model massa petit per millorar amb LoRA | Mitjana | Mig | Mesurar amb metriques objectives |
| Temps d'entrenament massa llarg en CPU | Alta | Mig | Acceptar limitacions del hardware actual |
| Reproduibilitat no garantida en GPU | Baixa | Mig | Congelar seed i versions de llibreries |

## 6. Estrategia de rollback

Si una fase de RC4 falla:
1. Documentar la causa i l'abast
2. Decidir si es corregeix dins la fase o es posposa a una fase posterior
3. Si el blocant es critica, tornar a l'estat previ de la fase
4. El checkpoint de cada fase es el manifest SHA-256 dels artefactes

## 7. Criteris de finalitzacio de RC4

- Totes les fases RC4.1 a RC4.5 completades
- v1.0 documentada, empaquetada i disponible a TGFS
- Pipeline automatitzat funcional
- Tests de res iliencia PASS
- Gate RC4 aprovat
