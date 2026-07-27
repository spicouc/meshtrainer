# meshtrainer — Operations Manual v1.0

## 1. Inici del pipeline

```bash
cd /opt/meshtrainer
source venv/bin/activate
python3 rc4_run.py config.yaml
```

## 2. Execucio normal

El runner executa automaticament:
1. Validacio de l'entorn
2. Inicialitzacio del coordinator
3. N rondes d'entrenament amb M workers cadascuna
4. Agregacio FedAvg
5. Generacio de manifests i SHA-256
6. Empaquetat de release
7. Copia a TGFS (si configurat)

## 3. Monitoritzacio

El pipeline genera logs estructurats:
```
[run-id] [stage] Missatge
[meshtrainer-run-1234-abc] [round-1] Iniciant...
[meshtrainer-run-1234-abc] [round-1] Ronda 1: 2 workers completats
[meshtrainer-run-1234-abc] [round-1] ✅ 0.01s
```

## 4. Lectura dels logs

- Cada etapa mostra durada i estat (OK/FAIL)
- Errors mostren el missatge d'excepcio
- El manifest final conte totes les etapes i els seus resultats

## 5. Resolucio d'errors

| Error | Causa | Solucio |
|---|---|---|
| "Model not found" | model_path incorrecte | Verificar path |
| "run_id is required" | Error intern del RPC | Reintentar |
| "Method not found" | Versio incorrecta del coordinator | Actualitzar codi |
| "SHA-256 verification failed" | Artefacte corrupte | Re-generar release |
| "Insufficient disk space" | Disc ple | Alliberar espai |

## 6. Resume

El pipeline no te resume automatic (RC4.3 pendent). En cas d'error:
1. Corregir la causa
2. Tornar a executar `python3 rc4_run.py config.yaml`
3. Es genera un nou run_id

## 7. Recuperacio despres d'interrupcio

1. Verificar que no queden processes penjats: `ps aux | grep coordinator`
2. Netejar DB temporal: `rm -f /tmp/*.db`
3. Re-executar el pipeline

## 8. Actualitzacio de versions

```bash
cd /opt/meshtrainer
git pull
source venv/bin/activate
pip install -r requirements.txt
python3 rc4_run.py config.yaml
```
