# PRODUCT / APP MVP — DESIGN V1

**Autor**: DinDjarin · **Data**: 2026-08-16
**Base**: core certificat `b146723` (inalterat) · multi-model autoritatiu `a52580c`
**Estat**: FASE 0 DISSENY — cap codi funcional d'app encara

---

## 1. Objectiu

Convertir MeshTrainer (core distribuït certificat) en una aplicació usable:

- selecció de backend/model (Qwen3 / MiniCPM5);
- selecció i càrrega de dataset;
- configuració LoRA/entrenament;
- configuració de workers;
- iniciar, cancel·lar i supervisar entrenaments;
- visualitzar rounds, workers, leases i recovery;
- mètriques de loss/ETT/progrés;
- artefactes i adapters resultants;
- estat i errors de l'execució.

L'aplicació és una **capa additiva** sobre el core certificat: no modifica cap
component del core (Coordinator, HTTP server, worker protocol, FedAvg, leases,
recovery, TrainingBackend, RC5.x). Si durant el desenvolupament apareix una
incompatibilitat real del contracte → STOP i documentar (mai adaptar el core al
producte silenciosament).

## 2. No-objectius (fora de l'MVP)

- multiusuari / RBAC complex
- cloud orchestration / Kubernetes
- billing
- model marketplace / automatic HuggingFace publishing
- workers distribuïts per internet (només local/trusted network)
- mobile app
- plugins de tercers
- quantization matrix / hyperparameter optimizer
- suport de nous formats de dataset (CSV/HF/Parquet es dissenya, no s'implementa)

## 3. Decisions preses (Fase 0)

| Decisió | Elecció | Raó |
|---|---|---|
| Producte | API + Web UI + CLI mínima (les tres) | ordre supervisor; una sola application layer |
| Stack app | Python + FastAPI + Pydantic + SQLite | mateix llenguatge que el core; tipat fort; zero infra nova |
| Frontend | HTML/CSS/JS lleuger (vanilla + fetch/SSE) | cap framework pesat no aporta valor ara |
| Temps real | SSE (servidor → client) | només cal estat/logs/progrés/mètriques; WebSocket NO (cap necessitat bidireccional real al MVP) |
| Persistència | `meshtrainer_app.db` SQLite pròpia | independent de les taules internes del Coordinator; la UI no toca mai el SQL del core |
| Execució jobs | El servei orquestra el core (Coordinator + HTTP server + `model_worker` com a subprocessos reals) | reutilitza el core certificat tal qual; el servei és l'equivalent de producte del driver de test, però NO crida els drivers de test |
| Dummy | visible només en mode `development/test` | mai per defecte a producció |
| Oracle | NO és mètrica d'usuari (és test/certificació) | separació producte vs certificació |

**Alternatives descartades**:
1. *CLI o Web UI (triar-ne una)* → descartat per ordre: l'MVP té les tres, compartint la mateixa application layer (cap duplicació de lògica).
2. *WebSocket per temps real* → descartat al MVP: SSE cobreix tots els fluxos (estat, logs, progrés, mètriques). WebSocket quedaria justificat només amb control bidireccional en viu (no previst).
3. *Exposar el Coordinator/HTTP server directament a la UI* → descartat: els drivers de test NO són API de producte; la UI només parla amb `MeshTrainerService`.
4. *Scheduler d'entrenament dins del procés FastAPI* → descartat: cada job s'executa com a procés orquestrador separat (aïllament de crash, cancel·lació neta, restart de l'app sense matar entrenaments).
5. *Frontend framework (React/Vue)* → descartat al MVP: pàgines senzilles, cap estat client complex.

## 4. Arquitectura

```
Web UI (HTML/JS, SSE)
        ↓ HTTP/JSON
Application API (FastAPI) ────→ Application Service (MeshTrainerService)
        ↑                           │            │
CLI (meshtrainer ...) ─────────────┘            │ orquestra (subprocessos)
                                                ↓
                              MeshTrainer certified core
                              (Coordinator + HTTP server + model_worker +
                               backends qwen3/minicpm5 + LeaseManager +
                               recovery journal — INALTERAT)
                                                ↓
                              SQLite del core per run (estat distribuït)
                              + SQLite de l'app (meshtrainer_app.db)
```

Regles de l'arquitectura:
1. **La UI/CLI/API mai no criden directament** `model_round_coordinator.py`,
   `generic_distributed_run.py` ni `generic_recovery_tests.py`.
2. Tot el que toca el core passa per `MeshTrainerService` (façana).
3. Els drivers de test queden fora del producte; el servei orquestra el core
   amb les mateixes operacions (round.create, assignment.create, lease,
   contribution.register/validate/activate, round.fedavg) via la llibreria i el
   protocol HTTP reals — no via els scripts de test.

## 5. Components

```
app/
  api/            # FastAPI: routers /api/health /api/backends /api/models
                  #   /api/datasets /api/jobs ... (contracte §11)
  services/       # MeshTrainerService (façana) + JobOrchestrator
                  #   + BackendRegistryService + DatasetService
  models/         # Pydantic models de producte: TrainingJob, Dataset,
                  #   WorkerDefinition, JobEvent, Artifact, Metrics
  datasets/       # DatasetAdapter (JSONL al MVP; interfície per a CSV/HF/Parquet)
  jobs/           # JobRunner (procés per job): cicle de vida, cancel·lació,
                  #   orquestració del core
  storage/        # meshtrainer_app.db (SQLite), artefactes, logs per job
  monitoring/     # col·lector de mètriques (SSE), heartbeat de workers
  web/            # HTML/CSS/JS estàtic (pantalles §20)
  cli/            # CLI mínima (mateixa application layer)
```

## 6. Model de dades (meshtrainer_app.db)

Taules conceptuals:

```sql
jobs              (job_id PK, name, backend_id, model_id, revision,
                   local_path, base_model_hash, dataset_id,
                   method, lora_rank, lora_alpha, lora_dropout,
                   learning_rate, rounds, max_seq_len, seed,
                   workers, concurrency, device, memory_mb,
                   output_dir, status, created_at, started_at,
                   finished_at, last_error)
datasets          (dataset_id PK, name, source_path, format, size,
                   examples, train_count, validation_count, test_count,
                   schema_json, created_at, validation_status)
worker_definitions(worker_id PK, host, port, backend_capabilities,
                   device, memory_mb, enabled)
job_events        (event_id PK, job_id, ts, type, payload_json)
job_artifacts     (artifact_id PK, job_id, type, path, sha256, size,
                   round, created_at)
job_metrics       (metric_id PK, job_id, round, worker_id, ts,
                   loss, val_loss, ppl, ett, duration_s)
```

Cap línia de la UI llegeix/escriu les taules internes del Coordinator
(`model_rounds`, `model_assignments`, `model_contributions`, `leases_r54`).
L'estat distribuït s'exposa via `MeshTrainerService` (llegint el core amb les
seves pròpies API/SQL del servei, mai amb SQL creuat des de la UI).

## 7. Entitat principal: TrainingJob

Camps mínims (contracte §4 de l'ordre):

```
job_id, name, created_at, started_at, finished_at
status: DRAFT|VALIDATING|READY|STARTING|RUNNING|RECOVERING|CANCELLING|
        CANCELLED|FAILED|COMPLETED
backend_id, model_id, revision, local_path (opcional), base_model_hash (quan resolt)
dataset_id
method, lora_rank, lora_alpha, lora_dropout, learning_rate,
rounds (epochs), max_seq_len, seed
workers, concurrency, device, memory constraints
output_dir, last_error
```

Transicions permeses:

```
DRAFT → VALIDATING → READY → STARTING → RUNNING
RUNNING → RECOVERING → RUNNING
RUNNING → CANCELLING → CANCELLED
RUNNING → FAILED        (crash del runner)
VALIDATING → FAILED     (validació de dataset/model)
READY → FAILED          (start denegat per validació ≠ PASS)
RUNNING → COMPLETED     (tots els rounds tancats + FedAvg final)
```

`start` NO és permès si `validation != PASS` (ni API ni CLI).

## 8. Backends

`GET /api/backends` retorna el registry real (no hardcodejat a la UI):

```
id              (qwen3, minicpm5)
display_name    (Qwen3, MiniCPM5)
capabilities    (lora, seq_len_range, dtype, ...)
required_dependencies
available       (depèn de l'entorn: imports + model present)
model_constraints
```

- Font de veritat: `model_worker.BACKENDS` (lazy registry) + inspecció de
  dependències al runtime.
- `dummy` només apareix en mode `development/test` (`APP_ENV=test`); amagat
  per defecte en producció.
- El servei fa `discover_backends()` en arrencar i ho cacheja amb TTL.

## 9. Models (backend vs model)

Separació estricta:

```
backend = qwen3      → model = Qwen/Qwen3-0.6B (snapshot local)
backend = minicpm5   → model = openbmb/MiniCPM5-1B (rev 4e9de7a0, snapshot local)
```

- `GET /api/models?backend_id=qwen3` llista snapshots locals (directoris amb
  config.json + weights) + models coneguts del registry.
- L'usuari selecciona model local o snapshot ja disponible.
- Quan es resol el model, el servei calcula `base_model_hash` (via backend) i
  el guarda al job. No marketplace al MVP.

## 10. Datasets

`Dataset` entity (§7 de l'ordre) + capa `DatasetAdapter`:

- Interfície: `discover(path)`, `validate()`, `iter_train()`, `iter_validation()`,
  `iter_test()`, `counts()`, `schema()`.
- Implementació MVP: **JSONL** (format certificat del core: exemples
  `{"instruction": ..., "response": ...}`).
- CSV/HF/Parquet: només interfície preparada, NO implementats.

Validació de dataset (abans de training, errors mostrats a la UI):

1. fitxer existeix i és llegible
2. JSONL parseja correctament (totes les línies)
3. schema compatible (camps `instruction`/`response` o `messages`)
4. exemples no buits
5. splits correctes (train/validation/test)
6. format chat/messages correcte si s'usa
7. tokenització possible (backend carregat pot tokenitzar una mostra)
8. ETT > 0 (computed amb el backend)
9. seq_len coherent (exemples no més llargs que `max_seq_len`, amb marge)

Resultat: `validation_status = PASS|FAIL` + llista d'errors per camp.

## 11. API MVP (contracte)

```
GET    /api/health
GET    /api/backends
GET    /api/models?backend_id=
POST   /api/datasets
GET    /api/datasets
GET    /api/datasets/{id}
POST   /api/datasets/{id}/validate
POST   /api/jobs
GET    /api/jobs
GET    /api/jobs/{id}
POST   /api/jobs/{id}/validate
POST   /api/jobs/{id}/start
POST   /api/jobs/{id}/cancel
GET    /api/jobs/{id}/rounds
GET    /api/jobs/{id}/workers
GET    /api/jobs/{id}/metrics
GET    /api/jobs/{id}/logs?level=&worker=&round=&component=&time=
GET    /api/jobs/{id}/artifacts
GET    /api/jobs/{id}/events          (SSE)
```

Totes les respostes: `{ok: bool, data: ..., error: {...} | null}` (error model §16).

## 12. CLI MVP

```
meshtrainer backend list
meshtrainer dataset add <path> [--name] [--format jsonl]
meshtrainer dataset validate <dataset_id>
meshtrainer job create --name --backend --model --dataset \
                       [--lora-rank] [--lora-alpha] [--lora-dropout] \
                       [--lr] [--rounds] [--seq-len] [--workers]
meshtrainer job validate <job_id>
meshtrainer job start <job_id>
meshtrainer job status <job_id>
meshtrainer job logs <job_id> [--follow]
meshtrainer job cancel <job_id>
meshtrainer job artifacts <job_id>
```

La CLI crida la mateixa `MeshTrainerService` (o l'API en mode client remot);
cap lògica duplicada entre CLI i API.

## 13. Worker contract

Separació definition / runtime state:

**Definition** (config): `worker_id, host, port, backend_capabilities, device,
memory_mb, enabled`

**Runtime** (estat viu, exposat per `GET /api/jobs/{id}/workers`):
`OFFLINE|IDLE|STARTING|TRAINING|SUBMITTING|RECOVERING|FAILED` + PID, lease,
assignment, shard, current round, heartbeat, progress, ETT.

Origen de la veritat del runtime: el core (leases_r54, model_assignments,
model_contributions, PIDs reals dels subprocessos) llegit a través del servei.

## 14. Training lifecycle (orquestració del servei)

Flux d'usuari:

```
Create Job → Select model/backend → Select dataset → Configure training
→ Configure workers → Validate → READY → Start → Monitor → Complete → Artifacts
```

Implementació al JobRunner (procés separat per job):

1. **prepare**: carrega backend una vegada (seed fixa), genera `adapter_0`
   autoritatiu + `base_model_hash` + `dataset_manifest_sha` + ETTs per shard
   (1 sola càrrega, com la consolidació R1.2); allibera el model
   (close/del/gc/malloc_trim) abans dels workers.
2. **round**: `round.create` (ADMIN) → `assignment.create` per worker (ADMIN) →
   spawn workers reals (`model_worker.py --backend ... --server-url ...`) amb
   stagger de LOAD segons memòria → workers: register/calibrate/lease/step/
   commit/checkpoint/contribution.register → ADMIN `contribution.validate` +
   `contribution.activate` → quòrum → `round.fedavg` → adapter del round.
3. Repeteix per als rounds configurats (R2, R3...).
4. **complete**: adapter final + metrics JSON + manifest + summary.

El servei **no** crida `generic_distributed_run.py`; implementa aquesta
orquestració amb les operacions del core (llibreria + HTTP JSON-RPC), que és
exactament el que el core certifica.

**Memòria**: el servei aplica el mateix aprenentatge R1.2 — el driver no reté
el model quan els workers carreguen; els workers es llancen amb stagger de
LOAD si el perfil de memòria ho requereix; els límits per worker es calculen
del `memory_mb` configurat i de la memòria lliure del host.

## 15. Cancel·lació (semàntica exacta)

`cancel` NO és `kill -9` immediat:

```
RUNNING → CANCELLING
  1. senyal SIGTERM cooperatiu als workers (no kill -9 directe)
  2. expirar/invalidar leases actives via LeaseManager (mecanisme existent)
  3. no es creen noves assignments; les contribucions en vol no s'activen
  4. persistir estat del job (CANCELLING) a meshtrainer_app.db
  5. esperar sortida neta dels subprocessos (timeout curt)
  6. si un worker no surt en el timeout → SIGKILL com a últim recurs (registrat)
  7. CANCELLED
```

Què passa amb els recursos del core:

- **leases**: expirades/invalidades → cap operació posterior les accepta
  (el core ja ho garanteix: `_require_lease` full-binding).
- **contribucions**: queden RECEIVED/VALIDATED sense ACTIVAR; cap FedAvg es
  fa sobre una ronda cancel·lada.
- **round incompleta**: queda OPEN/CANCELLED al core; no es tanca.
- **adapters parcials**: es conserven com a artefactes intermedis (no es
  declaren com a resultat final).
- **recoverability**: el journal del worker és persistent; un job cancel·lat
  no es pot reprendre automàticament al MVP (es marca CANCELLED; el dataset i
  adapter_0 es reutilitzen en un job nou).

## 16. Monitoring & events (SSE)

`GET /api/jobs/{id}/events` emet SSE amb tipus d'esdeveniment:

```
job.status, job.progress
round.create, assignment.create, worker.start, worker.lease,
worker.calibrate, contribution.submit, contribution.validate,
contribution.activate, round.fedavg, worker.release,
recovery.event, job.complete, job.failed, job.cancelled
```

Pantalla principal del job:

- **Header**: job, model, backend, status, elapsed time
- **Cards**: current round, workers online, workers active, ETT, loss,
  contributions, recoveries
- **Timeline**: els events anteriors en ordre cronològic (dades reals del core)

## 17. Mètriques (producte)

- training loss, validation loss, perplexity, ETT, progress, round duration,
  worker duration, FedAvg completion, recovery count
- **oracle NO és mètrica d'usuari** (és de test/certificació; no apareix a la UI)

Fonts: metrics del worker (loss per step), ETT del core (expected_ett vs
ett_registered), durades mesurades pel servei, `job_metrics` a l'app DB.

## 18. Logs

- `GET /api/jobs/{id}/logs?level=&worker=&round=&component=&time=`
- Cada job té el seu directori de logs (`storage/logs/{job_id}/`) — mai es
  barregen logs de jobs diferents.
- UI: live log viewer (SSE) amb filtres.
- El servei captura stdout/stderr dels subprocessos (workers + orquestrador)
  i els indexa per worker/round/component.

## 19. Artefactes

Job COMPLETED exposa:

- **final adapter** (adapter_N.bundle + sha256)
- adapters intermedis (opcional)
- metrics JSON
- config utilitzada (snapshot del job)
- manifest (fitxers + SHAs)
- training summary (rounds, ETTs, durades, recoveries)

Metadata d'artefacte: `artifact_id, type, path, sha256, size, round, created_at`.

`GET /api/jobs/{id}/artifacts` + descàrrega amb validació de path (anti path
traversal) i SHA verificable.

## 20. UX — wireframes textuals (9 pantalles)

1. **Dashboard**: jobs RUNNING/COMPLETED/FAILED (comptadors + llista recent),
   workers online/offline, disponibilitat de backends (Qwen3, MiniCPM5),
   sistema (RAM, CPU, disk, swap). Sense detalls interns a la primera pantalla.
2. **New Training Job**: wizard pas a pas (model → dataset → config → workers).
3. **Dataset**: llista + form d'addició (path, name, format) + botó Validate
   amb errors per camp.
4. **Training Configuration**: preset Safe/Custom (rank, alpha, dropout, lr,
   rounds, seq-len). target_modules no exposat (backend-owned; només Advanced
   en futur).
5. **Workers**: taula de worker definitions (enabled, device, memory) + estat
   runtime per job.
6. **Job Monitor**: header + cards + timeline (events reals) + mètriques.
7. **Logs**: viewer en viu amb filtres (level, worker, round, component).
8. **Artifacts**: llista d'adapters amb SHA/mida/round + descàrrega.
9. **Settings**: bind address, admin token, APP_ENV, storage paths.

## 21. Seguretat MVP

- bind configurable (default 127.0.0.1; trusted network si es configura)
- API admin token (obligatori per a operacions d'escriptura; `hmac.compare_digest`,
  mateix patró que el core)
- cap secret als logs (token/credentials mai loggejats)
- path traversal protection (dataset source_path, artifact download)
- dataset upload size limit
- artifact download validation (SHA + size)
- NO auth multiusuari al MVP

## 22. Error model

Tots els errors de l'API: `{ok: false, error: {code, message, details?}}`.

Codis:
```
validation.failed        (dataset/job no PASS)
backend.unavailable      (deps o model no disponibles)
model.not_found          (snapshot no trobat)
dataset.not_found / dataset.invalid / dataset.parse_error
job.not_found / job.invalid_state / job.start_not_allowed
core.protocol_error      (TrainingServerError/ModelRecoveryError mapejat)
worker.timeout / worker.crash
cancel.invalid_state
internal.error
```

El servei mapeja les excepcions del core (`ModelRecoveryError(reason)`,
`TrainingServerError`, `LeaseError`) a codis de producte sense exposar detalls
interns (el `reason` del core s'inclou com a `details` intern, mai com a text
d'usuari cru si conté informació interna).

## 23. Acceptance tests de producte (APP-01..15)

```
APP-01 create job (API + CLI)
APP-02 invalid dataset rejected (validation FAIL amb errors)
APP-03 valid dataset accepted (PASS)
APP-04 qwen backend selectable
APP-05 minicpm backend selectable
APP-06 start valid job → RUNNING → COMPLETED (dummy a test, o smoke qwen)
APP-07 cannot start invalid job (start denegat si validation != PASS)
APP-08 monitoring updates (SSE events arriben i són coherents)
APP-09 logs isolated by job (job A no contamina job B)
APP-10 cancel (RUNNING → CANCELLING → CANCELLED, workers surten nets)
APP-11 completed artifact available (adapter final present)
APP-12 SHA artifact valid (sha256(bytes) == metadata)
APP-13 restart app retains jobs (meshtrainer_app.db persistent)
APP-14 core remains unchanged (git diff b146723..HEAD sobre fitxers core = 0)
APP-15 Dummy hidden in production mode (APP_ENV=production)
```

## 24. Riscos oberts

1. **Memòria del host per 2 workers f32 simultanis** (lliçó R1.2): el servei ha
   de fer scheduling conscient de memòria (stagger de LOAD per defecte; 1
   worker en paral·lel si el perfil no hi cap). El producte NO necessita
   overlap real (això és certificació), però ha de documentar el perfil de
   hardware mínim: ~16 GB RAM per 2 workers MiniCPM5 f32, o seqüencial.
2. **Orquestració com a procés separat per job**: cal gestió de processos
   òrfens (reap en crash), ports dinàmics per run, neteja en cancel·lació.
3. **Dependència de versions**: el venv CT112 (transformers 5.14, peft 0.19)
   és el de certificació; l'app ha de declarar les mateixes versions.
4. **ETT/shards**: el càlcul d'ETT autoritatiu requereix el backend carregat;
   el JobRunner ho fa a la fase prepare (1 càrrega) i allibera abans dels
   workers (lliçó R1.2 aplicada).

## 25. Roadmap

- **FASE 0 (aquesta)**: disseny → aprovació → `PRODUCT MVP PHASE 0: CLOSED`
- **FASE 1**: Application Layer + API + CLI skeleton (MeshTrainerService,
  models, storage, datasets JSONL, jobs DRAFT→COMPLETED amb dummy, APP-01..15
  en mode test) → `PRODUCT MVP PHASE 1: CLOSED`
- **FASE 2**: Web UI (dashboard, wizard, monitor SSE, logs, artifacts)
- **FASE 3**: hardening (seguretat, errors, docs) + packaging estàndard
  (tarball + manifest + CE A/B + TGFS)

## 26. Regla d'or

**No convertir els scripts de certificació en una app improvisada.** El
producte és una capa nova sobre el core certificat; els drivers de test
(`generic_distributed_run.py`, `generic_recovery_tests.py`) es mantenen com a
evidència de certificació i no s'importen des de l'app.
