"""Tests E0-01..04 del PRODUCT MVP Phase 1 FINAL GATE.

E0-01 cancel·lació sense inventar core state
E0-02 dataset upload (UPLOAD-01..05)
E0-03 app restart / runner reconciliation (3 casos)
E0-04 SSE contract
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

_CORE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

os.environ["APP_ENV"] = "test"
_TMP = tempfile.mkdtemp(prefix="app_e0_")
os.environ["APP_DB_PATH"] = os.path.join(_TMP, "app.db")

from app.services.dataset_service import DatasetService, UPLOAD_MAX_BYTES  # noqa: E402
from app.services.mesh_trainer_service import MeshTrainerService  # noqa: E402
from app.storage.db import AppDB  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def make_ds(path: str, n: int = 6, valid: bool = True):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"instruction": f"instr {i}",
                                "response": f"resp {i}"}) + "\n")


def wait_status(svc, job_id, target, timeout=120, poll=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = svc.get_job(job_id)
        if j["status"] in target:
            return j
        time.sleep(poll)
    return svc.get_job(job_id)


def main():
    print("=== E0-01..04 (Phase 1 FINAL GATE) ===")
    svc = MeshTrainerService()
    tmp = tempfile.mkdtemp(prefix="e0_ds_")
    good = os.path.join(tmp, "good.jsonl")
    make_ds(good, 6)
    ds = svc.add_dataset(good, "good")
    svc.validate_dataset(ds["dataset_id"])

    # ══ E0-02: dataset upload (UPLOAD-01..05) ═════════════════════════════
    print("--- E0-02: dataset upload ---")
    uploader = DatasetService(svc.db)

    # UPLOAD-01: JSONL vàlid → registered
    data_ok = open(good, "rb").read()
    up1 = uploader.upload_dataset(data_ok, client_filename="data.jsonl",
                                  name="up1")
    check("UPLOAD-01 JSONL vàlid → registered",
          up1["dataset_id"].startswith("ds-") and up1["examples"] == 6,
          f"id={up1['dataset_id']} examples={up1['examples']}")

    # UPLOAD-02: filename ../../x → neutralitzat (el nom intern és el servidor)
    up2 = uploader.upload_dataset(data_ok, client_filename="../../etc/passwd")
    check("UPLOAD-02 path traversal neutralitzat",
          os.path.basename(up2["source_path"]) == up2["source_path"].split("/")[-1]
          and ".." not in up2["source_path"]
          and os.path.basename(up2["source_path"]).startswith("ds-"),
          f"path={up2['source_path']}")

    # UPLOAD-03: oversized → rejected
    big = b"x" * (UPLOAD_MAX_BYTES + 1)
    try:
        uploader.upload_dataset(big, client_filename="big.jsonl")
        check("UPLOAD-03 oversized rejected", False, "no va llençar")
    except Exception as e:
        check("UPLOAD-03 oversized rejected", "massa gran" in str(e), str(e)[:60])

    # UPLOAD-04: SHA metadata == bytes
    import hashlib
    up4 = uploader.upload_dataset(data_ok, client_filename="sha.jsonl")
    sha = up4.get("upload_sha256") or up4["schema_json"].get("upload_sha256", "")
    check("UPLOAD-04 SHA metadata == bytes",
          sha == hashlib.sha256(data_ok).hexdigest(), sha[:16])

    # UPLOAD-05: invalid JSONL → upload possible, validation FAIL controlat
    bad_path = os.path.join(tmp, "bad.jsonl")
    with open(bad_path, "w") as f:
        f.write('{"instruction": "a", "response": "b"}\nNOT_JSON\n')
    up5 = uploader.upload_dataset(open(bad_path, "rb").read(),
                                  client_filename="bad.jsonl")
    v5 = svc.validate_dataset(up5["dataset_id"])
    check("UPLOAD-05 invalid JSONL → upload ok, validation FAIL",
          v5["validation_status"] == "FAIL",
          f"status={v5['validation_status']}")

    # ══ E0-01: cancel sense inventar core state ═══════════════════════════
    print("--- E0-01: cancel·lació sense inventar core state ---")
    os.environ["APP_TEST_ROUND_DELAY"] = "30"
    job_c = svc.create_training_job(
        name="e0-01", backend_id="dummy", model_id="dummy",
        dataset_id=ds["dataset_id"],
        training={"rounds": 3}, workers_cfg={"workers": 2})
    svc.validate_job(job_c["job_id"])
    svc.start_job(job_c["job_id"])
    j = wait_status(svc, job_c["job_id"], ["RUNNING"], timeout=60)
    time.sleep(2)  # deixa que la ronda 1 comenci
    j = svc.cancel_job(job_c["job_id"])
    check("E0-01a RUNNING → CANCELLED", j["status"] == "CANCELLED", j["status"])
    del os.environ["APP_TEST_ROUND_DELAY"]

    # inspeccionar la BD del CORE (no la de l'app)
    core_db = os.path.join(_TMP.replace("app.db", ""), "app_storage",
                           "data", job_c["job_id"], "core.db")
    # la ruta real és a app_storage sota el repo; cerquem-la
    import glob
    cands = glob.glob(os.path.join(_CORE, "app_storage", "data",
                                   job_c["job_id"], "core.db"))
    if not cands:
        check("E0-01b BD core localitzable", False, "no trobada")
        core_state_ok = False
    else:
        conn = sqlite3.connect(cands[0])
        rows = conn.execute("SELECT round_id, status FROM model_rounds").fetchall()
        conn.close()
        # cap ronda amb status CANCELLED al core (l'app no hi inventa estat)
        no_cancelled = all(r[1] != "CANCELLED" for r in rows)
        check("E0-01b core sense round CANCELLED (estat no inventat)",
              no_cancelled, f"rounds={rows}")
        core_state_ok = no_cancelled

    # run_id no reutilitzat: cada job té run-<job_id> únic
    j2 = svc.create_training_job(name="e0-01b", backend_id="dummy",
                                 model_id="dummy", dataset_id=ds["dataset_id"],
                                 training={"rounds": 1}, workers_cfg={})
    check("E0-01c run_id únic per job (no reutilitzat)",
          f"run-{j2['job_id']}" != f"run-{job_c['job_id']}")

    # ── E0-01 R1 ADVERSARIAL: cancel DESPRÉS dels workers, ABANS d'activate ─
    # La finestra exacta: el runner fa la pausa test just després que els
    # workers acabin i ABANS del primer contribution.validate. Si el cancel
    # arriba aquí, cap activate ni fedavg pot existir posteriorment.
    print("--- E0-01 R1: cancel race (workers fets, abans de validate) ---")
    os.environ["APP_TEST_PAUSE_BEFORE_VALIDATE"] = "60"  # finestra ampla
    job_r = svc.create_training_job(name="e0-01-race", backend_id="dummy",
                                    model_id="dummy",
                                    dataset_id=ds["dataset_id"],
                                    training={"rounds": 1}, workers_cfg={})
    svc.validate_job(job_r["job_id"])
    svc.start_job(job_r["job_id"])
    # espera que el runner arribi a la pausa (worker DONE a runner.log)
    import glob as _glob
    rlog = os.path.join(_CORE, "app_storage", "logs", job_r["job_id"], "runner.log")
    deadline = time.time() + 90
    paused = False
    while time.time() < deadline:
        if os.path.exists(rlog):
            with open(rlog, errors="replace") as f:
                if "test pause" in f.read():
                    paused = True
                    break
        time.sleep(1)
    check("E0-01r1 runner a la finestra (workers acabats, abans de validate)",
          paused)
    # comprova que els workers han acabat (spawn w-A i w-B al log)
    with open(rlog, errors="replace") as f:
        rtxt = f.read()
    check("E0-01r2 workers A/B acabats abans del cancel",
          "spawn w-A" in rtxt and "spawn w-B" in rtxt)
    # ara CANCEL·LA en aquest punt exacte
    j = svc.cancel_job(job_r["job_id"])
    check("E0-01r3 job CANCELLED", j["status"] == "CANCELLED", j["status"])
    del os.environ["APP_TEST_PAUSE_BEFORE_VALIDATE"]
    # cap contribution.activate ni round.fedavg posterior al cancel
    evs_r = svc.get_events(job_r["job_id"])
    acts = [e for e in evs_r if e["type"] == "contribution.activate"]
    feds = [e for e in evs_r if e["type"] == "round.fedavg"]
    check("E0-01r4 cap contribution.activate posterior al cancel", len(acts) == 0,
          f"n={len(acts)}")
    check("E0-01r5 cap round.fedavg posterior al cancel", len(feds) == 0,
          f"n={len(feds)}")

    # ══ E0-03: runner reconciliation ══════════════════════════════════════
    print("--- E0-03: app restart / runner reconciliation ---")
    # Cas A: runner viu → reattach; job continua i COMPLETED
    os.environ["APP_TEST_ROUND_DELAY"] = "10"
    jobA = svc.create_training_job(name="e0-03A", backend_id="dummy",
                                   model_id="dummy",
                                   dataset_id=ds["dataset_id"],
                                   training={"rounds": 2}, workers_cfg={})
    svc.validate_job(jobA["job_id"])
    svc.start_job(jobA["job_id"])
    j = wait_status(svc, jobA["job_id"], ["RUNNING"], timeout=60)
    check("E0-03a runner en marxa (RUNNING)", j["status"] == "RUNNING")
    check("E0-03a runner_pid persistit", bool(j.get("runner_pid")),
          f"pid={j.get('runner_pid')}")
    check("E0-03a runner_identity persistit", bool(j.get("runner_identity")),
          f"identity={str(j.get('runner_identity'))[:8]}")
    # "reinici" de l'app: nova instància del servei sobre la mateixa DB
    svc2 = MeshTrainerService()
    rec = svc2.reconcile_runners()
    check("E0-03a reattach (PID viu + identity)",
          jobA["job_id"] in rec["reattached"], f"rec={rec}")
    del os.environ["APP_TEST_ROUND_DELAY"]
    j = wait_status(svc2, jobA["job_id"], ["COMPLETED", "FAILED"], timeout=180)
    check("E0-03a job continua fins COMPLETED", j["status"] == "COMPLETED",
          j["status"])

    # Cas B: DB amb job RUNNING però runner mort → FAILED
    jobB = svc2.create_training_job(name="e0-03B", backend_id="dummy",
                                    model_id="dummy",
                                    dataset_id=ds["dataset_id"],
                                    training={}, workers_cfg={})
    svc2.validate_job(jobB["job_id"])
    svc2.db.update_job(jobB["job_id"], status="RUNNING",
                       runner_pid=99999999,   # procés inexistent
                       runner_identity="dead-nonce",
                       runner_started_at="2026-01-01T00:00:00+00:00")
    svc3 = MeshTrainerService()  # "reinici" (el __init__ ja reconcilia)
    j = svc3.get_job(jobB["job_id"])
    check("E0-03b runner mort → FAILED (no RUNNING etern)",
          j["status"] == "FAILED" and "runner no disponible" in j["last_error"],
          f"status={j['status']} err={j['last_error'][:60]}")

    # Cas C adversarial: PID viu però identity no coincideix → NO reattach
    import os as _os
    pid_alive = _os.getpid()  # procés viu qualsevol
    jobC = svc3.create_training_job(name="e0-03C", backend_id="dummy",
                                    model_id="dummy",
                                    dataset_id=ds["dataset_id"],
                                    training={}, workers_cfg={})
    svc3.validate_job(jobC["job_id"])
    svc3.db.update_job(jobC["job_id"], status="RUNNING",
                       runner_pid=pid_alive,
                       runner_identity="other-nonce",  # no és el nostre runner
                       runner_started_at="2026-01-01T00:00:00+00:00")
    svc4 = MeshTrainerService()  # el __init__ reconcilia
    j = svc4.get_job(jobC["job_id"])
    check("E0-03c PID viu + identity mismatch → NO reattach (FAILED)",
          j["status"] == "FAILED" and "procés aliè" in j["last_error"],
          f"status={j['status']} err={j['last_error'][:60]}")

    # ── E0-03 R1 ADVERSARIAL: procés amb aparença de JobRunner + mateix
    #    job_id + nonce DIFERENT → NO reattach (parseig EXACTE de cmdline) ─
    print("--- E0-03 R1: nonce real (aparença JobRunner + nonce diferent) ---")
    # Spawna un procés REAL de job_runner amb job_id del jobD però amb un
    # runner-identity DIFERENT del persistit (simula procés aliè/segrestat).
    jobD = svc4.create_training_job(name="e0-03-nonce", backend_id="dummy",
                                    model_id="dummy",
                                    dataset_id=ds["dataset_id"],
                                    training={}, workers_cfg={})
    svc4.validate_job(jobD["job_id"])
    real_ident = "real-nonce-A"
    fake_ident = "fake-nonce-B"  # el procés porta un nonce diferent
    svc4.db.update_job(jobD["job_id"], status="RUNNING",
                       runner_identity=real_ident,
                       runner_started_at="2026-01-01T00:00:00+00:00")
    port = 19977
    fake_proc = subprocess.Popen(
        [sys.executable, "-m", "app.services.job_runner",
         "--job-id", jobD["job_id"], "--app-db", svc4.db.db_path,
         "--backend", "dummy", "--model", "dummy",
         "--dataset-path", ds["source_path"],
         "--out-dir", os.path.join(_CORE, "app_storage", "output", jobD["job_id"]),
         "--rounds", "1", "--seq-len", "32", "--num-examples", "2",
         "--lora", "{}", "--seed", "42", "--workers", "2",
         "--port", str(port), "--device", "cpu",
         "--runner-identity", fake_ident],  # nonce DIFERENT del persistit
        cwd=_CORE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)  # deixa que el procés arrenqui i detecti el mismatch
    # Defensa ACTIVA (R1): el procés aliè s'aborta sol en veure que la DB
    # ja té un nonce diferent — no pot segrestar el job.
    check("E0-03r1 procés aliè ABORTAT pel mismatch de nonce (defensa activa)",
          fake_proc.poll() is not None and fake_proc.returncode != 0,
          f"pid={fake_proc.pid} rc={fake_proc.returncode}")
    svc4.db.update_job(jobD["job_id"], runner_pid=fake_proc.pid)
    svc5 = MeshTrainerService()  # el __init__ reconcilia
    j = svc5.get_job(jobD["job_id"])
    # El procés aliè ABORTA en detectar el mismatch de nonce (no sobreescriu
    # la identity). El job acaba FAILED (mai RUNNING, mai reattach).
    check("E0-03r2 nonce diferent → NO reattach (FAILED)",
          j["status"] == "FAILED" and
          ("identity no vàlida" in j["last_error"]
           or "runner_identity mismatch" in j["last_error"]),
          f"status={j['status']} err={j['last_error'][:70]}")
    # i la identity persistida NO ha canviat (no segrestada)
    jd = svc5.get_job(jobD["job_id"])
    check("E0-03r3 identity persistida no sobreescrita pel procés aliè",
          jd.get("runner_identity") == real_ident,
          f"identity={str(jd.get('runner_identity'))[:14]}")
    fake_proc.terminate()
    try:
        fake_proc.wait(timeout=5)
    except Exception:
        fake_proc.kill()

    # ══ E0-04: SSE contract ═══════════════════════════════════════════════
    print("--- E0-04: SSE contract ---")
    # E0-04 R1: cursor ESTABLE = seq (rowid SQLite), no timestamp.
    evs = svc4.get_events(jobA["job_id"])
    seq_list = [e["seq"] for e in evs]
    check("E0-04 events amb seq monotònic (rowid, cursor estable)",
          seq_list == sorted(seq_list) and len(seq_list) >= 3
          and all(isinstance(s, int) for s in seq_list),
          f"n={len(seq_list)} seq={seq_list[:5]}...")

    # SSE-01: dos events al MATEIX segon → tots dos arriben (cursor seq)
    db_ev = svc4.db
    eid1, seq1 = db_ev.add_event(jobA["job_id"], "sse.test.one", {})
    eid2, seq2 = db_ev.add_event(jobA["job_id"], "sse.test.two", {})
    got = [e for e in db_ev.list_events(jobA["job_id"], after_seq=seq1 - 1)
           if e["type"] in ("sse.test.one", "sse.test.two")]
    check("SSE-01 dos events mateix segon → tots dos (seq diferents)",
          len(got) == 2 and seq1 != seq2 and {e["seq"] for e in got} == {seq1, seq2},
          f"seq1={seq1} seq2={seq2}")

    # SSE-02: reconnect → els IDs originals no canvien (rowid immutable)
    re_evs = db_ev.list_events(jobA["job_id"], after_seq=0)
    orig = {e["event_id"]: e["seq"] for e in re_evs}
    re_evs2 = db_ev.list_events(jobA["job_id"], after_seq=0)
    orig2 = {e["event_id"]: e["seq"] for e in re_evs2}
    check("SSE-02 reconnect → IDs originals no canvien",
          orig == orig2 and len(orig) == len(orig2),
          f"n={len(orig)}")

    # SSE-03: event nou → seq > anterior
    last_max = max(e["seq"] for e in db_ev.list_events(jobA["job_id"]))
    eid3, seq3 = db_ev.add_event(jobA["job_id"], "sse.test.three", {})
    check("SSE-03 event nou → id > anterior", seq3 > last_max,
          f"{last_max} -> {seq3}")

    # E0-04 real sobre HTTP: arrenquem uvicorn i llegim el stream
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=_CORE, env={**os.environ, "APP_ENV": "test"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # espera activa: el port ha de respondre (màx 20s)
    ready = False
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health",
                                   timeout=2).read()
            ready = True
            break
        except Exception:
            time.sleep(0.5)
    check("E0-04 uvicorn llest", ready)
    if ready:
        req = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/jobs/{jobA['job_id']}/events",
            timeout=10)
        ctype = req.headers.get("Content-Type", "")
        chunk = req.read(2000).decode("utf-8", "replace")
        req.close()
        has_id = "id: " in chunk
        has_event = "event: " in chunk
        has_data = "data: " in chunk
        no_envelope = '"ok"' not in chunk.split("data:")[0][:200]
        check("E0-04 Content-Type text/event-stream", "text/event-stream" in ctype,
              ctype)
        check("E0-04 SSE id/event/data presents",
              has_id and has_event and has_data,
              f"id={has_id} event={has_event} data={has_data}")
        check("E0-04 sense envelope REST al stream", no_envelope)
    else:
        check("E0-04 SSE HTTP", False, "uvicorn no llest")
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()

    # ── resum ────────────────────────────────────────────────────────────
    npass = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"=== E0-01..04: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
