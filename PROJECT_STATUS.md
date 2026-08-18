# MeshTrainer — Project Status

**Status date:** 2026-08-17  
**Next active milestone:** Product MVP Phase 2 — Web UI

This document records the current validated development state of MeshTrainer. It is intentionally separate from older RC reports that remain in the repository for historical traceability.

## Authoritative milestones

### Distributed core

- Generic distributed core: **CLOSED**
- Certified core lineage: `b146723`
- Coordinator / worker protocol: closed
- Lease and revision authority: closed
- Weighted FedAvg: closed
- Recovery / exactly-once behavior: closed
- Core remains frozen while product layers are built on top.

### Real model backends

- Qwen3 backend: **CERTIFIED**
- MiniCPM5 backend: **CERTIFIED**
- Real two-worker overlap: **CERTIFIED**
- Multi-model portability: **PROVEN**
- Authoritative multi-model closeout lineage: `a52580c`

The current architecture is backend-driven:

```text
MeshTrainer Core
    |
    +-- TrainingBackend
           |
           +-- Qwen3
           +-- MiniCPM5
           +-- future conforming backends
```

The core should not acquire model-specific logic when another backend is added.

## Product MVP

### Phase 0 — Design

**CLOSED**

Defined:

- FastAPI management API;
- shared `MeshTrainerService` application facade;
- separate application persistence;
- CLI and Web UI over the same application layer;
- dataset/job/worker contracts;
- cancellation semantics;
- SSE monitoring contract;
- artifact and security model;
- product acceptance-test contract.

### Phase 1 — Application Layer + API + CLI

**CLOSED / CERTIFIED MVP**

Functional code lineage: `60ebd29`  
Evidence-only final closeout lineage: `4696d35`

Validated capabilities:

- independent `meshtrainer_app.db` persistence;
- JSONL `DatasetAdapter`;
- controlled dataset upload and path protection;
- `MeshTrainerService` facade;
- separate `JobRunner` process per job;
- real coordinator / worker / FedAvg orchestration without importing certification drivers;
- FastAPI API skeleton;
- scriptable CLI;
- cooperative cancellation without inventing new core round states;
- runner PID + job ID + nonce reconciliation;
- stable SQLite-rowid SSE cursor;
- per-job logs and metrics;
- artifact metadata and SHA-256 validation;
- Dummy backend hidden in production.

Validation closeout:

```text
APP-01..15                 17/17 PASS
E0-01..04                  31/31 PASS
REAL-QWEN-APP              16/16 PASS
Clean Extraction A         11/11 PASS
Clean Extraction B         11/11 PASS
Core/drivers changes       0
```

A real Qwen3 job was executed through the product application path:

```text
Browser/API-equivalent application flow
        -> MeshTrainerService
        -> JobRunner
        -> certified core
        -> real Qwen3 workers
        -> contribution submit
        -> validate
        -> activate
        -> FedAvg
        -> final adapter
```

The Phase 1 artifact/evidence closeout verified independent worker ETT, real adapter sizes and SHA-256 coherence.

## Phase 2 — Web UI

**AUTHORIZED / NEXT**

Goal: make MeshTrainer visibly usable from a browser without exposing internal test drivers or core storage.

Required product flow:

```text
Dashboard
  -> Create training job
  -> Select backend/model
  -> Select or upload dataset
  -> Configure LoRA
  -> Configure workers/concurrency
  -> Validate
  -> Start
  -> Monitor live via SSE
  -> View rounds/workers/loss/ETT/logs
  -> Cancel safely if required
  -> View/download artifacts
```

Phase 2 is expected to add:

- browser dashboard;
- dataset management UI;
- training-job wizard;
- worker cards;
- live timeline;
- metrics visualization;
- live logs;
- cancellation UI;
- artifact downloads;
- responsive and basic accessible layout;
- Playwright browser acceptance suite;
- one real Qwen smoke started from the Web UI.

The Web UI must communicate only through the Phase 1 REST/SSE application contracts.

## Phase 3 — after Web UI

Planned final MVP work:

- hardening;
- UX polish;
- installation / launch workflow;
- documentation;
- final clean packaging and release candidate.

## What is not part of the current MVP

The following remain intentionally out of scope:

- multi-user/RBAC platform;
- Kubernetes/cloud orchestration;
- billing;
- public model marketplace;
- automatic Hugging Face publishing;
- internet-facing distributed worker network;
- mobile application;
- third-party plugin ecosystem;
- hyperparameter optimizer;
- large quantization matrix.

## Artifact policy

Do not publish into Git:

- model weights;
- local model snapshots;
- generated adapters;
- checkpoints;
- application/core runtime databases;
- tokens/secrets;
- host-specific diagnostic evidence;
- temporary swap/resource artifacts.

The public repository should contain source, tests, documentation and reproducible configuration — not generated training payloads.

## GitHub synchronization note

The original public GitHub history predates the certified multi-model and Product MVP work. The status above reflects the authoritative validated project state. Public-source synchronization should preserve the frozen core/product milestone boundaries and exclude generated artifacts.
