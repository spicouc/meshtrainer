# RC5 — Work Unit Model

**Document:** RC5_WORK_UNIT_MODEL.md
**Versio:** 1.1.0-draft

---

## 1. Jerarquia

```
Run
 └── Ronda (Round)
      └── Assignment (per worker)
           └── Micro-unitat (Work Unit) ← ATOM TRANSACCIONAL
```

## 2. Micro-unitat com a atom transaccional

La micro-unitat es la unitat minima de treball que pot ser:

- Assignada
- Executada
- Verificada
- Facturada (receipt)
- Re-assignada en cas de fallida

**Propietats:**

| Propietat | Descripcio |
|---|---|
| Idempotent | Executar-la dos cops produeix el mateix delta |
| Determinista | Amb la mateixa seed, mateix resultat |
| Verificable | El Coordinator pot validar el receipt |
| Atòmica | O es completa sencera o es re-assigna |

## 3. Tokens efectius com a atom estadistic

El **token efectiu** es la unitat estadistica de mesura:

- 1 token efectiu = 1 token processat per una micro-unitat completada
- El total de tokens efectius d'una ronda = suma de tokens de totes les
  micro-unitats completades
- Usat per al calibratge (declarat vs observat)

## 4. Deltes

El delta es el resultat de l'entrenament d'una micro-unitat:
- Un delta per micro-unitat
- Un delta agregat per assignment
- Un delta global per ronda (FedAvg)

## 5. Contribucio unica ACTIVE per assignment

Cada worker nomes pot tenir UNA assignment activa simultàniament.
L'estat ACTIVE significa que el worker te una micro-unitat assignada
i en proces. No pot rebre una nova assignacio fins que:

- Completi l'actual (envia receipt + delta)
- Falli (timeout, error)
- Sigui cancel·lat pel Split Server
