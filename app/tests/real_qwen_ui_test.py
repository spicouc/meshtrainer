#!/usr/bin/env python3
"""REAL-QWEN-UI: smoke qwen3 real INICIAT DES DE LA WEB UI (punt 32).

Browser creates job → Browser validates → Browser starts → JobRunner real →
Qwen workers reals → FedAvg → COMPLETED → UI mostra artifact final.

Evidència: evidence_phase2/REAL_QWEN_UI.log + REAL_QWEN_UI_EVIDENCE.json
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

os.environ.pop("SSL_CERT_FILE", None)
os.environ.pop("SSL_CERT_DIR", None)

BASE = "http://127.0.0.1:8022"
REPO = "/root/meshtrainer"
EVID = os.path.join(REPO, "evidence_phase2")
os.makedirs(EVID, exist_ok=True)

LOG_LINES = []


def log(msg):
    LOG_LINES.append(msg)
    print(msg, flush=True)


def api(path, method="GET", body=None, base=BASE):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
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
    # dataset qwen mínim (6 exemples curts)
    ds_path = "/tmp/qwen_ui_smoke.jsonl"
    with open(ds_path, "w") as f:
        for i in range(6):
            f.write(json.dumps({"instruction": f"Quina és la capital de {['França','Itàlia','Espanya','Alemanya','Portugal','Grècia'][i]}?",
                                "response": f"La capital és {['París','Roma','Madrid','Berlín','Lisboa','Atenes'][i]}."}) + "\n")

    srv_log = open("/tmp/qwen_ui_srv.log", "w")
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api.main:app",
         "--host", "127.0.0.1", "--port", "8022"],
        cwd=REPO,
        env=dict(os.environ, APP_ENV="test",
                 # Phase 3 R1-07: el model es resol per env (com un usuari
                 # real amb el model local en una ubicació pròpia)
                 QWEN3_MODEL=os.environ.get("QWEN3_MODEL",
                                            "/root/qwen3_0_6b_snapshot")),
        stdout=srv_log, stderr=subprocess.STDOUT)
    up = False
    for _ in range(90):
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=15)
            up = True
            break
        except Exception:
            time.sleep(1)
    if not up:
        log("FAIL servidor no up: " + open("/tmp/qwen_ui_srv.log").read()[-400:])
        srv.kill()
        return 1

    log("=== REAL-QWEN-UI (qwen3 real des del navegador) ===")
    import httpx
    with open(ds_path, "rb") as f:
        r = httpx.post(f"{BASE}/api/datasets/upload",
                       files={"file": ("qwen_ui_smoke.jsonl", f, "application/octet-stream")},
                       timeout=60)
    ds = r.json()["data"]
    api(f"/api/datasets/{ds['dataset_id']}/validate", "POST")
    log(f"dataset: {ds['dataset_id']} validat PASS")

    ev = {"commit": "", "backend": "qwen3", "model": "", "job_id": "",
          "runner_pid": None, "worker_pids": [], "etts": [],
          "adapter_0": None, "adapter_1": None,
          "submit": False, "validate": False, "activate": False,
          "fedavg": False, "final_status": "", "ui_result": "PENDING"}
    try:
        import subprocess as sp
        try:
            ev["commit"] = sp.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                                  capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            pass

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 820})
            page_errors = []
            page.on("pageerror", lambda e: page_errors.append(str(e)[:150]))

            # ── Browser crea el job (wizard complet) ─────────────────────
            log("Browser → wizard /jobs/new")
            page.goto(f"{BASE}/#/jobs/new")
            page.wait_for_selector("text=Select backend", timeout=20000)
            page.locator("[data-backend=qwen3]").click()
            page.wait_for_selector("text=Select dataset", timeout=20000)
            page.select_option("#ds-select", ds["dataset_id"])
            page.wait_for_timeout(400)
            page.locator("button:has-text('Continue')").click()
            page.wait_for_selector("text=Training configuration", timeout=15000)
            page.locator("button:has-text('Continue')").click()
            page.wait_for_selector("text=Workers", timeout=15000)
            page.locator("button:has-text('Continue')").click()
            page.wait_for_selector("text=Review", timeout=15000)
            log("Browser → Review (VALIDATE JOB)")
            page.locator("button:has-text('VALIDATE JOB')").click()
            page.wait_for_timeout(3000)
            body_txt = page.inner_text("body")
            log(f"Browser → validate: {'PASS' if 'PASS' in body_txt else 'FAIL?'}")

            # ── Browser arranca l'entrenament ────────────────────────────
            page.locator("button:has-text('START TRAINING')").click()
            page.wait_for_timeout(2500)
            url = page.url
            job_id = url.split("/jobs/")[1].split("/")[0]
            ev["job_id"] = job_id
            log(f"Browser → start → monitor {job_id}")

            # ── espera COMPLETED (qwen3 f32, 1 ronda, 2 workers ~5-8 min)
            deadline = time.time() + 600
            status = ""
            while time.time() < deadline:
                try:
                    j = api(f"/api/jobs/{job_id}")
                    status = j["status"]
                except Exception:
                    status = "?"
                if status in ("COMPLETED", "FAILED", "CANCELLED"):
                    break
                time.sleep(10)
            ev["final_status"] = status
            log(f"final status: {status}")

            # ── evidència del job ─────────────────────────────────────────
            j = api(f"/api/jobs/{job_id}")
            ev["runner_pid"] = j.get("runner_pid")
            log(f"runner_pid: {ev['runner_pid']}")

            evs = api(f"/api/jobs/{job_id}/events?format=json")
            types = [e["type"] for e in evs]
            ev["submit"] = "contribution.submit" in types
            ev["validate"] = "contribution.validate" in types
            ev["activate"] = "contribution.activate" in types
            ev["fedavg"] = "round.fedavg" in types
            log(f"events: submit={ev['submit']} validate={ev['validate']} "
                f"activate={ev['activate']} fedavg={ev['fedavg']}")

            for e in evs:
                if e["type"] == "contribution.submit":
                    try:
                        pl = json.loads(e["payload"]) if isinstance(e["payload"], str) else e["payload"]
                        if pl.get("ett"):
                            ev["etts"].append(pl["ett"])
                    except Exception:
                        pass
            log(f"ETT per worker: {ev['etts']}")

            # worker PIDs reals (format: "[w-A] 471 REGISTERED base=...")
            import re
            ws = api(f"/api/jobs/{job_id}/workers")
            for w in ws:
                try:
                    fpath = w["log"]
                    with open(fpath) as fh:
                        content = fh.read()
                    m = re.search(r"\[(\S+)\]\s+(\d+)\s+REGISTERED", content)
                    if m:
                        ev["worker_pids"].append(int(m.group(2)))
                except Exception:
                    pass
            log(f"worker_pids: {ev['worker_pids']}")

            # artifacts
            arts = api(f"/api/jobs/{job_id}/artifacts")
            ev["training_summary"] = False
            for a in arts:
                if a["type"] == "adapter_0":
                    ev["adapter_0"] = {"sha256": a["sha256"], "size": a["size"]}
                if a["type"] == "adapter_1":
                    ev["adapter_1"] = {"sha256": a["sha256"], "size": a["size"]}
                if a["type"] == "training_summary.json":
                    ev["training_summary"] = True
            log(f"adapter_0: {ev['adapter_0']}")
            log(f"adapter_1: {ev['adapter_1']}")
            log(f"training_summary present (Phase 3): {ev['training_summary']}")

            # ── la UI mostra l'artifact final? ────────────────────────────
            page.reload()
            page.wait_for_timeout(3500)
            ui_txt = page.inner_text("body")
            ui_shows_artifact = "Download" in ui_txt and "adapter" in ui_txt
            ev["ui_result"] = "PASS" if (status == "COMPLETED" and ui_shows_artifact) else "FAIL"
            log(f"UI mostra artifact final: {ui_shows_artifact} → {ev['ui_result']}")

            ok = (status == "COMPLETED" and ev["submit"] and ev["validate"]
                  and ev["activate"] and ev["fedavg"] and ev["adapter_0"]
                  and ev["adapter_1"] and ev["training_summary"]
                  and ev["ui_result"] == "PASS")
            log(f"=== REAL-QWEN-UI: {'PASS' if ok else 'FAIL'} ===")
            browser.close()

    finally:
        srv.terminate()
        srv_log.close()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()

    # ── evidència ────────────────────────────────────────────────────────
    with open(os.path.join(EVID, "REAL_QWEN_UI.log"), "w") as f:
        f.write("\n".join(LOG_LINES) + "\n")
    with open(os.path.join(EVID, "REAL_QWEN_UI_EVIDENCE.json"), "w") as f:
        json.dump(ev, f, indent=2)
    log(f"evidència: {EVID}/REAL_QWEN_UI.log + .json")
    return 0 if ev["ui_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
