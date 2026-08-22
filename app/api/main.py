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
    from fastapi import (APIRouter, FastAPI, File, Form, HTTPException, Query,
                         Request, UploadFile)
    from fastapi.responses import FileResponse, StreamingResponse
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


# ── Phase 2 (additiu): sistema / settings / descàrrega d'artefactes ─────
@router.get("/system")
def system_info():
    return _wrap(lambda: service.get_system_info())


# ── Phase 3 (punt 5): first-run status ────────────────────────────────────
@router.get("/setup/status")
def setup_status():
    """Indica si cal First Run Setup (no hi ha config de l'usuari)."""
    def _check():
        import os
        from app.config import MODELS_DIR
        cfg_path = os.path.join(os.path.expanduser("~"), ".config",
                                "meshtrainer", "config.toml")
        first_run = not os.path.exists(cfg_path)
        return {
            "first_run": first_run,
            "config_exists": not first_run,
            "models_dir": MODELS_DIR,
            "steps": ["system_check", "storage", "model_availability",
                      "recommended_profile", "finish"],
        }
    return _wrap(_check)


@router.get("/settings")
def settings():
    return _wrap(lambda: service.get_settings())


@router.get("/artifacts/{job_id}/{artifact_id}/download")
def artifact_download(job_id: str, artifact_id: str):
    """Descàrrega segura via API (mai path local com URL directa).
    Anti path traversal validat a l'application layer."""
    try:
        res = service.get_artifact_download(job_id, artifact_id)
    except KeyError as e:
        return _err("not_found", str(e))
    except FileNotFoundError as e:
        return _err("artifact_unavailable", str(e))
    except PermissionError as e:
        return _err("forbidden", str(e))
    return FileResponse(res["path"], filename=os.path.basename(res["path"]),
                        media_type="application/octet-stream")


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
async def dataset_upload(file: UploadFile = File(...),
                         scope: str = Form("generic"),
                         backend_id: str = Form(""),
                         model_id: str = Form("")):
    """E0-02: upload multipart. El nom intern el genera el servidor;
    el nom del client no determina el path final (anti path traversal).

    v1.4.1 (P1): scope = generic | backend | model (compatibilitat)."""
    data = await file.read()
    return _wrap(lambda: service.upload_dataset(data, file.filename or "",
                                                "", scope, backend_id,
                                                model_id))


@router.get("/datasets")
def datasets_list(backend_id: str = Query(""), model_id: str = Query("")):
    """v1.4.1 (P1): filtra per compatibilitat (generic + backend + model)."""
    return _wrap(lambda: service.list_datasets(backend_id, model_id))


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
def job_events(job_id: str, after_seq: int = Query(0, ge=0),
               format: str = Query("sse"), request: Request = None):
    """SSE: flux d'events en viu del job (E0-04 R1 + v1.4.1 P1).

    Contracte: Content-Type text/event-stream; cada esdeveniment amb
    'id:' (seq REAL de SQLite, rowid monotònic i estable), 'event:', 'data:'.
    El cursor és el seq (rowid) — mai el timestamp en segons (evita pèrdua
    d'events al mateix segon). NO s'embolica en l'envelope REST.

    v1.4.1 (P1 reconnect): el cursor es pren de (en ordre de precedència):
      1. query ?after_seq=<n> (client explícit)
      2. header Last-Event-ID (reconnect automàtic d'EventSource)
    Cursor final = max(after_seq, Last-Event-ID) quan ambdós són vàlids.
    El servidor MAI reenvia events amb seq <= cursor → 0 duplicats i
    0 perduts en reconnect (comprovat per FT-17/18).
    """
    # v1.4.1 (P1): contracte únic — cursor = max(after_seq, Last-Event-ID)
    lei = 0
    if request is not None:
        v = request.headers.get("Last-Event-ID") or request.headers.get(
            "last-event-id") or ""
        if v.isdigit():
            lei = int(v)
    after_seq = max(after_seq, lei)
    events = service.get_events(job_id, after_seq)
    if format == "json":
        return _ok(events)

    def gen():
        last_seq = after_seq
        for e in events:
            last_seq = e["seq"]
            yield (f"id: {e['seq']}\n"
                   f"event: message\n"
                   f"data: {json.dumps(e, ensure_ascii=False)}\n\n")
        # polling lleuger (long-poll curt) per a events nous
        import time
        while True:
            new = service.get_events(job_id, last_seq)
            if new:
                for e in new:
                    last_seq = e["seq"]
                    yield (f"id: {e['seq']}\n"
                           f"event: message\n"
                           f"data: {json.dumps(e, ensure_ascii=False)}\n\n")
            time.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def create_app() -> FastAPI:
    app = FastAPI(title="MeshTrainer App MVP", version="1.4.1")
    app.include_router(router)

    # ── v1.4.1 (P0 XSS): Content-Security-Policy mínima ──────────────────
    from starlette.middleware.base import BaseHTTPMiddleware

    # v1.4.1 R1 (punt 9): style-src 'self' sense 'unsafe-inline' — la UI
    # NO usa style attributes inline (convertits a classes CSS + <progress>
    # natiu). Cap estil dinàmic per dades d'usuari.
    CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
           "img-src 'self' data:; connect-src 'self'; "
           "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
           "form-action 'self'")

    class CSPMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path.startswith(("/static", "/jobs", "/datasets",
                                            "/workers", "/settings", "/")):
                response.headers["Content-Security-Policy"] = CSP
            return response

    app.add_middleware(CSPMiddleware)

    # ── Phase 2: serveix la Web UI (vanilla, zero toolchain) ─────────────
    web_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web")
    if os.path.isdir(web_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/static", StaticFiles(directory=os.path.join(web_dir, "static")),
                  name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(os.path.join(web_dir, "index.html"))

        @app.get("/jobs/new", include_in_schema=False)
        def jobs_new():
            return FileResponse(os.path.join(web_dir, "index.html"))

        @app.get("/datasets", include_in_schema=False)
        def datasets_page():
            return FileResponse(os.path.join(web_dir, "index.html"))

        @app.get("/workers", include_in_schema=False)
        def workers_page():
            return FileResponse(os.path.join(web_dir, "index.html"))

        @app.get("/settings", include_in_schema=False)
        def settings_page():
            return FileResponse(os.path.join(web_dir, "index.html"))

        @app.get("/jobs/{job_id}", include_in_schema=False)
        def job_page(job_id: str):
            return FileResponse(os.path.join(web_dir, "index.html"))
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
