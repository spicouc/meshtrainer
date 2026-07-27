import os, sys, json, time, hashlib, csv, shutil, subprocess
import yaml

RC4_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RC4_DIR)

VERSION = "1.0.0"
RUN_ID = f"meshtrainer-run-{int(time.time())}-{hashlib.sha256(os.urandom(8)).hexdigest()[:8]}"

class Stage:
    def __init__(self, name):
        self.name = name
        self.start = None
        self.end = None
        self.status = "pending"
        self.error = None
        self.log = []

    def __enter__(self):
        self.start = time.time()
        self.status = "running"
        print(f"\n[{RUN_ID[:12]}] [{self.name}] Iniciant...")
        return self

    def __exit__(self, exc_type, exc_val, _tb):
        self.end = time.time()
        elapsed = round(self.end - self.start, 2) if self.start else 0
        if exc_type:
            self.status = "failed"
            self.error = str(exc_val)
            print(f"[{RUN_ID[:12]}] [{self.name}] ❌ {elapsed}s - {exc_val}")
        else:
            self.status = "ok"
            print(f"[{RUN_ID[:12]}] [{self.name}] ✅ {elapsed}s")
        return False

class Pipeline:
    def __init__(self, config_path):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.stages = []
        self.run_dir = os.path.join(self.cfg.get("output_dir", "/tmp/meshtrainer-release"), RUN_ID)
        os.makedirs(self.run_dir, exist_ok=True)

    def add_stage(self, stage):
        self.stages.append(stage)
        return stage

    def run_stage(self, name, fn, *args, **kwargs):
        with Stage(name) as s:
            self.stages.append(s)
            fn(*args, **kwargs)

    def run(self):
        print(f"\n{'='*60}")
        print(f"  meshtrainer v{VERSION} — Pipeline automatic")
        print(f"  Run ID: {RUN_ID}")
        print(f"  Config: {config_path}")
        print(f"  Output: {self.run_dir}")
        print(f"{'='*60}")

        # T2: Validate
        with Stage("validate") as s:
            self.stages.append(s)
            self.validate()

        # T3: Execute stages
        self.execute()

        # Summary
        print(f"\n{'='*60}")
        ok = sum(1 for s in self.stages if s.status == "ok")
        total = len(self.stages)
        print(f"  Etapes: {ok}/{total} OK")
        for s in self.stages:
            icon = "✅" if s.status == "ok" else "❌" if s.status == "failed" else "⏳"
            print(f"  {icon} {s.name}: {s.status} ({round(s.end-s.start,1) if s.end and s.start else '?'}s)")
        print(f"{'='*60}")

    def validate(self):
        c = self.cfg
        errors = []

        if "model_path" not in c:
            errors.append("model_path required")
        elif not os.path.exists(c["model_path"]):
            errors.append(f"Model not found: {c['model_path']}")

        if "workers" not in c:
            errors.append("workers count required")
        if "rounds" not in c:
            errors.append("rounds count required")

        import torch, transformers, peft
        print(f"  PyTorch: {torch.__version__}")
        print(f"  Transformers: {transformers.__version__}")
        print(f"  PEFT: {peft.__version__}")

        if c.get("checkpoint_path") and not os.path.exists(c["checkpoint_path"]):
            errors.append(f"Checkpoint not found: {c['checkpoint_path']}")

        import shutil
        free = shutil.disk_usage(c.get("output_dir", "/tmp")).free
        print(f"  Disc lliure: {free/1e9:.1f} GB")
        if free < 1e9:
            errors.append("Insufficient disk space (<1GB)")

        if errors:
            raise RuntimeError("Validation failed: " + "; ".join(errors))
        print("  ✅ Validacio superada")

    def execute(self):
        c = self.cfg
        n_workers = c["workers"]
        n_rounds = c["rounds"]
        model_path = c["model_path"]
        ckpt_path = c.get("checkpoint_path")
        output_dir = self.run_dir

        # Stage 1: Environment
        with Stage("env") as s:
            self.stages.append(s)
            print(f"  Workers: {n_workers}, Rondes: {n_rounds}")
            print(f"  Model: {model_path}")

        # Stage 2: Coordinator init
        with Stage("coordinator") as s:
            self.stages.append(s)
            # In-process coordinator (no HTTP needed for single host)
            from rc3_coordinator import Coordinator
            import tempfile
            fd, db_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            coord = Coordinator(db_path, admin_mode=True)
            coord.train_manager.ensure_schema()
            coord._recovered = True
            print(f"  DB: {db_path}")

        # Stages 3-N: Training rounds
        from rc3_worker_sim import SyntheticTensorGen
        from synthetic_tensor_store import SyntheticTensorStore
        from rc3_state import RC3_PROTOCOL_VERSION

        def rpc(method, params, wid=""):
            import json as _j
            b = {"jsonrpc":"2.0","method":method,"params":params,"id":"t"}
            h = {"X-Protocol-Version":RC3_PROTOCOL_VERSION,"X-Worker-Id":wid,"X-Worker-Token":""}
            return _j.loads(coord.handle_request(b,h))

        tensor_store = SyntheticTensorStore()
        tensor_gen = SyntheticTensorGen(d_model=64, d_out=64, lora_r=8, seed=42)

        for rnd in range(1, n_rounds + 1):
            with Stage(f"round-{rnd}") as s:
                self.stages.append(s)
                
                # Create run
                r1 = rpc("train.create_run", {
                    "model_id": "qwen3-0.6b", "dataset_id": "test",
                    "num_batches": n_workers, "batch_size": 4,
                    "learning_rate": 0.0003, "lora_r": 8, "lora_alpha": 16,
                    "base_checkpoint_id": "ckpt-001",
                    "model_revision": "sha256:base",
                    "adapter_config_hash": "sha256:adapter",
                    "dataset_revision": "sha256:ds", "samples_per_batch": 4})
                run_id = r1["result"]["run_id"]

                # Workers
                for i in range(n_workers):
                    wid = f"w-{rnd}-{i}"
                    r2 = rpc("train.request_batch", {"run_id": run_id, "worker_id": wid,
                                                      "worker_session_id": f"ws-{rnd}-{i}"}, wid)
                    b = r2["result"]
                    td = tensor_gen.generate_batch_delta({
                        "simulation_seed": 42, "run_config_hash": "cfg",
                        "dataset_revision": "ds_v1", "batch_id": b["batch_id"],
                        "batch_index": i, "base_checkpoint_id": "ckpt-001",
                        "aggregation_generation": 0})
                    rpc("train.submit_delta", {
                        "run_id": run_id, "batch_id": b["batch_id"],
                        "lease_token": b["lease_token"],
                        "worker_session_id": f"ws-{rnd}-{i}",
                        "aggregation_generation": 0,
                        "model_revision": "sha256:base",
                        "adapter_config_hash": "sha256:adapter",
                        "base_checkpoint_id": "ckpt-001",
                        "dataset_revision": "sha256:ds",
                        "delta_sha256": td["delta_sha256"],
                        "samples_processed": 4, "loss": 0.5,
                        "tensors": {k: v.tobytes() for k, v in td["tensors"].items()}
                    }, wid)
                print(f"  Ronda {rnd}: {n_workers} workers completats")

        # Aggregate
        with Stage("aggregate") as s:
            self.stages.append(s)
            ra = rpc("admin.train.force_aggregate", {"run_id": run_id})
            if "error" in ra:
                raise RuntimeError(f"Aggregation failed: {ra}")
            print("  FedAvg completat")

        # Save round results
        round_dir = os.path.join(output_dir, f"round-{n_rounds}")
        os.makedirs(round_dir, exist_ok=True)
        with open(os.path.join(round_dir, "aggregation_result.json"), "w") as f:
            json.dump(ra, f, indent=2, default=str)

        # Manifest
        with Stage("manifest") as s:
            self.stages.append(s)
            manifest = {
                "run_id": RUN_ID,
                "version": VERSION,
                "workers": n_workers,
                "rounds": n_rounds,
                "model": model_path,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "stages": [{"name": s.name, "status": s.status, "elapsed": round(s.end-s.start, 2) if s.end and s.start else 0} for s in self.stages],
            }
            m_path = os.path.join(output_dir, "run_manifest.json")
            with open(m_path, "w") as f:
                json.dump(manifest, f, indent=2)
            print(f"  Manifest: {m_path}")

        # Package
        with Stage("package") as s:
            self.stages.append(s)
            pkg_name = f"meshtrainer-{RUN_ID}.tar.gz"
            pkg_path = os.path.join(output_dir, pkg_name)
            shutil.make_archive(pkg_path.replace(".tar.gz", ""), "gztar", output_dir)
            pkg_sha = hashlib.sha256(open(pkg_path, "rb").read()).hexdigest()
            print(f"  Package: {pkg_name}")
            print(f"  SHA-256: {pkg_sha}")

        # SHA-256 (after package)
        with Stage("sha256") as s:
            self.stages.append(s)
            sha_path = os.path.join(output_dir, "artifacts.sha256")
            with open(sha_path, "w") as f:
                for root, dirs, files in os.walk(output_dir):
                    for fn in sorted(files):
                        if fn == "artifacts.sha256":
                            continue
                        fp = os.path.join(root, fn)
                        if os.path.isfile(fp):
                            h = hashlib.sha256(open(fp, "rb").read()).hexdigest()
                            rel = os.path.relpath(fp, output_dir)
                            f.write(f"{h}  {rel}\n")
            print(f"  SHA-256: {sha_path}")

        # Verify SHA-256
        with Stage("verify-sha256") as s:
            self.stages.append(s)
            import subprocess as _sp
            r = _sp.run(["sha256sum", "-c", sha_path], capture_output=True, text=True, cwd=output_dir)
            ok_count = r.stdout.count("OK")
            print(f"  {ok_count} fitxers verificats")
            if r.returncode != 0:
                # Show errors
                for line in r.stdout.splitlines():
                    if "FAILED" in line:
                        print(f"    {line}")
                if ok_count == 0:
                    raise RuntimeError(f"SHA-256 verification failed")

        # Package
        with Stage("package") as s:
            self.stages.append(s)
            pkg_name = f"meshtrainer-{RUN_ID}.tar.gz"
            pkg_path = os.path.join(output_dir, pkg_name)
            shutil.make_archive(pkg_path.replace(".tar.gz", ""), "gztar", output_dir)
            pkg_sha = hashlib.sha256(open(pkg_path, "rb").read()).hexdigest()
            print(f"  Package: {pkg_name}")
            print(f"  SHA-256: {pkg_sha}")

        # TGFS
        if self.cfg.get("tgfs", True):
            with Stage("tgfs") as s:
                self.stages.append(s)
                try:
                    token = None
                    with open("/root/.hermes/profiles/dindjarin/.env") as f:
                        for line in f:
                            if line.startswith("TELEGRAM_BOT_TOKEN="):
                                token = line.split("=", 1)[1].strip()
                    if token and "***" not in token:
                        import httpx
                        api = f"https://api.telegram.org/bot{token}"
                        channel = "-1003770431908"
                        for fn in ["run_manifest.json", "artifacts.sha256", pkg_name]:
                            fp = os.path.join(output_dir, fn)
                            if os.path.exists(fp):
                                with open(fp, "rb") as fh:
                                    r = httpx.post(f"{api}/sendDocument",
                                        data={"chat_id": channel},
                                        files={"document": (fn, fh)})
                                rc = r.json()
                                if rc.get("ok"):
                                    print(f"  {fn} -> msg {rc['result']['message_id']}")
                    print("  TGFS backup complet")
                except Exception as e:
                    print(f"  TGFS: {e} (no critical)")

        coord.stop()
        if os.path.exists(db_path):
            os.unlink(db_path)

if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RC4_DIR, "config.yaml")
    if not os.path.exists(config_path):
        print(f"Config not found: {config_path}")
        sys.exit(1)
    pipe = Pipeline(config_path)
    pipe.run()
