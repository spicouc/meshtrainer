# RC4 Resilience Plan

**Objectiu:** Definir les proves de recuperacio davant fallades

---

## 1. Casos de fallida

| Id | Cas | Descripcio | Procediment | Resultat esperat | PASS/FAIL |
|---|---|---|---|---|---|
| R1 | Caiguda d'un worker | Un worker deixa de respondre durant l'entrenament | Coordinator detecta inactivitat, reassigna tasca | Tasca completada per un altre worker | PASS si la tasca es completa |
| R2 | Timeout de worker | Worker no envia resultat dins del temps | Coordinator expira el lease, reassigna | Tasca reassignada, worker descartat | PASS si es reassigna |
| R3 | Corrupcio d'un delta | Worker envia delta amb hash incorrecte | Coordinator valida SHA-256, rebutja | Delta rebutjat, worker notificat | PASS si es rebutja |
| R4 | Perdua temporal del coordinator | Coordinator deixa de respondre | Workers intenten reconnexio amb backoff | Workers es reconnecten en <= 5 intents | PASS si es reconecten |
| R5 | Reinici complet del sistema | Coordinator + tots els workers moren | Nova instancia, estat desde checkpoint anterior | Es pot reprendre desde l'ultim checkpoint | PASS si el checkpoint es carrega |
| R6 | Checkpoint invalid | El checkpoint del disc esta corrupte | Coordinator detecta SHA-256 incorrecte | Es rebutja, es retorna al checkpoint anterior | PASS si es detecta |
| R7 | Duplicat de tasca | Worker envia el mateix resultat dues vegades | Coordinator detecta idempotencia | Segon enviament ignorat | PASS si es rebutja el duplicat |
| R8 | Missatge corrupte | Worker envia dades malformades | Coordinator rebutja amb error de parseig | Worker notificat, tasca no perduda | PASS si el worker pot reintentar |

## 2. Procediment generic de prova

1. Iniciar coordinator i workers en estat normal
2. Executar una ronda parcial
3. Injectar la fallida (R1-R8)
4. Observar el comportament del sistema
5. Verificar que la recuperacio es correcta
6. Verificar que no hi ha perdua de dades
7. Verificar que el checkpoint final es valid

## 3. Criteris generals PASS/FAIL

- PASS si el sistema es recupera sense intervencio manual
- PASS si el checkpoint final conte totes les dades esperades
- PASS si cap NaN/Inf apareix durant la recuperacio
- PASS si el manifest final es coherent
- FAIL si es perd informacio d'entrenament
- FAIL si el coordinator entra en estat inconsistent
