# RC4 Gate Specification

**Objectiu:** Definir les metriques, evidencies i criteris per declarar la versio 1.0

---

## 1. Metriques obligatories

| Metrica | Threshold | Metode de mesura |
|---|---|---|
| Loss de validacio | < 15.0 | Forward pass sobre validacio set |
| Perplexity | < 50000 | exp(loss) |
| Temps d'inferencia | < 10s per exemple | Cronometratge |
| Taxa de resposta en catala | > 80% | Recompte manual |
| RAM maxima | < 6 GB | RSS del proces |
| Tensors per delta | = 224 | recompte |
| Tensors no nuls | > 0 per worker | np.any(np.abs(delta) > 1e-10) |
| Cap NaN | = 0 | np.isnan() |
| Cap Inf | = 0 | np.isinf() |

## 2. Evidencies requerides

Per a cada fase de RC4:

- RC4.1: Logs de les 3 configuracions (2/4/8 workers)
- RC4.2: Sortida dels benchmarks + exemples d'inferencia
- RC4.3: Logs de les 8 proves de res iliencia
- RC4.4: Log de l'ordre unica completa
- RC4.5: Documentacio v1.0 completa

## 3. Documentacio minima per a la v1.0

- README.md amb instruccions d'instal·lacio i us
- ARCHITECTURE.md amb el diagrama i descripcio
- DEPLOYMENT.md amb el procediment de desplegament
- CONFIGURATION.md amb totes les opcions
- TROUBLESHOOTING.md amb problemes coneguts
- RELEASE_NOTES.md amb el changelog

## 4. Criteris PASS/FAIL per fase

| Fase | PASS | FAIL |
|---|---|---|
| RC4.1 | 4 i 8 workers completen | Qualsevol worker falla |
| RC4.2 | Totes les metriques dins del threshold | Qualsevol metrica fora |
| RC4.3 | 8/8 proves de res iliencia PASS | Alguna prova no es recupera |
| RC4.4 | Ordre unica completa sense errors | Alguna etapa falla |
| RC4.5 | Tota la documentacio present | Falta algun document |

## 5. Llurables finals de la v1.0

- `meshtrainer-v1.0.tar.gz` — codi font complet
- `RC4_VALIDATION_REPORT.md` — resultats complets
- `RC4_GATE_FINAL.md` — aprovacio del gate
- `RC4_RELEASE_MANIFEST.sha256` — verificacio criptografica
- `RC4_RELEASE_NOTES.md` — changelog i novetats

## 6. Condicions per declarar la v1.0

Totes les seguents han de ser certes:
- RC4.1 a RC4.5 completades i aprovades
- Totes les metriques obligatories dins del threshold
- Tota la documentacio minima present
- Gate RC4 aprovat pel supervisor
- Backup TGFS de la release
- SHA-256 verificable i reproduible
