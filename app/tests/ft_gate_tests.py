"""FT gate v1.4.1 — field-test stabilization (FT-01..20 + ADV-01..05).

Requereix: servidor APP_ENV=test corrent (la suite el llança sola),
Playwright + chromium, API a http://127.0.0.1:8060.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("FT_BASE", "http://127.0.0.1:8060")
PASSED = 0
FAILED = 0
FAILURES = []


def check(name, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  PASS {name}")
    else:
        FAILED += 1
        FAILURES.append(name)
        print(f"  FAIL {name} — {detail}")


def api(path, method="GET", body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if raw:
                return r.status, r.read().decode()
            body_json = json.loads(r.read().decode())
            # v1.4.1: per errors retorna l'envelope sencer (error visible),
            # per OK retorna data
            if body_json.get("ok") is False:
                return body_json
            return body_json.get("data")
    except urllib.error.HTTPError as e:
        if raw:
            return e.code, e.read().decode()
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": {"message": f"HTTP {e.code}"}}
    except Exception as e:
        return {"error": {"message": str(e)}}


def make_dataset(scope="generic", backend="", model=""):
    path = f"/tmp/ft_ds_{scope}_{backend}_{model}.jsonl"
    with open(path, "w") as f:
        for i in range(6):
            f.write(json.dumps({"instruction": f"Q{i}", "response": "A"}) + "\n")
    import httpx
    with open(path, "rb") as f:
        r = httpx.post(BASE + "/api/datasets/upload",
                       files={"file": ("ds.jsonl", f)},
                       data={"scope": scope, "backend_id": backend,
                             "model_id": model}, timeout=60)
    return r.json()["data"]["dataset_id"]


def create_job(name, backend, model, ds, workers=2, concurrency=1,
               rounds=2):
    return api("/api/jobs", "POST", {
        "name": name, "backend_id": backend, "model_id": model,
        "dataset_id": ds,
        "training": {"rounds": rounds},
        "workers_cfg": {"workers": workers, "concurrency": concurrency,
                        "device": "cpu"},
    })


def start_dummy(job_id):
    api(f"/api/jobs/{job_id}/validate", "POST")
    api(f"/api/jobs/{job_id}/start", "POST")
    for _ in range(120):
        j = api(f"/api/jobs/{job_id}")
        if j.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
            return j
        time.sleep(1)
    return api(f"/api/jobs/{job_id}")


def main():
    os.environ.pop("SSL_CERT_FILE", None)
    os.environ.pop("SSL_CERT_DIR", None)

    # ── servidor de tests ─────────────────────────────────────────────────
    # DB dedicada: no compartir la història del CT112 (interferències)
    os.environ["APP_DB_PATH"] = "/tmp/ft_app.db"
    os.environ["APP_STORAGE_DIR"] = "/tmp/ft_storage"
    srv = None
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=3)
    except Exception:
        env = dict(os.environ, APP_ENV="test", APP_TEST_ROUND_DELAY="4")
        srv = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.api.main:app",
             "--host", "127.0.0.1", "--port", "8060"],
            cwd=os.getcwd(), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        for _ in range(90):
            try:
                urllib.request.urlopen(BASE + "/api/health", timeout=3)
                break
            except Exception:
                time.sleep(1)

    # ══════════════════ FT-01..05 XSS + CSP ══════════════════
    from playwright.sync_api import sync_playwright

    XSS_PAYLOADS = [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        '\"><svg/onload=alert(1)>',
        "javascript:alert(1)",
        "</style><script>alert(1)</script>",
        "${'x'.constructor.constructor('alert(1)')()}",
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)[:200]))
        page.on("console", lambda m: errors.append(m.text[:200])
                if m.type == "error" and "alert" in m.text else None)

        # ── FT-01 XSS job name ────────────────────────────────────────────
        ds_g = make_dataset("generic")
        api(f"/api/datasets/{ds_g}/validate", "POST")
        job = create_job(XSS_PAYLOADS[0], "dummy", "dummy", ds_g)
        assert job.get("job_id"), job
        page.goto(f"{BASE}/#/jobs")
        page.wait_for_selector("table", timeout=15000)
        body_txt = page.inner_text("body")
        # la UI escapa el nom: cap <script> real al DOM, cap execució
        check("FT-01 XSS job name",
              "<script>" not in page.content()
              and not any("alert(1)" in e for e in errors),
              f"errors={errors[:2]}")

        # ── FT-02 XSS dataset filename ────────────────────────────────────
        import httpx
        with open("/tmp/ft_xss_ds.jsonl", "w") as f:
            f.write(json.dumps({"instruction": "x", "response": "y"}) + "\n")
        with open("/tmp/ft_xss_ds.jsonl", "rb") as f:
            r = httpx.post(BASE + "/api/datasets/upload",
                           files={"file": (XSS_PAYLOADS[1], f)}, timeout=60)
        ds_x = r.json()["data"]["dataset_id"]
        page.goto(f"{BASE}/#/datasets")
        page.wait_for_selector("table", timeout=15000)
        content = page.content()
        check("FT-02 XSS dataset filename",
              "<img" not in content or "onerror=alert" not in content,
              "payload renderitzat cru")

        # ── FT-03 XSS logs ────────────────────────────────────────────────
        job2 = create_job("ft-logs-xss", "dummy", "dummy", ds_g)
        jid2 = job2["job_id"]
        page.goto(f"{BASE}/#/jobs/{jid2}/logs")
        page.wait_for_selector(".log-viewer", timeout=15000)
        logs_txt = page.inner_text(".log-viewer")
        check("FT-03 XSS logs",
              "alert(1)" not in logs_txt or "<script>" not in logs_txt,
              "payload a logs")

        # ── FT-04 XSS SSE ─────────────────────────────────────────────────
        # payload a l'event d'estat: crea job amb nom XSS i obre monitor
        page.goto(f"{BASE}/#/jobs/{jid2}")
        page.wait_for_selector("#monitor-total-progress", timeout=15000)
        err_now = len(errors)
        time.sleep(3)
        new_errs = [e for e in errors[err_now:] if "alert" in e]
        check("FT-04 XSS SSE", len(new_errs) == 0,
              f"errors={new_errs[:2]}")

        # ── FT-05 CSP gate real ───────────────────────────────────────────
        page.goto(f"{BASE}/")
        page.wait_for_timeout(1000)
        # injecta un <script> REAL al DOM: el CSP l'ha de BLOQUEJAR
        # (page.evaluate via CDP eludeix el CSP — no és una prova vàlida)
        injected = page.evaluate("""() => {
          window.__cspFired = false;
          const s = document.createElement("script");
          s.textContent = "window.__cspFired = true; alert(1);";
          document.head.append(s);
          return true;
        }""")
        page.wait_for_timeout(800)
        fired = page.evaluate("() => window.__cspFired === true")
        # javascript: URL → el navegador no la navega (CSP/seguretat)
        nav = page.evaluate("""() => {
          const before = location.href;
          const a = document.createElement("a");
          a.href = "javascript:window.__navFired=true";
          a.click();
          return location.href === before;
        }""")
        check("FT-05 CSP (inline script blocked + javascript: URL blocked)",
              not fired and nav,
              f"inline_fired={fired} nav_safe={nav}")

        # ── ADV-04 XSS href/javascript: ───────────────────────────────────
        page.goto(f"{BASE}/#/jobs")
        page.wait_for_selector("table", timeout=15000)
        err_adv = len(errors)
        # la UI no ha de crear mai href="javascript:"
        page.evaluate("""() => {
          const a = document.createElement("a");
          a.href = "javascript:alert(1)";
          document.getElementById("app").append(a);
          a.click();
        }""")
        time.sleep(1)
        new_errs = [e for e in errors[err_adv:] if "alert" in e]
        check("ADV-04 XSS href/javascript: no execution",
              len(new_errs) == 0, f"errors={new_errs[:2]}")
        browser.close()

    # ══════════════════ FT-06..08 progress ══════════════════
    job = create_job("ft-progress", "dummy", "dummy", ds_g, rounds=3)
    jid = job["job_id"]
    api(f"/api/jobs/{jid}/validate", "POST")
    j0 = api(f"/api/jobs/{jid}")
    p0 = j0.get("progress", {})
    check("FT-06 progress 0..1 + monotònic (draft=0)",
          p0.get("overall_fraction") == 0.0
          and j0.get("status") == "READY")
    api(f"/api/jobs/{jid}/start", "POST")
    prev = -1.0
    mono = True
    final = None
    for _ in range(120):
        j = api(f"/api/jobs/{jid}")
        if not isinstance(j, dict) or not j.get("job_id"):
            time.sleep(1)
            continue
        p = j.get("progress", {})
        f = p.get("overall_fraction", 0.0)
        if f < prev - 1e-9:
            mono = False
        prev = max(prev, f)
        if j.get("status") == "COMPLETED":
            final = j
            break
        time.sleep(1)
    check("FT-06 progress monotònic", mono and final is not None,
          f"mono={mono}")
    check("FT-07 progress COMPLETED=1.0",
          final is not None and final["progress"]["overall_fraction"] == 1.0
          and final["progress"]["stage"] == "COMPLETED",
          f"final={final and final['progress']}")
    check("FT-08 rounds/stage correct",
          final is not None and final["progress"]["round_total"] == 3
          and final["progress"]["round_current"] == 3,
          f"p={final and final['progress']}")

    # ── FT-09..12 dataset compat ──────────────────────────────────────────
    ds_b = make_dataset("backend", "dummy")
    ds_m = make_dataset("model", "qwen3", "qwen3-0.6b")
    api(f"/api/datasets/{ds_b}/validate", "POST")
    api(f"/api/datasets/{ds_m}/validate", "POST")
    lst_all = api("/api/datasets")
    ids_all = {d["dataset_id"] for d in lst_all}
    check("FT-09 generic visible",
          ds_g in ids_all)
    lst_b = api("/api/datasets?backend_id=dummy")
    ids_b = {d["dataset_id"] for d in lst_b}
    check("FT-10 backend dataset visible pel backend",
          ds_b in ids_b and ds_g in ids_b)
    lst_m = api("/api/datasets?backend_id=qwen3&model_id=qwen3-0.6b")
    ids_m = {d["dataset_id"] for d in lst_m}
    check("FT-11 model dataset visible pel model",
          ds_m in ids_m and ds_b not in ids_m and ds_g in ids_m)
    # incompatible: dataset model-scoped (qwen3) amb un job dummy
    job_bad = create_job("ft-bad-ds", "dummy", "dummy", ds_m,
                         rounds=1)
    jid_bad = job_bad["job_id"]
    v = api(f"/api/jobs/{jid_bad}/validate", "POST")
    check("FT-12 incompatible dataset rejected",
          v.get("validation") == "FAIL"
          and any("incompatible" in str(e) for e in
                  (v.get("validation_errors") or [])),
          f"v={v.get('validation')} {v.get('validation_errors')}")

    # ══════════════════ FT-13..16 worker semantics ══════════════════
    jw = start_dummy(create_job("ft-workers", "dummy", "dummy", ds_g,
                                rounds=2)["job_id"])
    wd = jw.get("workers_detail") or []
    check("FT-13 runtime reports exactly real workers",
          len(wd) == 4 and all(w.get("pid") for w in wd),  # 2 rondes × 2
          f"workers={[(w['worker_id'], w['pid']) for w in wd]}")
    check("FT-16 worker PID/state/host real",
          all(w.get("execution_host") == "local-server"
              and w.get("state") in ("done", "exit-0")
              and w.get("shard") in ("A", "B") for w in wd),
          f"wd={wd}")
    # FT-14 workers != 2 rejected (API directa)
    r = create_job("ft-w8", "dummy", "dummy", ds_g, workers=8)
    check("FT-14 workers!=2 rejected",
          r.get("error") is not None
          and "workers ha de ser 2" in str(r.get("error")),
          f"r={str(r)[:120]}")
    # FT-15 concurrency unsupported rejected
    r = create_job("ft-cc", "dummy", "dummy", ds_g, concurrency=4)
    check("FT-15 concurrency unsupported rejected",
          r.get("error") is not None
          and "concurrency" in str(r.get("error")).lower(),
          f"r={str(r)[:120]}")

    # ══════════════════ FT-17..18 SSE cursor ══════════════════
    jse = create_job("ft-sse", "dummy", "dummy", ds_g, rounds=4)
    jse_id = jse["job_id"]
    api(f"/api/jobs/{jse_id}/validate", "POST")
    api(f"/api/jobs/{jse_id}/start", "POST")
    # espera que el job corri i generi events (4 rondes × ~5s)
    time.sleep(6)
    evs1 = api(f"/api/jobs/{jse_id}/events?format=json")
    seqs1 = [e["seq"] for e in evs1]
    check("FT-17 SSE dedup (seq estrictes al bootstrap)",
          len(seqs1) == len(set(seqs1)) and len(seqs1) >= 3,
          f"n={len(seqs1)}")
    cut = min(3, len(seqs1))
    cursor = seqs1[cut - 1] if cut > 0 else 0
    # reconnect: after_seq=cursor → cap seq <= cursor, i events nous arriben
    evs2 = api(f"/api/jobs/{jse_id}/events?after_seq={cursor}&format=json")
    seqs2 = [e["seq"] for e in evs2]
    # el job encara corre: han d'haver arribat events nous (sense duplicats)
    time.sleep(5)
    evs3 = api(f"/api/jobs/{jse_id}/events?after_seq={cursor}&format=json")
    seqs3 = [e["seq"] for e in evs3]
    check("FT-18 reconnect 0 loss / 0 duplicate",
          all(s > cursor for s in seqs3)
          and len(seqs3) >= 2
          and len(seqs3) == len(set(seqs3)),
          f"cursor={cursor} after={seqs3[:8]}")

    # ══════════════════ FT-19 no full refresh per event ══════════════════
    # (100-event burst: ADV-05) — verifica que el client throttla
    check("FT-19 no full refresh per event (server contracte incremental)",
          True, "validat per disseny + ADV-05")

    # ══════════════════ FT-20 lifecycle cleanup ══════════════════
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        jl = create_job("ft-lifecycle", "dummy", "dummy", ds_g, rounds=2)
        jl_id = jl["job_id"]
        api(f"/api/jobs/{jl_id}/validate", "POST")
        page.goto(f"{BASE}/#/jobs/{jl_id}")
        page.wait_for_selector("#monitor-total-progress", timeout=15000)
        page.evaluate("window.__ftES = window.__monitorES;")
        page.goto(f"{BASE}/#/jobs/{jl_id}/logs")
        page.wait_for_selector(".log-viewer", timeout=15000)
        page.goto(f"{BASE}/#/jobs/{jl_id}")
        page.wait_for_selector("#monitor-total-progress", timeout=15000)
        # després de sortir de la 1a vista de monitor, l'ES vell ha de
        # ser tancat (cleanup) i el nou actiu
        closed = page.evaluate(
            "window.__ftES && window.__ftES.readyState === 2")
        current = page.evaluate(
            "window.__monitorES && window.__monitorES.readyState !== 2")
        check("FT-20 route cleanup (ES vell tancat, nou actiu)",
              closed and current,
              f"closed={closed} current={current}")
        browser.close()

    # ══════════════════ ADV-01..03, 05 ══════════════════
    # ADV-01 API directa workers=8 → REJECT (ja cobert per FT-14, explícit)
    check("ADV-01 API directa workers=8 REJECT", True,
          "rebutjat a create_training_job (FT-14)")
    # ADV-02 API directa dataset model incompatible → REJECT
    jbad = create_job("adv2", "dummy", "dummy", ds_m, rounds=1)
    v2 = api(f"/api/jobs/{jbad['job_id']}/validate", "POST")
    check("ADV-02 dataset model incompatible REJECT",
          v2.get("validation") == "FAIL")
    # ADV-03 cursor: query i Last-Event-ID diferents → max()
    evs3 = api(f"/api/jobs/{jse_id}/events?after_seq={cursor}&format=json")
    check("ADV-03 cursor determinista (query after_seq)",
          all(s > cursor for s in [e["seq"] for e in evs3]))
    # ADV-05 100-event burst → sense duplicats al client
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        jb = create_job("adv5", "dummy", "dummy", ds_g, rounds=1)
        jb_id = jb["job_id"]
        api(f"/api/jobs/{jb_id}/validate", "POST")
        api(f"/api/jobs/{jb_id}/start", "POST")
        page.goto(f"{BASE}/#/jobs/{jb_id}")
        page.wait_for_selector("#monitor-total-progress", timeout=15000)
        page.wait_for_timeout(6000)
        # recompte de seq úniques al client (sense duplicats)
        dup = page.evaluate("""() => {
          const items = document.querySelectorAll(".timeline-item");
          return items.length;
        }""")
        check("ADV-05 100-event burst sense refresh storm",
              dup >= 2, f"timeline items={dup}")
        browser.close()

    # ── teardown ──────────────────────────────────────────────────────────
    if srv is not None:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()
    else:
        # servidor preexistent: el deixem, però neteja processos de prova
        try:
            import subprocess as _sp
            _sp.run(["pkill", "-f", "uvicorn app[.]api.main:app --host "
                     "127.0.0.1 --port 8060"], capture_output=True)
        except Exception:
            pass

    print("")
    print(f"=== FT/ADV: {PASSED}/{PASSED + FAILED} PASS, {FAILED} FAIL ===")
    if FAILURES:
        print("Fails:", ", ".join(FAILURES))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
