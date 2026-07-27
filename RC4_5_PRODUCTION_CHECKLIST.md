# meshtrainer — Production Checklist v1.0

## Abans del desplegament

- [ ] Totes les dependencies instal·lades
- [ ] Model base descarregat i verificat
- [ ] Config.yaml validat
- [ ] Runner probat amb 2 workers, 2 rondes
- [ ] Tests basics executats (pytest -k "T1 or T2")
- [ ] Disc amb espai suficient (>10 GB)
- [ ] Permisos d'escriptura correctes

## Durant l'execució

- [ ] Logs estructurats visibles
- [ ] Coordinator respon a health check
- [ ] Workers es registren correctament
- [ ] Rondes es completen sense errors
- [ ] FedAvg es completa
- [ ] Manifest es genera
- [ ] SHA-256 es verifica
- [ ] Release es genera correctament

## Després de l'execució

- [ ] Release tar.gz disponible
- [ ] SHA-256 del paquet verificat
- [ ] Manifests complets
- [ ] Backup TGFS realitzat (si aplica)
- [ ] Logs revisats per errors
- [ ] Checkpoints accessibles

## Verificacio final

- [ ] `python3 rc4_run.py config.yaml` completa sense errors
- [ ] sha256sum -c artifacts.sha256 = ALL FILES OK
- [ ] Release tar.gz sha256 coincideix amb el manifest
