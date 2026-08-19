"""MeshTrainerService — façana de producte sobre el core certificat (Fase 1).

Tradueix conceptes de producte (jobs, datasets, workers) a les operacions
del core. La UI/CLI/API només parlen amb aquesta capa. Cap import de drivers
de test; cap modificació del core b146723.
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone

from app.config import (APP_DB_PATH, APP_STORAGE_DIR, CORE_SERVER_PORT_BASE,
                        storage_subdir)
from app.models.schemas import (BackendInfo, DatasetValidationStatus,
                                JobStatus, TrainingConfig, TrainingJob,
                                WorkerConfig)
from app.services.dataset_service import DatasetService
from app.services.registry import (backend_available, discover_backends,
                                   discover_models)
from app.storage.db import AppDB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MeshTrainerService:
    def __init__(self, db_path: str = APP_DB_PATH):
        self.db = AppDB(db_path)
        self.datasets = DatasetService(self.db)
        self._runners: dict[str, subprocess.Popen] = {}
        self.reconcile_runners()

    # ── backends / models ────────────────────────────────────────────────
    def discover_backends(self) -> list[BackendInfo]:
        return discover_backends()

    def validate_model(self, backend_id: str, model_id: str = "") -> dict:
        if not backend_available(backend_id):
            return {"valid": False, "reason": f"backend {backend_id} no disponible"}
        if backend_id == "dummy":
            # mode test: el dummy accepta qualsevol identificador
            return {"valid": True,
                    "model": {"model_id": model_id or "dummy",
                              "backend_id": "dummy", "local_path": "dummy",
                              "exists": True}}
        models = discover_models(backend_id)
        if model_id:
            for m in models:
                if m["model_id"] == model_id:
                    return {"valid": m["exists"], "model": m}
            return {"valid": False, "reason": f"model {model_id} no trobat"}
        return {"valid": True, "models": models}

    # ── datasets ─────────────────────────────────────────────────────────
    def add_dataset(self, source_path: str, name: str = "") -> dict:
        return self.datasets.add_dataset(source_path, name)

    def upload_dataset(self, data: bytes, client_filename: str = "",
                       name: str = "", scope: str = "generic",
                       backend_id: str = "", model_id: str = "") -> dict:
        return self.datasets.upload_dataset(data, client_filename, name,
                                            scope, backend_id, model_id)

    def validate_dataset(self, dataset_id: str) -> dict:
        return self.datasets.validate_dataset(dataset_id)

    def get_dataset(self, dataset_id: str) -> dict:
        return self.datasets.get_dataset(dataset_id)

    def list_datasets(self, backend_id: str = "", model_id: str = "") -> list[dict]:
        return self.datasets.list_datasets(backend_id, model_id)

    # ── jobs ─────────────────────────────────────────────────────────────
    def create_training_job(self, name: str, backend_id: str, model_id: str,
                            dataset_id: str, training: dict,
                            workers_cfg: dict, local_path: str = "") -> dict:
        if not backend_available(backend_id):
            raise ValueError(f"backend {backend_id} no disponible")
        # ── v1.4.1 (P0 worker semantics): rebutjar al LÍMIT, no només a la
        #    UI — qualsevol client (UI, curl, API, CLI) rep el mateix error.
        w = WorkerConfig(**workers_cfg)
        if int(w.workers) != 2:
            raise ValueError(
                "workers ha de ser 2 (core certificat: worker A + B, execució "
                "local al servidor) — rebutjat a la creació")
        if int(w.concurrency or 1) != 1:
            raise ValueError(
                "concurrency no disponible (sense scheduler real) — rebutjat "
                "a la creació")
        ds = self.db.get_dataset(dataset_id)
        if not ds:
            raise KeyError(f"dataset no trobat: {dataset_id}")
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        t = TrainingConfig(**training)
        rec = {
            "job_id": job_id, "name": name, "backend_id": backend_id,
            "model_id": model_id, "local_path": local_path or None,
            "dataset_id": dataset_id,
            "method": t.method, "lora_rank": t.lora_rank,
            "lora_alpha": t.lora_alpha, "lora_dropout": t.lora_dropout,
            "learning_rate": t.learning_rate, "rounds": t.rounds,
            "max_seq_len": t.max_seq_len, "seed": t.seed,
            "workers": w.workers, "concurrency": w.concurrency,
            "device": w.device, "memory_mb": w.memory_mb,
            "output_dir": storage_subdir(f"output/{job_id}"),
            "status": "DRAFT", "validation": "PENDING",
            "validation_errors": [],
            "created_at": _now(),
        }
        self.db.insert_job(rec)
        return self.db.get_job(job_id)

    def get_job(self, job_id: str) -> dict:
        j = self.db.get_job(job_id)
        if not j:
            raise KeyError(f"job no trobat: {job_id}")
        # ── Phase 2 (additiu, només lectura): camps derivats per a la UI ──
        j = dict(j)
        evs = self.db.list_events(job_id)
        j["events"] = evs
        # progrés de rondes
        fed_rounds = [e for e in evs if e["type"] == "round.fedavg"]
        j["rounds_done"] = len(fed_rounds)
        j["rounds_total"] = j.get("rounds") or 0
        j["rounds_progress"] = (len(fed_rounds) / j["rounds_total"]
                                if j["rounds_total"] else 0)
        # mètriques agregades
        loss_h = []
        for m in self.db.list_metrics(job_id):
            if m.get("loss") is not None:
                loss_h.append({"round": m.get("round", ""), "value": m["loss"]})
        j["loss_history"] = loss_h
        j["last_loss"] = loss_h[-1]["value"] if loss_h else None
        j["ett_total"] = None
        etts = [e.get("payload", {}).get("ett") if isinstance(e.get("payload"), dict) else None
                for e in evs if e["type"] == "contribution.submit"]
        etts = [x for x in etts if x]
        if etts:
            j["ett_total"] = sum(etts)
        j["recoveries"] = sum(1 for e in evs if e["type"] == "recovery.event")
        # workers config
        try:
            j["workers_cfg_workers"] = (j.get("workers") or 0)
        except Exception:
            pass
        # v1.4.1 (P0 worker semantics): workers REALS persistits
        wmap = {}
        for e in evs:
            pl = e.get("payload") or {}
            wid = pl.get("worker_id") or ""
            if e["type"] == "worker.spawn" and wid:
                wmap[wid] = {"worker_id": wid, "pid": pl.get("pid"),
                             "shard": pl.get("shard"), "round": pl.get("round"),
                             "device": pl.get("device"),
                             "execution_host": pl.get("execution_host"),
                             "state": pl.get("state")}
            elif e["type"] == "worker.state" and wid in wmap:
                wmap[wid]["state"] = pl.get("state")
        j["workers_detail"] = list(wmap.values())
        j["execution"] = {"location": "local-server", "certified_workers": 2}
        # v1.4.1 (P1 total progress): contracte monòton, no derivat al client
        status = j.get("status") or ""
        stage_map = {
            "DRAFT": "PREPARING", "READY": "PREPARING", "STARTING": "PREPARING",
            "RUNNING": "TRAINING_WORKERS", "CANCELLING": "FINALIZING",
            "CANCELLED": "COMPLETED", "COMPLETED": "COMPLETED",
            "FAILED": "FINALIZING", "RECOVERING": "ROUND_SETUP",
        }
        stage = stage_map.get(status, "PREPARING")
        rdone = j.get("rounds_done") or 0
        rtot = j.get("rounds_total") or 0
        if status == "COMPLETED":
            overall = 1.0
            stage = "COMPLETED"
        elif status in ("CANCELLED", "FAILED"):
            # v1.4.1 (punt 4): MAI forçar 1.0 artificialment en fallida —
            # la barra reflecteix treball completat, no temps.
            overall = rdone / max(1, rtot) if rtot else 0.0
            stage = "FINALIZING"
        elif status in ("DRAFT", "READY", "STARTING"):
            overall = 0.0
        else:
            overall = min(1.0, rdone / max(1, rtot)) if rtot else 0.0
        j["progress"] = {
            "overall_fraction": round(max(0.0, min(1.0, overall)), 4),
            "round_current": rdone + (1 if status == "RUNNING" and rdone < rtot else 0),
            "round_total": rtot,
            "stage": stage,
            "workers_done": sum(1 for w in j["workers_detail"]
                                if w.get("state") in ("done", "exit-0")),
            "workers_total": max(1, len(j["workers_detail"]) or 2),
        }
        return j

    def list_jobs(self, status: str = "") -> list[dict]:
        return self.db.list_jobs(status or None)

    def validate_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        errors = []
        # dataset PASS?
        ds = self.db.get_dataset(job["dataset_id"])
        if not ds:
            errors.append("dataset no trobat")
        elif ds["validation_status"] != "PASS":
            errors.append(f"dataset no validat (status={ds['validation_status']})")
        # ── v1.4.1 (P1 dataset compatibility): rebutjar incompatible ─────
        if ds:
            sc = ds.get("scope") or "generic"
            if sc == "backend" and ds.get("backend_id") != job["backend_id"]:
                errors.append(f"dataset incompatible: específic del backend "
                              f"'{ds.get('backend_id')}', job demana "
                              f"'{job['backend_id']}'")
            elif sc == "model" and (ds.get("backend_id") != job["backend_id"]
                                    or ds.get("model_id") != job["model_id"]):
                errors.append(f"dataset incompatible: específic del model "
                              f"'{ds.get('backend_id')}/{ds.get('model_id')}', "
                              f"job demana '{job['backend_id']}/{job['model_id']}'")
        # backend disponible?
        if not backend_available(job["backend_id"]):
            errors.append(f"backend {job['backend_id']} no disponible")
        # model existeix?
        m = self.validate_model(job["backend_id"], job["model_id"])
        if not m.get("valid"):
            errors.append(f"model no vàlid: {m.get('reason', '')}")
        # ── v1.4.1 (P0 worker semantics): el core certificat executa
        #    EXACTAMENT 2 workers (A+B) al servidor local. Rebutjar
        #    qualsevol altre valor — no acceptar allò que s'ignorarà.
        if int(job.get("workers") or 0) != 2:
            errors.append("workers ha de ser 2 (core certificat: worker A + B, "
                          "execució local al servidor)")
        if int(job.get("concurrency") or 1) > 1:
            errors.append("concurrency no disponible (sense scheduler real; "
                          "es desactiva fins a nova arquitectura)")
        status = "PASS" if not errors else "FAIL"
        self.db.update_job(job_id, validation=status,
                           validation_errors=errors)
        if status == "PASS":
            self.db.update_job(job_id, status="READY")
        return self.db.get_job(job_id)

    def start_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job["status"] not in ("READY", "DRAFT"):
            raise ValueError(f"start no permès des de {job['status']}")
        if job["validation"] != "PASS":
            # intenta validar un cop més; si segueix FAIL -> denegat
            job = self.validate_job(job_id)
            if job["validation"] != "PASS":
                raise ValueError("start denegat: validation != PASS")
        self.db.update_job(job_id, status="STARTING")
        port = CORE_SERVER_PORT_BASE + (int(job_id.split("-")[-1][:4], 16) % 100)
        run_id = f"run-{job_id}"
        identity = secrets.token_hex(16)  # runner nonce (E0-03)
        cmd = [sys.executable, "-m", "app.services.job_runner",
               "--job-id", job_id, "--app-db", self.db.db_path,
               "--backend", job["backend_id"],
               "--model", job.get("local_path") or job["model_id"],
               "--dataset-path", self.db.get_dataset(job["dataset_id"])["source_path"],
               "--out-dir", job["output_dir"],
               "--rounds", str(job["rounds"]), "--seq-len", str(job["max_seq_len"]),
               "--num-examples", str(max(2, 6 // max(1, job["workers"]))),
               "--lora", json.dumps({
                   "lora_rank": job["lora_rank"], "lora_alpha": job["lora_alpha"],
                   "lora_dropout": job["lora_dropout"],
                   "learning_rate": job["learning_rate"], "seed": job["seed"]}),
               "--seed", str(job["seed"]),
               "--workers", str(job["workers"]),
               "--port", str(port), "--device", job["device"],
               "--runner-identity", identity]
        delay = os.environ.get("APP_TEST_ROUND_DELAY", "")
        if delay:
            cmd += ["--round-delay", delay]
        pause = os.environ.get("APP_TEST_PAUSE_BEFORE_VALIDATE", "")
        if pause:
            cmd += ["--pause-before-validate", pause]
        logf = open(storage_subdir(f"logs/{job_id}") + "/runner_launch.log", "w")
        p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                             text=True, cwd=os.path.dirname(os.path.abspath(__file__)) + "/../..")
        self._runners[job_id] = p
        # E0-03: persistim identitat del runner per a reconciliation
        self.db.update_job(job_id, runner_pid=p.pid, run_id=run_id,
                           runner_identity=identity,
                           runner_started_at=_now())
        return self.get_job(job_id)

    def cancel_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job["status"] not in ("RUNNING", "STARTING", "READY"):
            raise ValueError(f"cancel no permès des de {job['status']}")
        self.db.update_job(job_id, status="CANCELLING")
        self.db.add_event(job_id, "job.cancel", {})
        p = self._runners.get(job_id)
        if p and p.poll() is None:
            try:
                p.send_signal(signal.SIGTERM)
            except Exception:
                pass
            try:
                p.wait(timeout=30)
            except Exception:
                pass
        # refresca l'estat (el runner marca CANCELLED en sortir net)
        return self.get_job(job_id)

    # ── consultes de supervisió ──────────────────────────────────────────
    @staticmethod
    def _pid_alive(pid) -> bool:
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError, TypeError):
            return False

    @staticmethod
    def _is_our_runner(pid, job_id: str, runner_identity: str) -> bool:
        """E0-03 (R1): verifica EXACTAMENT que el procés és el nostre
        JobRunner: parseja /proc/<pid>/cmdline per arguments precisos
        (no substring aproximat). Exigeix simultàniament:
          - mòdul == app.services.job_runner (via -m)
          - --job-id == job_id
          - --runner-identity == runner_identity persistida
        """
        if not pid or not runner_identity:
            return False
        try:
            with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
                raw = f.read().decode("utf-8", "replace")
        except Exception:
            return False
        args = [a for a in raw.split("\x00") if a]
        if not args:
            return False
        # mòdul: esperem "python -m app.services.job_runner" o
        # "python .../app/services/job_runner.py"
        joined = " ".join(args)
        module_ok = ("app.services.job_runner" in args
                     or any("app/services/job_runner.py" in a for a in args))
        if not module_ok:
            return False
        # parseig exacte de parells --flag valor
        want = {"--job-id": job_id, "--runner-identity": runner_identity}
        for flag, expected in want.items():
            if flag not in args:
                return False
            idx = args.index(flag)
            if idx + 1 >= len(args) or args[idx + 1] != expected:
                return False
        return True

    def reconcile_runners(self) -> dict:
        """E0-03: en arrencar l'app, reconcilia els jobs RUNNING/STARTING.

        - PID viu + runner_identity present: procés continuïtat (reattach).
        - PID mort / sense identity: el runner ja no existeix → FAILED.
        - PID viu però identity no coincideix amb cap procés nostre: no es
          confia en el PID → FAILED (evita reattach a un procés aliè).
        """
        reconciled = {"reattached": [], "failed": []}
        for j in self.db.list_jobs(status="RUNNING") + self.db.list_jobs(status="STARTING"):
            pid = j.get("runner_pid")
            ident = j.get("runner_identity") or ""
            # E0-03c: no es confia només en el PID — el procés ha de ser el
            # nostre JobRunner d'aquest job (cmdline EXACTA) amb identity
            # persistida coincident (R1: nonce real, no substring).
            if (pid and ident and self._pid_alive(pid)
                    and self._is_our_runner(pid, j["job_id"], ident)):
                # El procés del runner viu; l'app el pot tornar a supervisar.
                reconciled["reattached"].append(j["job_id"])
                self._log_event(j["job_id"], "job.reconcile",
                                {"pid": pid, "identity": ident[:8],
                                 "action": "reattach"})
            else:
                # runner mort o identitat no vàlida → no deixar RUNNING etern
                reason = ("runner_pid inexistent" if not pid
                          else "procés mort" if not self._pid_alive(pid)
                          else "procés aliè (cmdline no coincideix)"
                          if not self._is_our_runner(pid, j["job_id"], ident)
                          else "identity no vàlida")
                self.db.update_job(j["job_id"], status="FAILED",
                                   last_error=f"runner no disponible en reinici ({reason})")
                self._log_event(j["job_id"], "job.reconcile",
                                {"action": "failed", "reason": reason})
                reconciled["failed"].append(j["job_id"])
        return reconciled

    def _log_event(self, job_id: str, type_: str, payload: dict):
        self.db.add_event(job_id, type_, payload)
    def get_workers(self, job_id: str) -> list[dict]:
        self.get_job(job_id)  # existeix?
        out = []
        wdir = storage_subdir(f"logs/{job_id}")
        for fname in sorted(os.listdir(wdir)):
            if fname.startswith("worker_") and fname.endswith(".log"):
                out.append({"worker": fname, "state": "TRAINING",
                            "log": os.path.join(wdir, fname)})
        return out

    def get_rounds(self, job_id: str) -> list[dict]:
        self.get_job(job_id)
        return [e for e in self.db.list_events(job_id)
                if e["type"] in ("round.create", "round.fedavg", "job.progress")]

    def get_metrics(self, job_id: str) -> list[dict]:
        return self.db.list_metrics(job_id)

    def get_logs(self, job_id: str, level: str = "", worker: str = "",
                 round_: str = "", component: str = "") -> list[dict]:
        self.get_job(job_id)
        lines = []
        wdir = storage_subdir(f"logs/{job_id}")
        for fname in sorted(os.listdir(wdir)):
            if component and component not in fname:
                continue
            if worker and worker not in fname:
                continue
            fpath = os.path.join(wdir, fname)
            with open(fpath, errors="replace") as f:
                for line in f:
                    lines.append({"file": fname, "line": line.rstrip()})
        return lines

    def list_artifacts(self, job_id: str) -> list[dict]:
        return self.db.list_artifacts(job_id)

    def get_events(self, job_id: str, after_seq: int = 0) -> list[dict]:
        """Events amb seq (rowid) > after_seq. Cursor ESTABLE (E0-04 R1)."""
        return self.db.list_events(job_id, after_seq)

    def health(self) -> dict:
        return {"ok": True, "service": "meshtrainer-app",
                "backends": [b.id for b in self.discover_backends()]}

    # ── Phase 2 (additiu): informació de sistema / settings / download ───
    def get_system_info(self) -> dict:
        """Info de sistema per al dashboard (llegeix /proc — read-only)."""
        def _meminfo():
            out = {}
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        k, _, v = line.partition(":")
                        out[k.strip()] = int(v.strip().split()[0])  # kB
            except Exception:
                pass
            return out

        def _cpu_count():
            try:
                return os.cpu_count() or 0
            except Exception:
                return 0

        def _disk():
            try:
                st = os.statvfs("/")
                return {"total": st.f_blocks * st.f_frsize,
                        "free": st.f_bavail * st.f_frsize}
            except Exception:
                return {}

        mi = _meminfo()
        ram_total = mi.get("MemTotal", 0) * 1024
        ram_avail = mi.get("MemAvailable", 0) * 1024
        swap_total = mi.get("SwapTotal", 0) * 1024
        swap_free = mi.get("SwapFree", 0) * 1024
        disk = _disk()
        # ── Phase 3 (additiu): detecció completa + guidance ─────────────
        from app.services.hardware import (detect_hardware,
                                           model_capability,
                                           recommended_config)
        hw = detect_hardware()
        avail_gb = (hw["memory"]["available_bytes"] + hw["memory"]["swap_available_bytes"]) / (1024**3)
        caps = {}
        recs = {}
        for bid in ("qwen3", "minicpm5"):
            caps[bid] = model_capability(bid, avail_gb)
            recs[bid] = recommended_config(bid, avail_gb)
        return {
            "cpu": {"cores": _cpu_count(), "model": hw["cpu"]["model"]},
            "ram": {"total": ram_total, "available": ram_avail,
                    "used": max(0, ram_total - ram_avail)},
            "swap": {"total": swap_total, "free": swap_free,
                     "used": max(0, swap_total - swap_free)},
            "disk": disk,
            "hardware": hw,
            "capabilities": caps,
            "recommended": recs,
        }

    def get_settings(self) -> dict:
        """Configuració visible per a la UI (sense secrets)."""
        from app.config import APP_ENV, APP_HOST, APP_PORT, APP_DB_PATH, APP_STORAGE_DIR
        return {
            "app_env": APP_ENV,
            "api_bind": f"{APP_HOST}:{APP_PORT}",
            "storage_dir": APP_STORAGE_DIR,
            "db_path": APP_DB_PATH,
            "admin_token_configured": bool(os.environ.get("APP_API_TOKEN", "")),
            "backends": [b.id for b in self.discover_backends()],
        }

    def get_artifact_download(self, job_id: str, artifact_id: str) -> dict:
        """Retorna el path de l'artefacte per a descàrrega segura via API.
        Valida que l'artefacte pertany al job i que el path està dins del
        storage de l'app (anti path traversal)."""
        self.get_job(job_id)  # existeix?
        arts = self.db.list_artifacts(job_id)
        art = next((a for a in arts if a["artifact_id"] == artifact_id), None)
        if not art:
            raise KeyError(f"artefacte no trobat: {artifact_id}")
        path = art["path"]
        if not os.path.exists(path):
            raise FileNotFoundError(f"artefacte no disponible: {artifact_id}")
        # anti path traversal: el path ha d'estar sota el storage de l'app
        storage = os.path.abspath(APP_STORAGE_DIR)
        if not os.path.abspath(path).startswith(storage + os.sep):
            raise PermissionError("path fora del storage — denegat")
        return {"artifact": art, "path": path}
