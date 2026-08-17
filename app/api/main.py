"""API skeleton FastAPI (Fase 1). Contracte del disseny §11.

El servidor FastAPI és una capa prima: tota la lògica viu a
MeshTrainerService. Endpoints SSE per a events (monitoratge en viu).
"""
from __future__ import annotations

import json
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

try:
    from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel as PBase
except ImportError as e:  # pragma: no cover
    raise RuntimeError(
        "FastAPI/pydantic requerits per a l'API (pip install fastapi "
        "uvicorn). El CLI i els serveis funcionen sense l'API.") from e

from app.models.schemas import (BackendInfo, Dataset, TrainingConfig,
                                TrainingJob, WorkerConfig)
from app.services.mesh_trainer_service import MeshTrainerService

service = MeshTrainerService()
router = APIRouter(prefix="/api")


def _ok(data):
    return {"ok": True, "data": data, "error": None}


def _err(code: str, message: str, details=None):
    return {"ok": False, "data": None,
            "error": {"code": code, "message": message, "details": details}}


def _wrap(fn):
    try:
        return _ok(fn())
    except KeyError as e:
        return _err("not_found", str(e))
    except ValueError as e:
        return _err("invalid_state", str(e))
    except Exception as e:
        return _err("internal.error", str(e))


# ── contracte §11 ────────────────────────────────────────────────────────
@router.get("/health")
def health():
    return _wrap(lambda: service.health())


@router.get("/backends")
def backends():
    return _wrap(lambda: [b.model_dump() if hasattr(b, "model_dump") else dict(b)
                          for b in service.discover_backends()])


@router.get("/models")
def models(backend_id: str = Query("")):
    return _wrap(lambda: service.validate_model(backend_id))


class DatasetCreate(PBase):
    source_path: str
    name: str = ""


@router.post("/datasets")
def dataset_create(body: DatasetCreate):
    return _wrap(lambda: service.add_dataset(body.source_path, body.name))


@router.post("/datasets/upload")
async def dataset_upload(file: UploadFile = File(...)):
    """E0-02: upload multipart. El nom intern el genera el servidor;
    el nom del client no determina el path final (anti path traversal)."""
    data = await file.read()
    return _wrap(lambda: service.upload_dataset(data, file.filename or ""))


@router.get("/datasets")
def datasets_list():
    return _wrap(lambda: service.list_datasets())


@router.get("/datasets/{dataset_id}")
def dataset_get(dataset_id: str):
    return _wrap(lambda: service.get_dataset(dataset_id))


@router.post("/datasets/{dataset_id}/validate")
def dataset_validate(dataset_id: str):
    return _wrap(lambda: service.validate_dataset(dataset_id))


class JobCreate(PBase):
    name: str
    backend_id: str
    model_id: str
    dataset_id: str
    training: dict = {}
    workers_cfg: dict = {}
    local_path: str = ""


@router.post("/jobs")
def job_create(body: JobCreate):
    return _wrap(lambda: service.create_training_job(
        body.name, body.backend_id, body.model_id, body.dataset_id,
        body.training, body.workers_cfg, body.local_path))


@router.get("/jobs")
def jobs_list(status: str = Query("")):
    return _wrap(lambda: service.list_jobs(status))


@router.get("/jobs/{job_id}")
def job_get(job_id: str):
    return _wrap(lambda: service.get_job(job_id))


@router.post("/jobs/{job_id}/validate")
def job_validate(job_id: str):
    return _wrap(lambda: service.validate_job(job_id))


@router.post("/jobs/{job_id}/start")
def job_start(job_id: str):
    return _wrap(lambda: service.start_job(job_id))


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    return _wrap(lambda: service.cancel_job(job_id))


@router.get("/jobs/{job_id}/rounds")
def job_rounds(job_id: str):
    return _wrap(lambda: service.get_rounds(job_id))


@router.get("/jobs/{job_id}/workers")
def job_workers(job_id: str):
    return _wrap(lambda: service.get_workers(job_id))


@router.get("/jobs/{job_id}/metrics")
def job_metrics(job_id: str):
    return _wrap(lambda: service.get_metrics(job_id))


@router.get("/jobs/{job_id}/logs")
def job_logs(job_id: str, level: str = Query(""), worker: str = Query(""),
             round: str = Query(""), component: str = Query("")):
    return _wrap(lambda: service.get_logs(job_id, level, worker, round, component))


@router.get("/jobs/{job_id}/artifacts")
def job_artifacts(job_id: str):
    return _wrap(lambda: service.list_artifacts(job_id))


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, after_ts: str = Query("")):
    """SSE: flux d'events en viu del job (E0-04).

    Contracte: Content-Type text/event-stream; cada esdeveniment amb
    'id:' (monotònic), 'event:', 'data:'. NO s'embolica en l'envelope
    REST {ok,data,error} — els events SSE són el payload directe.
    """
    events = service.get_events(job_id, after_ts)

    def gen():
        last = after_ts
        seq = 0
        for e in events:
            seq += 1
            last = e["ts"]
            yield (f"id: {seq}\n"
                   f"event: {e['type']}\n"
                   f"data: {json.dumps(e, ensure_ascii=False)}\n\n")
        # polling lleuger (long-poll curt) per a events nous
        import time
        while True:
            new = service.get_events(job_id, last)
            if new:
                for e in new:
                    seq += 1
                    last = e["ts"]
                    yield (f"id: {seq}\n"
                           f"event: {e['type']}\n"
                           f"data: {json.dumps(e, ensure_ascii=False)}\n\n")
            time.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def create_app() -> FastAPI:
    app = FastAPI(title="MeshTrainer App MVP", version="0.1.0")
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
