# ADR — BROWSER / REMOTE WORKER ARCHITECTURE

**Estat**: DISSENY — no implementat. Fora de l'abast de v1.4.1.
**Data**: 2026-08-19
**Decisió relacionada**: v1.4.1 (P0) exposa honestament 2 workers locals
certificats; aquest ADR prepara la següent fase.

## Objectiu

Un navegador/client connectat HA DE PODER SER REALMENT UN WORKER i
consumir el hardware DEL CLIENT (no el del servidor).

## Arquitectura proposada

### Tipus de workers

| Tipus | Execució | Hardware usat | Pros | Contres |
|---|---|---|---|---|
| **Local Worker** | Procés al servidor (com avui, model_worker.py) | Servidor | Certificat, determinista | No escala |
| **Remote Native Worker** | Procés remot amb runtime Python (agent/CLI) | Màquina remota | Compute real, GPU remota | Setup, confiança |
| **Browser Worker** | Web Worker / WebGPU al navegador | Client (CPU/GPU del navegador) | Zero install, el client aporta hardware | WebGPU/WASM per LoRA, no determinista fàcilment |

### Components

- **Registration**: `POST /api/workers/register` amb capabilities
  (cpu, mem, gpu/webgpu, model support) → `worker_definitions` (taula
  existent, avui sense ús).
- **Heartbeat**: `POST /api/workers/{id}/heartbeat` cada N segons;
  caducitat → worker offline, no assignable.
- **Lease**: reutilitza el contracte `rc5_4_leases.py` del core certificat
  (proves de vida, expiració, renovació) — extensió additiva.
- **Hardware discovery**: reutilitza `app/services/hardware.py` (guidance
  COMFORTABLE/LIMITED/NOT RECOMMENDED) per decidir quins models pot
  executar cada worker.
- **Assignment**: el scheduler (nou, capa d'aplicació — NO core) tria
  workers per: capacitat ≥ requisit del model, quòrum de ronda, balanceig.
- **Progress**: contracte PROGRESS_CONTRACT.md — `workers_done/total` amb
  N workers reals; cada worker reporta per SSE.
- **Cancel**: `job.status=CANCELLING` → tots els workers del job reben
  cancel (missatge SSE dedicat + tombstone).
- **Reconnect**: reutiliza el cursor SSE (max(after_seq, Last-Event-ID));
  el worker perdut re-entra amb resync.

### Model de dades (evolució de la taula `worker_definitions`)

```
worker_id, host, port, backend_capabilities JSON,
device (cpu/cuda/webgpu), memory_mb, enabled,
last_heartbeat, trust_level (local/remote-token/browser-anon)
```

### Seguretat / confiança

- Local: confiança total (com avui).
- Remote native: token per worker + xifrat TLS.
- Browser: sessió autenticada + capacitats anunciades verificades per
  benchmarks curts (mida de batch real), quota de contribució.
- El servidor mai executa codi del client: el browser worker només
  calcula (matrius/pesos) i puja resultats amb SHA verificable.

### Riscos oberts

- Determinisme amb WebGPU (flotants no associatius) → seeds + checksums.
- Latència de xarxa vs benefici (workers remots petits no valen la pena).
- Seguretat del canal del navegador (CSP/COOP/COEP per WebGPU).

## Decisió

**No implementar a v1.4.1.** Aquesta ADR és la base de disseny per a la
fase següent (scheduler real + workers remots/browser).
