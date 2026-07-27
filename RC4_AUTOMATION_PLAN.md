# RC4 Automation Plan

**Objectiu:** Dissenyar una unica ordre que executi el pipeline complet

---

## 1. Pipeline automatitzat

Una sola ordre:

```
python3 rc4_run.py --workers 2 --rounds 3 --output ./release/
```

Ha d'executar automaticament:

1. Entrenament — iniciar coordinator + N workers, executar rondes
2. Agregacio — FedAvg sobre tots els deltes
3. Validacio — loss, perplexity, metriques de qualitat
4. Benchmark — comparacio base vs inicial vs agregat
5. Generacio de manifests — SHA-256 de tots els artefactes
6. Informes — markdown amb resultats complets
7. Empaquetat — release tar.gz
8. Verificacio SHA-256 — sha256sum -c sobre el manifest
9. Copia a TGFS — pujada automatica al canal de backup

## 2. Arguments d'entrada

| Argument | Defecte | Descripcio |
|---|---|---|
| --workers | 2 | Nombre de workers |
| --rounds | 3 | Nombre de rondes federades |
| --output | ./release/ | Directori de sortida |
| --model | /root/qwen3_0_6b_snapshot | Path del model base |
| --dataset | (dataset per defecte) | Path del dataset |
| --seed | 42 | Seed global |
| --no-tgfs | False | No pujar a TGFS |
| --dry-run | False | Simular sense executar |

## 3. Sortida

Directori de release:
```
release/
  run_manifest.json
  artifacts.sha256
  round_1/
    worker_0_delta.pt
    worker_1_delta.pt
    aggregated.pt
    manifest.json
  round_2/
    ...
  round_3/
    ...
  final_adapter/
    adapter_config.json
    adapter_model.safetensors
    manifest.json
  RC4_RELEASE_REPORT.md
  RC4_RELEASE.tar.gz
```

## 4. Criteris PASS/FAIL

- PASS si l'ordre completa sense errors
- PASS si el manifest SHA-256 es valid
- PASS si el release tar.gz es genera correctament
- PASS si TGFS rep tots els artefactes
- FAIL si qualsevol etapa falla
