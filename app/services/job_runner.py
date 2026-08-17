"""JobRunner — executa un job de producte orquestrant el core certificat.

Procés separat per job (aïllament de crash, cancel·lació neta). NO importa
drivers de test: parla amb el core via training_http_server (in-process,
thread) + protocol HTTP real (CoreClient) + model_worker com a subprocessos
reals.

Flux (per round):
  prepare (1 load) → round.create → assignment.create → spawn workers A/B
  → contributions → admin validate+activate → quòrum → fedavg → adapter_N
"""
from __future__ import annotations

import argparse
import base64
import gc
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from app.config import CORE_SERVER_HOST, CORE_SERVER_PORT_BASE, storage_subdir  # noqa: E402
from app.storage.db import AppDB  # noqa: E402
from app.services.core_client import CoreClient, CoreRpcError  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CancelledError(Exception):
    """Llançada quan la cancel·lació atura els workers (no és un error)."""


def _model_path(backend: str, model: str) -> str:
    if backend == "minicpm5":
        return model or os.environ.get("MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")
    if backend == "qwen3":
        return model or "/root/qwen3_0_6b_snapshot"
    return model or "dummy"


def _release(backend) -> None:
    """Alliberament estricte del model (lliçó R1.2): close + null attrs +
    gc + malloc_trim."""
    try:
        backend.close()
    except Exception:
        pass
    for attr in ("model", "tokenizer", "peft_model", "optimizer"):
        if hasattr(backend, attr):
            try:
                setattr(backend, attr, None)
            except Exception:
                pass
    del backend
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


