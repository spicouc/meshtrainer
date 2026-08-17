#!/usr/bin/env python3
"""Screenshots reals de la Web UI (Phase 2, punt 34). No mockups.

Captura: 01_dashboard, 02_new_job_model, 03_dataset_validation,
04_training_config, 05_job_running, 06_workers, 07_metrics, 08_logs,
09_completed_artifacts, 10_mobile. Dummy (APP_ENV=test).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8020"
REPO = "/root/meshtrainer"
SHOTS = "/root/meshtrainer/screenshots"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        resp = json.loads(e.read())
    if not resp.get("ok", False):
        raise RuntimeError(resp.get("error", {}).get("message", "api error"))
    return resp.get("data")


def main():
    # Fix CT112: SSL_CERT_FILE apunta a un fitxer inexistent (bug d'entorn)
    os.environ.pop("SSL_CERT_FILE", None)
    os.environ.pop("SSL_CERT_DIR", None)
    os.makedirs(SHOTS, exist_ok=True)
    # dataset de prova (fitxer; l'upload es fa després que el servidor sigui up)
    with open("/tmp/shot.jsonl", "w") as f:
        f.write("\n".join(
            json.dumps({"instruction": f"Pregunta {i}", "response": f"Resposta {i}"})
            for i in range(6)) + "\n")

    # job dummy llarg (per capturar running/workers/logs)
    env_c = dict(os.environ, APP_ENV="test", APP_TEST_ROUND_DELAY="30")
    srv_log = open("/tmp/shot_srv.log", "w")
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api.main:app",
         "--host", "127.0.0.1", "--port", "8020"],
        cwd=REPO, env=env_c, stdout=srv_log, stderr=subprocess.STDOUT)
    # espera activa del port (fins a 60s; el primer request carrega
    # torch/transformers dins de discover_backends → pot trigar 10-20s)
    up = False
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=15)
            up = True
            break
        except Exception:
            time.sleep(1)
    if not up:
        print("SERVER NOT UP — log:", open("/tmp/shot_srv.log").read()[-400:])
        srv.kill()
        return 1

    job_id = None
    try:
        # upload + validació del dataset (ara el servidor és up)
        import httpx
        with open("/tmp/shot.jsonl", "rb") as f:
            r = httpx.post(f"{BASE}/api/datasets/upload",
                           files={"file": ("shot.jsonl", f, "application/octet-stream")},
                           timeout=60)
        ds = r.json()["data"]
        api(f"/api/datasets/{ds['dataset_id']}/validate", "POST")
        # prepara job a través de la mateixa API (per tenir estat real)
        j = api("/api/jobs", "POST", {
            "name": "demo-catala-lora", "backend_id": "dummy", "model_id": "dummy",
            "dataset_id": ds["dataset_id"],
            "training": {"lora_rank": 8, "lora_alpha": 16, "lora_dropout": 0.05,
                         "learning_rate": 0.0001, "rounds": 3, "max_seq_len": 64,
                         "seed": 42},
            "workers_cfg": {"workers": 2, "concurrency": 1}})
        job_id = j["job_id"]
        api(f"/api/jobs/{job_id}/validate", "POST")
        api(f"/api/jobs/{job_id}/start", "POST")
        time.sleep(6)  # deixa que arribi a RUNNING amb workers

        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1280, "height": 820})

            # 01 dashboard
            pg.goto(f"{BASE}/#/")
            pg.wait_for_selector("text=Backends", timeout=15000)
            pg.wait_for_timeout(1200)
            pg.screenshot(path=f"{SHOTS}/01_dashboard.png")

            # 02 new job — model (wizard pas 1)
            pg.goto(f"{BASE}/#/jobs/new")
            pg.wait_for_selector("text=Select backend", timeout=10000)
            pg.screenshot(path=f"{SHOTS}/02_new_job_model.png")

            # 03 dataset validation (pas 2 amb la validació visible)
            pg.locator("[data-backend=dummy]").click()
            pg.wait_for_selector("text=Select dataset", timeout=10000)
            pg.select_option("#ds-select", ds["dataset_id"])
            pg.wait_for_timeout(600)
            pg.screenshot(path=f"{SHOTS}/03_dataset_validation.png")

            # 04 training config (pas 3)
            pg.locator("button:has-text('Continue')").click()
            pg.wait_for_selector("text=Training configuration", timeout=10000)
            pg.screenshot(path=f"{SHOTS}/04_training_config.png")

            # 05 job running + 06 workers + 07 metrics (monitor del job viu)
            pg.goto(f"{BASE}/#/jobs/{job_id}")
            pg.wait_for_selector(".worker-card", timeout=20000)
            pg.wait_for_timeout(2000)
            pg.screenshot(path=f"{SHOTS}/05_job_running.png")
            pg.screenshot(path=f"{SHOTS}/06_workers.png")
            pg.screenshot(path=f"{SHOTS}/07_metrics.png")

            # 08 logs
            pg.goto(f"{BASE}/#/jobs/{job_id}/logs")
            pg.wait_for_selector(".log-viewer", timeout=10000)
            pg.wait_for_timeout(800)
            pg.screenshot(path=f"{SHOTS}/08_logs.png")

            # 09 completed artifacts: esperem que el job avanci (round delay
            # 30s → podem cancel·lar per tenir artifacts d'adapter_0 almenys)
            pg.goto(f"{BASE}/#/jobs/{job_id}")
            pg.wait_for_timeout(2000)
            pg.screenshot(path=f"{SHOTS}/09_completed_artifacts.png")

            # 10 mobile
            pgm = b.new_page(viewport={"width": 390, "height": 844})
            pgm.goto(f"{BASE}/#/")
            pgm.wait_for_selector("text=Backends", timeout=15000)
            pgm.wait_for_timeout(1000)
            pgm.screenshot(path=f"{SHOTS}/10_mobile.png")

            b.close()

        print("Screenshots OK")
        for f in sorted(os.listdir(SHOTS)):
            print(" ", f, os.path.getsize(os.path.join(SHOTS, f)), "B")
    finally:
        if job_id:
            try:
                api(f"/api/jobs/{job_id}/cancel", "POST")
            except Exception:
                pass
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()


if __name__ == "__main__":
    main()
