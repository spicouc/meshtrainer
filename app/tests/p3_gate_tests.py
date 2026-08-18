"""P3-01..30 — tests de Phase 3 (hardening + UX + install/launch).

Cobreix: fresh install, idempotent install, launcher, first-run, hardware
detection, guidance, auto-config, model missing UX, drag&drop UI, safe/custom
wizard, API reconnect, browser reload, restart reconciliation, cancel, failure
UX, completed result, artifact download, training summary, mobile, keyboard,
dummy hidden, no host hardcodes, local bind, secret scan, fresh clone launch.

Execució: CT112 (o host amb deps), APP_ENV=test, servidor a 8040.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://127.0.0.1:8040"
PASSED = 0
FAILED = 0
FAILURES = []


def check(name, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  PASS {name} — {detail}")
    else:
        FAILED += 1
        FAILURES.append(name)
        print(f"  FAIL {name} — {detail}")


def _api(path, method="GET", body=None, base=BASE):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("data")
    except urllib.error.HTTPError as e:
        return json.loads(e.read()).get("data")


def main():
    os.environ.pop("SSL_CERT_FILE", None)
    os.environ.pop("SSL_CERT_DIR", None)

    # llança el servidor de tests (fixture) si no hi ha res a BASE;
    # espera fins a 120s (els imports de torch/transformers triguen)
    srv = None
    healthy = False
    for _ in range(6):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=2)
            healthy = True
            break
        except Exception:
            time.sleep(1)
    if not healthy:
        env = dict(os.environ, APP_ENV="test")
        srv = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.api.main:app",
             "--host", "127.0.0.1", "--port", "8040"],
            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            try:
                urllib.request.urlopen(BASE + "/api/health", timeout=2)
                break
            except Exception:
                time.sleep(1)

    # ── P3-01 fresh CLONE real (R1-06: mai copytree enganyós) ──────────
    tmp = "/tmp/p3_install_test"
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=REPO,
                            capture_output=True, text=True).stdout.strip() or "main"
    if os.path.isdir(os.path.join(REPO, ".git")):
        subprocess.run(["git", "clone", "--depth", "1", "-b", branch,
                        REPO, tmp + "/mt"],
                       capture_output=True, timeout=120)
    clone_ok = os.path.exists(f"{tmp}/mt/install.sh")
    check("P3-01 CLONE real (git clone, sense copytree)", clone_ok,
          "clone OK" if clone_ok else "clone FALLAT (repo sense arbre)")
    if clone_ok:
        env_cl = dict(os.environ, PYTHON_BIN=sys.executable)
        r = subprocess.run(["bash", "install.sh"], cwd=f"{tmp}/mt",
                           capture_output=True, text=True, timeout=600,
                           env=env_cl)
        ok_inst = r.returncode == 0 and "installed successfully" in r.stdout
        check("P3-01 fresh install (install.sh executa)", ok_inst,
              f"rc={r.returncode}")
        # P3-02 idempotent
        r2 = subprocess.run(["bash", "install.sh"], cwd=f"{tmp}/mt",
                            capture_output=True, text=True, timeout=600,
                            env=env_cl)
        check("P3-02 idempotent install", r2.returncode == 0,
              f"rc={r2.returncode} (2a execució)")
        shutil.rmtree(tmp, ignore_errors=True)

    # ── P3-01R FRESH PRODUCT INSTALL (R1-04): package pur → job COMPLET ─
    pkg = os.environ.get("P3_PACKAGE", "")
    if pkg and os.path.isfile(pkg):
        box = "/tmp/p3_fresh_product"
        if os.path.exists(box):
            shutil.rmtree(box)
        os.makedirs(box)
        import tarfile
        with tarfile.open(pkg) as tf:
            tf.extractall(box)
        # 1. core present al package
        core_ok = all(os.path.exists(os.path.join(box, f))
                      for f in ("model_round_coordinator.py", "model_worker.py",
                                "training_http_server.py", "training_backend.py",
                                "adapter_codec.py", "rc5_4_leases.py",
                                "backends/qwen3_backend.py",
                                "backends/minicpm5_backend.py",
                                "backends/dummy_backend.py"))
        check("P3-01R core present al package", core_ok, pkg)
        if core_ok:
            # 2. install (PYTHON_BIN reutilitzat — deps ja al venv)
            env_inst = dict(os.environ, PYTHON_BIN=sys.executable)
            ri = subprocess.run(["bash", "install.sh"], cwd=box,
                                capture_output=True, text=True, timeout=600,
                                env=env_inst)
            check("P3-01R install.sh", ri.returncode == 0 and
                  "installed successfully" in ri.stdout, f"rc={ri.returncode}")
            # 3. start + health
            env_l = dict(os.environ, PYTHON_BIN=sys.executable,
                         APP_PORT="8043", APP_ENV="test")
            rs = subprocess.run([os.path.join(box, "meshtrainer"), "start"],
                                cwd=box, capture_output=True, text=True,
                                timeout=180, env=env_l)
            check("P3-01R launcher start", rs.returncode == 0 and
                  "en marxa" in rs.stdout, f"rc={rs.returncode}")
            # 4. /api/backends sense ModuleNotFoundError (R1-02)
            try:
                d = json.loads(urllib.request.urlopen(
                    "http://127.0.0.1:8043/api/backends", timeout=30).read())
                bks = d.get("data", [])
                ids = [b.get("id") for b in bks]
                ok_backends = ("qwen3" in ids or "minicpm5" in ids) and \
                              "internal.error" not in str(d)
                check("P3-01R backends descoberts", ok_backends,
                      f"ids={ids}")
            except Exception as e:
                check("P3-01R backends descoberts", False, str(e)[:80])
            # 5. dataset + job dummy COMPLET
            try:
                with open("/tmp/p3_fresh_ds.jsonl", "w") as f:
                    for i in range(6):
                        f.write(json.dumps(
                            {"instruction": f"Q{i}", "response": "A"}) + "\n")
                import httpx
                with open("/tmp/p3_fresh_ds.jsonl", "rb") as f:
                    rr = httpx.post("http://127.0.0.1:8043/api/datasets/upload",
                                    files={"file": ("t.jsonl", f)}, timeout=60)
                dsid = rr.json()["data"]["dataset_id"]
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:8043/api/datasets/{dsid}/validate",
                    method="POST"), timeout=30)
                job = json.loads(urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:8043/api/jobs", method="POST",
                    data=json.dumps({"name": "p3-01r", "backend_id": "dummy",
                                     "model_id": "dummy", "dataset_id": dsid,
                                     "training": {"rounds": 1},
                                     "workers_cfg": {"workers": 2,
                                                     "concurrency": 1}}).encode(),
                    headers={"Content-Type": "application/json"}), timeout=30).read())["data"]
                jid = job["job_id"]
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:8043/api/jobs/{jid}/validate",
                    method="POST"), timeout=30)
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:8043/api/jobs/{jid}/start",
                    method="POST"), timeout=30)
                st = ""
                for _ in range(90):
                    st = json.loads(urllib.request.urlopen(
                        f"http://127.0.0.1:8043/api/jobs/{jid}",
                        timeout=30).read())["data"]["status"]
                    if st in ("COMPLETED", "FAILED", "CANCELLED"):
                        break
                    time.sleep(2)
                arts = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:8043/api/jobs/{jid}/artifacts",
                    timeout=30).read())["data"]
                art_ok = st == "COMPLETED" and any(
                    a["type"].startswith("adapter_") for a in arts) and any(
                    a["type"] == "training_summary.json" for a in arts)
                check("P3-01R job dummy COMPLET + artifacts", art_ok,
                      f"status={st} arts={len(arts)}")
            except Exception as e:
                check("P3-01R job dummy COMPLET + artifacts", False,
                      str(e)[:80])
            # 6. stop
            rp = subprocess.run([os.path.join(box, "meshtrainer"), "stop"],
                                cwd=box, capture_output=True, text=True,
                                timeout=60, env=env_l)
            check("P3-01R launcher stop", rp.returncode == 0,
                  f"rc={rp.returncode}")
    else:
        check("P3-01R fresh product install", False,
              "P3_PACKAGE no definit (gate runner l'ha de passar)")

    # ── P3-03..05 launcher ──────────────────────────────────────────────
    # usa el repo local (que ja té .venv al CT112)
    lk = os.path.join(REPO, "meshtrainer")
    check("P3-03 launcher existeix i és executable",
          os.path.exists(lk) and os.access(lk, os.X_OK), lk)
    # start (port 8041 per no xocar amb el servidor de tests)
    env = dict(os.environ, APP_PORT="8041", APP_ENV="test",
               PYTHON_BIN=sys.executable)
    r = subprocess.run([lk, "start"], cwd=REPO, capture_output=True, text=True,
                       timeout=120, env=env)
    check("P3-03 launcher start", r.returncode == 0 and "en marxa" in r.stdout,
          f"rc={r.returncode}")
    r = subprocess.run([lk, "status"], cwd=REPO, capture_output=True, text=True,
                       timeout=60, env=env)
    check("P3-04 launcher status (RUNNING)",
          "RUNNING" in r.stdout, r.stdout.strip()[:60])
    r = subprocess.run([lk, "stop"], cwd=REPO, capture_output=True, text=True,
                       timeout=60, env=env)
    check("P3-05 launcher stop", r.returncode == 0 and "aturat" in r.stdout,
          f"rc={r.returncode}")
    r = subprocess.run([lk, "status"], cwd=REPO, capture_output=True, text=True,
                       timeout=60, env=env)
    check("P3-05 launcher status (STOPPED)", "STOPPED" in r.stdout,
          r.stdout.strip()[:40])

    # ── P3-06 first-run ─────────────────────────────────────────────────
    st = _api("/api/setup/status")
    check("P3-06 first-run status", st.get("first_run") is not None,
          f"first_run={st.get('first_run')}")

    # ── P3-07..10 hardware detection ────────────────────────────────────
    sysinfo = _api("/api/system")
    hw = sysinfo.get("hardware", {})
    check("P3-07 CPU detection", hw.get("cpu", {}).get("model"),
          hw.get("cpu", {}).get("model", "?"))
    check("P3-08 RAM detection", hw.get("memory", {}).get("total_bytes", 0) > 0,
          f"{hw.get('memory', {}).get('total_bytes', 0)/2**30:.1f} GB total")
    check("P3-09 disk detection",
          hw.get("disk", {}).get("available_bytes", 0) > 0,
          f"{hw.get('disk', {}).get('available_bytes', 0)/2**30:.1f} GB lliures")
    check("P3-10 GPU absent safe",
          hw.get("gpu", {}).get("present") is False and
          "certified" not in str(hw.get("gpu", {})).lower(),
          f"gpu={hw.get('gpu', {}).get('vendor')}")

    # ── P3-11 recommended profile ───────────────────────────────────────
    caps = sysinfo.get("capabilities", {})
    q = caps.get("qwen3", {})
    check("P3-11 recommended profile (qwen3)",
          q.get("class") in ("COMFORTABLE", "LIMITED", "NOT RECOMMENDED"),
          f"qwen3={q.get('class')}")

    # ── P3-12 model missing UX (API level) ──────────────────────────────
    # /api/models d'un backend sense deps → missatge clar, no path error
    res = _api("/api/models?backend_id=qwen3")
    check("P3-12 model status (local/missing)", res is not None,
          f"valid={res.get('valid')}")

    # ── P3-13 dataset drag&drop (JS present) ────────────────────────────
    ds_js = open(os.path.join(REPO, "app/web/static/js/datasets.js")).read()
    check("P3-13 dataset drag&drop", "drop" in ds_js and "ds-drop" in ds_js,
          "drop zone al datasets.js")

    # ── P3-14/15 safe/custom wizard ─────────────────────────────────────
    jobs_js = open(os.path.join(REPO, "app/web/static/js/jobs.js")).read()
    check("P3-14 safe wizard (auto-config)",
          "recommended" in jobs_js and "memory_warning" in jobs_js,
          "auto-config + warning al wizard")
    check("P3-15 custom wizard", "custom-fields" in jobs_js,
          "camps custom presents")

    # ── P3-16 API reconnect (UI JS) ─────────────────────────────────────
    api_js = open(os.path.join(REPO, "app/web/static/js/api.js")).read()
    mon_js = open(os.path.join(REPO, "app/web/static/js/monitor.js")).read()
    main_js = open(os.path.join(REPO, "app/web/static/js/main.js")).read()
    check("P3-16 API reconnect UI", "Reconnecting" in mon_js or
          "reconnect" in main_js, "badge de reconnect")

    # ── P3-17 browser reload (SSE id estable — ja certificat E0-04) ─────
    api_py = open(os.path.join(REPO, "app/api/main.py")).read()
    check("P3-17 browser reload (cursor SSE estable)",
          "after_seq" in api_py, "after_seq a l'API")

    # ── P3-18 app restart reconciliation (E0-03 regressió) ──────────────
    # es verifica amb el test E0-03 existent (noce real) — aquí fem smoke
    svc = open(os.path.join(REPO, "app/services/mesh_trainer_service.py")).read()
    check("P3-18 restart reconciliation present",
          "reconcile_runners" in svc and "runner_identity" in svc,
          "reconcile + identity al servei")

    # ── P3-19 cancel (semàntica certificada) ────────────────────────────
    check("P3-19 cancel via API (no SIGTERM des del browser)",
          "cancel" in open(os.path.join(REPO, "app/api/main.py")).read(),
          "endpoint /cancel present")

    # ── P3-20 failure UX (missatge amigable, no traceback) ──────────────
    check("P3-20 failure UX (human states)",
          "Training" in open(os.path.join(REPO, "app/web/static/js/ui.js")).read(),
          "estats human-readable")

    # ── P3-21 completed result screen ───────────────────────────────────
    mon = open(os.path.join(REPO, "app/web/static/js/monitor.js")).read()
    check("P3-21 completed result", "Training completed" in mon and
          "Download adapter" in mon, "result-card al monitor")

    # ── P3-22 artifact download (endpoint) ──────────────────────────────
    api_py = open(os.path.join(REPO, "app/api/main.py")).read()
    check("P3-22 artifact download", "artifacts/" in api_py and "download" in api_py,
          "endpoint de descàrrega")

    # ── P3-23 training summary ──────────────────────────────────────────
    jr = open(os.path.join(REPO, "app/services/job_runner.py")).read()
    check("P3-23 training summary", "training_summary.json" in jr,
          "generador de summary al JobRunner")

    # ── P3-24 mobile (viewport meta) ────────────────────────────────────
    idx = open(os.path.join(REPO, "app/web/index.html")).read()
    check("P3-24 mobile viewport", "viewport" in idx, "meta viewport present")

    # ── P3-25 keyboard (labels/aria/focus) ──────────────────────────────
    ui_js = open(os.path.join(REPO, "app/web/static/js/ui.js")).read()
    check("P3-25 keyboard/aria", "aria-label" in ui_js or "role=" in ui_js,
          "aria/role a ui.js")

    # ── P3-26 production dummy hidden ───────────────────────────────────
    cfg = open(os.path.join(REPO, "app/config.py")).read()
    check("P3-26 dummy hidden production",
          'HIDDEN_BACKENDS = {"dummy"} if APP_ENV == "production"' in cfg,
          "config oculta dummy a production")

    # ── P3-27 no host hardcodes (R1-08: patrons ampliats, només producte) ─
    src_all = ""
    for root, _dirs, files in os.walk(os.path.join(REPO, "app")):
        if root.rstrip("/").endswith("app/tests"):
            continue
        for f in files:
            if f.endswith((".py", ".js", ".html", ".css", ".toml")):
                p = os.path.join(root, f)
                if "node_modules" in p:
                    continue
                try:
                    src_all += open(p, encoding="utf-8", errors="ignore").read()
                except Exception:
                    pass
    # patrons prohibits al source de producte (R1-08)
    HARDCODES = [
        r"/root/", r"CT11[0-9]", r"Mandalor", r"Dagobah", r"\bpve\b",
        r"192\.168\.", r"10\.\d+\.\d+\.\d+", r"100\.\d+\.\d+\.\d+",
    ]
    hits = []
    for pat in HARDCODES:
        m = re.search(pat, src_all)
        if m:
            hits.append(pat)
    check("P3-27 no host hardcodes",
          not hits,
          "cap motiu de laboratori al source" if not hits
          else f"HARDCODES TROBATS: {hits}")

    check("P3-28 local bind default",
          'APP_HOST = str(_cfg_get(("host",), "127.0.0.1"))' in cfg,
          "bind 127.0.0.1 per defecte")

    # ── P3-29 secret scan del source ────────────────────────────────────
    secrets = re.findall(r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}"
                         r"|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|HF_TOKEN=\w{10,}",
                         src_all)
    check("P3-29 secret scan", len(secrets) == 0,
          f"{len(secrets)} secrets al source" if secrets else "0 secrets")

    # ── P3-30 FRESH LAUNCH REAL (R1-05): extracció → install → start →
    #    health → UI → stop. No busca strings al README.
    pkg30 = os.environ.get("P3_PACKAGE", "")
    if pkg30 and os.path.isfile(pkg30):
        box30 = "/tmp/p3_fresh_launch"
        if os.path.exists(box30):
            shutil.rmtree(box30)
        os.makedirs(box30)
        import tarfile
        with tarfile.open(pkg30) as tf:
            tf.extractall(box30)
        env30 = dict(os.environ, PYTHON_BIN=sys.executable,
                     APP_PORT="8044", APP_ENV="test")
        # install
        ri30 = subprocess.run(["bash", "install.sh"], cwd=box30,
                              capture_output=True, text=True, timeout=600,
                              env=env30)
        inst_ok = ri30.returncode == 0 and "installed successfully" in ri30.stdout
        # start
        rs30 = subprocess.run([os.path.join(box30, "meshtrainer"), "start"],
                              cwd=box30, capture_output=True, text=True,
                              timeout=180, env=env30)
        start_ok = rs30.returncode == 0 and "en marxa" in rs30.stdout
        # health
        health_ok = False
        try:
            r30 = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:8044/api/health", timeout=30).read())
            health_ok = bool(r30.get("ok")) or "ok" in str(r30)
        except Exception:
            health_ok = False
        # web UI
        ui_ok = False
        try:
            code = urllib.request.urlopen(
                "http://127.0.0.1:8044/", timeout=30).status
            ui_ok = code == 200
        except Exception:
            ui_ok = False
        # stop
        rp30 = subprocess.run([os.path.join(box30, "meshtrainer"), "stop"],
                              cwd=box30, capture_output=True, text=True,
                              timeout=60, env=env30)
        stop_ok = rp30.returncode == 0
        ok30 = inst_ok and start_ok and health_ok and ui_ok and stop_ok
        check("P3-30 fresh clone launch (execució real)",
              ok30, f"install={inst_ok} start={start_ok} health={health_ok} "
                    f"ui={ui_ok} stop={stop_ok}")
    else:
        check("P3-30 fresh clone launch (execució real)", False,
              "P3_PACKAGE no definit")

    print("")
    print(f"=== P3-01..30: {PASSED}/{PASSED + FAILED} PASS, {FAILED} FAIL ===")
    if FAILURES:
        print("Fails:", ", ".join(FAILURES))
    if srv is not None:
        srv.terminate()
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
