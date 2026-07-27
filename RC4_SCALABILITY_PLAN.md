# RC4 Scalability Plan

**Objectiu:** Mesurar l'escalabilitat del sistema amb diferents nombres de workers

---

## 1. Configuracions a provar

| Prova | Workers | Tipus | Execucio | Maquinari minim |
|---|---|---|---|---|
| Referencia | 2 | Simulats o reals | Sequencial o paral·lel | 4 CPU, 8 GB RAM |
| Escala 1 | 4 | Simulats | Sequencial | 4 CPU, 8 GB RAM |
| Escala 2 | 8 | Simulats | Sequencial | 4 CPU, 8 GB RAM |

Els workers simulats (`SimulatedWorker` + `SyntheticTensorStore`) permeten provar l'escalabilitat del coordinator sense carregar el model real, evitant OOM.

## 2. Metriques per configuracio

| Metrica | Descripcio |
|---|---|
| CPU | % d'us mesurat durant l'execucio |
| RAM | RSS maxim del proces coordinator |
| Temps per ronda | Des del primer registre fins al checkpoint agregat |
| Temps d'agregacio | Durada de FedAvg |
| Temps de resposta del coordinator | Latencia RPC |
| Amplada de banda de dades | MB transferits per ronda |
| Eficiencia | (workers * temps) / temps real |

## 3. Procediment

Per a cada configuracio:
1. Iniciar coordinator amb base de dades neta
2. Crear N workers simulats
3. Registrar tots els workers
4. Assignar una tasca a cada worker
5. Cada worker genera deltes sintetics i els envia
6. Coordinator agrega
7. Mesurar i registrar metriques
8. Repetir 3 vegades per obtenir mitjana

## 4. Criteris PASS/FAIL

- PASS si 4 workers completen sense errors
- PASS si 8 workers completen sense errors
- PASS si el coordinator no supera 2 GB RAM
- PASS si el temps d'agregacio no supera 5s
- PASS si cap worker rep timeout o error
- FAIL si qualsevol worker no completa la tasca