class JobRunner:
    def __init__(self, job_id: str, app_db: str, backend: str, model: str,
                 dataset_path: str, out_dir: str, rounds: int, seq_len: int,
                 num_examples: int, lora: dict, seed: int, workers_n: int,
                 server_port: int, device: str = "cpu", round_delay: float = 0.0,
                 runner_identity: str = ""):
        self.job_id = job_id
        self.db = AppDB(app_db)
        self.runner_identity = runner_identity
        self.backend = backend
        self.model = model
        self.dataset_path = dataset_path
        self.out_dir = out_dir
        self.rounds = max(1, rounds)
        self.seq_len = seq_len
        self.num_examples = max(1, num_examples)
        self.lora = lora or {}
        self.seed = seed
        self.round_delay = round_delay
        self.workers_n = max(2, workers_n) if workers_n >= 2 else 2
        self.port = server_port
        self.device = device
        self.admin_token = secrets.token_hex(16)
        self.server_url = f"http://{CORE_SERVER_HOST}:{self.port}"
        self.data_dir = storage_subdir(f"data/{job_id}")
        self.logs_dir = storage_subdir(f"logs/{job_id}")
        os.makedirs(self.out_dir, exist_ok=True)
        self._stop = False
        self._httpd = None
        self._worker_procs: list[subprocess.Popen] = []

    # ── logging / events ─────────────────────────────────────────────────
    def _lora_cfg(self) -> dict:
        """Tradueix el config de producte (lora_rank/lora_alpha/...) al
        format del backend certificat (lora={"r","alpha","dropout"})."""
        return {"r": int(self.lora.get("lora_rank", 8)),
                "alpha": int(self.lora.get("lora_alpha", 16)),
                "dropout": float(self.lora.get("lora_dropout", 0.05))}

    def _log(self, msg: str):
        line = f"[{_now()}] {msg}"
        print(line, flush=True)
        try:
            with open(os.path.join(self.logs_dir, "runner.log"), "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _event(self, type_: str, payload: dict):
        self.db.add_event(self.job_id, type_, payload)

    def _set_status(self, status: str, error: str = ""):
        self.db.update_job(self.job_id, status=status, last_error=error)
        self._event("job.status", {"status": status, "error": error})

    # ── dataset → data dir ───────────────────────────────────────────────
    def _prepare_data_dir(self):
        os.makedirs(self.data_dir, exist_ok=True)
        dst = os.path.join(self.data_dir, "train.jsonl")
        shutil.copyfile(self.dataset_path, dst)
        return dst

    # ── prepare (1 load consolidada) ─────────────────────────────────────
    def _prepare(self) -> dict:
        """UNA càrrega del model: adapter_0 + base_model_hash + pre_hash +
        ETT per shard. Alliberament estricte abans dels workers."""
        from model_worker import load_backend
        cls = load_backend(self.backend)
        mp = _model_path(self.backend, self.model)
        dbp = os.path.join(self.data_dir, "prepare.db")
        if os.path.exists(dbp):
            os.remove(dbp)
        bk = cls(mp, db_path=dbp, seq_len=self.seq_len, seed=self.seed,
                 lora=self._lora_cfg())
        bk.load_model()
        identity = bk.base_model_identity()
        base_hash = identity["base_model_hash"]
        ad0 = bk.adapter_to_bundle()
        ad0_sha = hashlib.sha256(ad0).hexdigest()
        bk.load_adapter(ad0, ad0_sha, strict=True)
        pre_hash = bk.adapter_hash()

        # ETT per shard A/B (tokenitzant els exemples reals del dataset)
        from model_worker import load_examples
        ett_map = {}
        for shard in ("A", "B"):
            exs = load_examples(self.data_dir, shard, self.num_examples)
            ett = 0
            for e in exs:
                tok = bk.tokenize_example(e["instruction"], e["response"])
                labels = tok[2] if isinstance(tok, tuple) else tok
                ett += bk.compute_ett(labels)
            ett_map[shard] = ett
        self._log(f"prepare: adapter_0 {ad0_sha[:16]} base {base_hash[:16]} "
                  f"pre_hash {pre_hash[:16]} ett {ett_map}")
        _release(bk)
        return {"adapter_0": ad0, "adapter_0_sha": ad0_sha,
                "base_model_hash": base_hash, "pre_hash": pre_hash,
                "ett_map": ett_map}

    # ── servei core in-process ───────────────────────────────────────────
    def _start_core_server(self):
        from training_http_server import serve
        os.environ["MESH_ADMIN_TOKEN"] = self.admin_token
        httpd, proto = serve(host=CORE_SERVER_HOST, port=self.port,
                             db_path=os.path.join(self.data_dir, "core.db"),
                             admin_token=self.admin_token)
        self._httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.4)
        self._log(f"core server a {self.server_url} (admin token ok)")
        return proto

    # ── workers ──────────────────────────────────────────────────────────
    def _shard_manifest(self, shard: str, n: int) -> str:
        from model_worker import load_examples, shard_manifest_sha
        exs = load_examples(self.data_dir, shard, n)
        return shard_manifest_sha([e["source"] for e in exs])

    def _wait_worker_loaded(self, worker_id: str, proc, logf, timeout=1200):
        """Espera que el worker hagi carregat el model (línia REGISTERED al
        seu log) abans de spawnar el següent. Política de recursos: evita el
        doble pic de LOAD; no serialitza el treball (els workers coexisteixen
        després)."""
        import re
        logf.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                self._log(f"worker {worker_id} va morir durant el LOAD "
                          f"(rc={proc.returncode})")
                return
            try:
                logf.flush()
                with open(logf.name, errors="replace") as f:
                    if re.search(r"REGISTERED", f.read()):
                        self._log(f"worker {worker_id} carregat (REGISTERED) — "
                                  f"spawn següent")
                        return
            except Exception:
                pass
            time.sleep(1.0)
        self._log(f"worker {worker_id} no va carregar en {timeout}s")

    def _spawn_worker(self, worker_id, session, shard, assignment, round_id,
                      adapter_path, adapter_sha, num_ex, train_only=False):
        cmd = [sys.executable, os.path.join(_CORE_DIR, "model_worker.py"),
               "--backend", self.backend,
               "--worker-id", worker_id, "--session-id", session,
               "--shard", shard, "--run-id", f"run-{self.job_id}",
               "--round-id", round_id, "--assignment-id", assignment,
               "--server-url", self.server_url,
               "--data-dir", self.data_dir,
               "--model", _model_path(self.backend, self.model),
               "--adapter-0", adapter_path, "--adapter-0-sha", adapter_sha,
               "--out-dir", self.out_dir,
               "--num-examples", str(num_ex), "--seq-len", str(self.seq_len)]
        if self.backend != "dummy":
            cmd += ["--backend-config",
                    json.dumps({"lora": self._lora_cfg(), "seed": self.seed})]
        else:
            # el dummy també necessita la seed (base_model_hash en depèn)
            cmd += ["--backend-config", json.dumps({"seed": self.seed})]
        if train_only:
            cmd += ["--train-only"]
        logf = open(os.path.join(self.logs_dir, f"worker_{worker_id}.log"), "w")
        p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                             text=True)
        self._log(f"spawn {worker_id} pid={p.pid}")
        self._worker_procs.append(p)
        return p, logf

    def _run_workers_round(self, round_id: str, adapter_path, adapter_sha,
                           num_ex, round_idx: int = 1) -> dict:
        """Spawn A i B com a subprocessos reals de la MATEIXA ronda
        (política de recursos: stagger de LOAD, mai serialització del
        treball). Noms ÚNICS per ronda (w-A/w-B a r1; w-A2/w-B2 a r2...)
        perquè unit_id=unit-<assignment> no col·lideixi entre rondes
        (idempotència persistent del core). Retorna evidències dels workers."""
        sfx = "" if round_idx == 1 else str(round_idx)
        specs = [(f"w-A{sfx}", f"S-A{sfx}", "A", f"asg-A{sfx}"),
                 (f"w-B{sfx}", f"S-B{sfx}", "B", f"asg-B{sfx}")]
        procs, logfs, evs = {}, {}, {}
        for i, (wid, sess, shard, asg) in enumerate(specs):
            p, lf = self._spawn_worker(wid, sess, shard, asg, round_id,
                                       adapter_path, adapter_sha, num_ex)
            procs[wid] = p
            logfs[wid] = lf
            if i == 0 and self.backend != "dummy":
                # Política de recursos (producte, Fase 1): espera que A hagi
                # CARREGAT (REGISTERED al seu log) abans de spawnar B.
                # Evita el doble pic de LOAD dels safetensors (causa de l'OOM
                # global del host). NO serialitza: un cop B carrega, ambdós
                # coexisteixen a la mateixa ronda (quòrum 2/2 del core).
                self._wait_worker_loaded(wid, p, logfs[wid])
            elif i == 0:
                time.sleep(1.0)  # dummy: stagger mínim (carrega instantània)
        for wid, p in procs.items():
            rc = p.wait(timeout=3600)
            logfs[wid].close()
            if rc != 0 and self._stop:
                # cancel·lació en curs: no és un error de codi
                raise CancelledError(f"worker {wid} aturat per cancel·lació")
            evf = os.path.join(self.out_dir, f"evidence_{wid}.json")
            if not os.path.exists(evf):
                raise RuntimeError(f"worker {wid} sense evidència (rc={rc})")
            with open(evf) as f:
                evs[wid] = json.load(f)
        return evs

    # ── ronda ────────────────────────────────────────────────────────────
    def _run_round(self, client: CoreClient, round_idx: int, run_id: str,
                   round_id: str, adapter_path, adapter_sha, adapter_bytes,
                   base_hash, pre_hash, num_ex) -> dict:
        self._event("round.create", {"round": round_id})
        client.round_create(run_id, round_id, self.backend, base_hash,
                            adapter_sha, pre_hash)
        sfx = "" if round_idx == 1 else str(round_idx)
        for wid, shard in ((f"w-A{sfx}", "A"), (f"w-B{sfx}", "B")):
            exp = self._expected_ett(round_idx, shard, num_ex)
            asg = f"asg-{shard}{sfx}"
            client.assignment_create(
                run_id, round_id, asg, wid, f"shard-{shard}",
                self._shard_manifest(shard, num_ex), base_hash, adapter_sha,
                expected_ett=exp)
            self._event("assignment.create",
                        {"assignment": asg, "shard": shard,
                         "expected_ett": exp})

        evs = self._run_workers_round(round_id, adapter_path, adapter_sha,
                                      num_ex, round_idx)
        for wid, ev in evs.items():
            self._event("contribution.submit",
                        {"worker": wid, "ett": ev.get("ett_actual"),
                         "contribution": ev.get("contribution_id", "")[:12]})

        # ADMIN: validate + activate (frontera certificada)
        for wid, ev in evs.items():
            cid = ev["contribution_id"]
            client.contribution_validate(cid)
            client.contribution_activate(cid)
            self._event("contribution.activate", {"worker": wid, "cid": cid[:12]})

        # Quòrum + fedavg
        fed = client.round_fedavg(run_id, round_id,
                                  base64.b64encode(adapter_bytes).decode("ascii"))
        self._event("round.fedavg", {"round": round_id,
                                     "adapter_sha": fed.get("adapter_1_sha", "")[:16],
                                     "num_contributions": fed.get("num_contributions")})
        a_new = base64.b64decode(fed["adapter_1_b64"])
        a_new_sha = fed["adapter_1_sha"]
        a_path = os.path.join(self.out_dir, f"adapter_{round_idx}.bundle")
        with open(a_path, "wb") as f:
            f.write(a_new)
        self._log(f"round {round_id}: adapter_{round_idx} {a_new_sha[:16]} "
                  f"ett={fed.get('total_ett')}")
        return {"adapter_bytes": a_new, "adapter_sha": a_new_sha,
                "adapter_path": a_path, "fed": fed, "evidence": evs}

    def _expected_ett(self, round_idx: int, shard: str, n: int) -> int:
        # ETT real per al nombre d'exemples del worker — es calcula amb el
        # backend a la fase prepare (mapa) o directament si cal precisió.
        from model_worker import load_backend, load_examples
        mp = _model_path(self.backend, self.model)
        dbp = os.path.join(self.data_dir, f"ett_{round_idx}_{shard}.db")
        if os.path.exists(dbp):
            os.remove(dbp)
        bk = load_backend(self.backend)(mp, db_path=dbp, seq_len=self.seq_len,
                                        seed=self.seed, lora=self._lora_cfg())
        bk.load_model()
        exs = load_examples(self.data_dir, shard, n)
        ett = 0
        for e in exs:
            tok = bk.tokenize_example(e["instruction"], e["response"])
            labels = tok[2] if isinstance(tok, tuple) else tok
            ett += bk.compute_ett(labels)
        _release(bk)
        return ett

    # ── cicle de vida ────────────────────────────────────────────────────
    def _handle_sigterm(self, signum, frame):
        self._log("SIGTERM rebut — cancel·lació neta en curs")
        self._stop = True
        for p in self._worker_procs:
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass

    def run(self):
        self._set_status("RUNNING")
        # E0-03: el runner confirma la seva identitat al job (el procés que
        # escriu la identity és el runner real; un PID aliè no val).
        if self.runner_identity:
            self.db.update_job(self.job_id, runner_identity=self.runner_identity)
        self.db.update_job(self.job_id, started_at=_now())
        try:
            self._prepare_data_dir()
            prep = self._prepare()
            self.db.update_job(self.job_id, base_model_hash=prep["base_model_hash"])
            ad0_path = os.path.join(self.out_dir, "adapter_0.bundle")
            with open(ad0_path, "wb") as f:
                f.write(prep["adapter_0"])
            self.db.add_artifact(self.job_id, "adapter_0", ad0_path,
                                 prep["adapter_0_sha"],
                                 len(prep["adapter_0"]), "r0")

            proto = self._start_core_server()
            client = CoreClient(self.server_url, self.admin_token)
            run_id = f"run-{self.job_id}"

            # Round 1 (num_examples) + rounds següents (meitat)
            adapter_bytes = prep["adapter_0"]
            adapter_sha = prep["adapter_0_sha"]
            adapter_path = ad0_path
            num_ex = self.num_examples
            base_hash = prep["base_model_hash"]
            pre_hash = prep["pre_hash"]

            # metrics
            res = None
            round_id = ""
            for i in range(1, self.rounds + 1):
                if self._stop:
                    break
                round_id = f"r{i}"
                if self.round_delay and i > 1:
                    self._log(f"round-delay {self.round_delay}s abans de {round_id}")
                    # sleep interrompible per SIGTERM (cancel·lació ràpida)
                    for _ in range(int(self.round_delay * 10)):
                        if self._stop:
                            break
                        time.sleep(0.1)
                if i > 1:
                    # pre_hash del nou baseline (1 load + alliberament)
                    from model_worker import load_backend
                    mp = _model_path(self.backend, self.model)
                    dbp = os.path.join(self.data_dir, f"prehash_r{i}.db")
                    if os.path.exists(dbp):
                        os.remove(dbp)
                    bk = load_backend(self.backend)(
                        mp, db_path=dbp, seq_len=self.seq_len,
                        seed=self.seed, lora=self._lora_cfg())
                    bk.load_model()
                    bk.load_adapter(adapter_bytes, adapter_sha, strict=True)
                    pre_hash = bk.adapter_hash()
                    _release(bk)
                    num_ex = max(1, self.num_examples // 2)

                res = self._run_round(client, i, run_id, round_id,
                                      adapter_path, adapter_sha,
                                      adapter_bytes, base_hash, pre_hash,
                                      num_ex)
                adapter_bytes = res["adapter_bytes"]
                adapter_sha = res["adapter_sha"]
                adapter_path = res["adapter_path"]
                self.db.add_artifact(self.job_id, f"adapter_{i}", adapter_path,
                                     adapter_sha, len(adapter_bytes), round_id)
                self._event("job.progress", {"round": i, "of": self.rounds})

            # metrics
            loss = res["fed"].get("avg_loss") if res else None
            if res:
                self.db.add_metric(self.job_id, round_=round_id,
                                   loss=loss, ett=res["fed"].get("total_ett"))
            if self._stop:
                self._set_status("CANCELLED")
            else:
                self._set_status("COMPLETED")
            if self._httpd:
                self._httpd.shutdown()
            self.db.update_job(self.job_id, finished_at=_now())
            return 0
        except CancelledError:
            self._log("cancel·lació neta completada")
            if self._httpd:
                try:
                    self._httpd.shutdown()
                except Exception:
                    pass
            self._set_status("CANCELLED")
            self.db.update_job(self.job_id, finished_at=_now())
            return 0
        except Exception as e:
            self._log(f"ERROR: {e}")
            if self._httpd:
                try:
                    self._httpd.shutdown()
                except Exception:
                    pass
            self._set_status("FAILED", error=str(e))
            self.db.update_job(self.job_id, finished_at=_now())
            return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--app-db", required=True)
    ap.add_argument("--backend", required=True)
    ap.add_argument("--model", default="")
    ap.add_argument("--dataset-path", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--seq-len", type=int, default=64)
    ap.add_argument("--num-examples", type=int, default=2)
    ap.add_argument("--lora", default="{}")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--port", type=int, default=CORE_SERVER_PORT_BASE)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--round-delay", type=float, default=0.0)
    ap.add_argument("--runner-identity", default="")
    args = ap.parse_args()

    runner = JobRunner(
        job_id=args.job_id, app_db=args.app_db, backend=args.backend,
        model=args.model, dataset_path=args.dataset_path,
        out_dir=args.out_dir, rounds=args.rounds, seq_len=args.seq_len,
        num_examples=args.num_examples, lora=json.loads(args.lora),
        seed=args.seed, workers_n=args.workers, server_port=args.port,
        device=args.device, round_delay=args.round_delay,
        runner_identity=args.runner_identity)
    import signal as _sig
    _sig.signal(_sig.SIGTERM, runner._handle_sigterm)
    sys.exit(runner.run())


if __name__ == "__main__":
    main()
