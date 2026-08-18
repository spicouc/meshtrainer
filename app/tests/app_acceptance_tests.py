"""Acceptance tests de producte APP-01..15 (Fase 1).

Execució: CT112 amb /opt/qwen3-venv/bin/python (dummy backend, ràpid).
APP-14 (core inalterat) es verifica al host contra el tarball b146723
(no depèn de git ni del CT).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

_CORE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

# Mode test: dummy visible
os.environ["APP_ENV"] = "test"
os.environ["APP_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="app_test_"), "app.db")

from app.services.mesh_trainer_service import MeshTrainerService  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def make_dataset(path: str, n: int = 6, valid: bool = True):
    with open(path, "w", encoding="utf-8") as f:
        if valid:
            for i in range(n):
                f.write(json.dumps({"instruction": f"instr {i}",
                                    "response": f"resp {i}"}, ensure_ascii=False) + "\n")
        else:
            f.write('{"instruction": "ok", "response": "ok"}\n')
            f.write("NOT_JSON_LINE\n")


def wait_status(svc, job_id, target, timeout=120, poll=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = svc.get_job(job_id)
        if j["status"] in target:
            return j
        time.sleep(poll)
    return svc.get_job(job_id)


def main():
    print("=== APP ACCEPTANCE (Fase 1) ===")
    svc = MeshTrainerService()

    # ── APP-02/03: datasets ──────────────────────────────────────────────
    tmp = tempfile.mkdtemp(prefix="app_ds_")
    good = os.path.join(tmp, "good.jsonl")
    bad = os.path.join(tmp, "bad.jsonl")
    make_dataset(good, 6, valid=True)
    make_dataset(bad, 6, valid=False)

    ds_bad = svc.add_dataset(bad, "bad")
    ds_bad_v = svc.validate_dataset(ds_bad["dataset_id"])
    check("APP-02 invalid dataset rejected", ds_bad_v["validation_status"] == "FAIL",
          f"status={ds_bad_v['validation_status']}")

    ds_good = svc.add_dataset(good, "good")
    ds_good_v = svc.validate_dataset(ds_good["dataset_id"])
    check("APP-03 valid dataset accepted", ds_good_v["validation_status"] == "PASS",
          f"status={ds_good_v['validation_status']} examples={ds_good_v['examples']}")

    # ── APP-04/05: backends selectables ──────────────────────────────────
    bks = {b.id: b for b in svc.discover_backends()}
    check("APP-04 qwen backend selectable", "qwen3" in bks)
    check("APP-05 minicpm backend selectable", "minicpm5" in bks)

    # ── APP-01: create job ───────────────────────────────────────────────
    job = svc.create_training_job(
        name="test-job", backend_id="dummy", model_id="dummy",
        dataset_id=ds_good["dataset_id"],
        training={"lora_rank": 8, "lora_alpha": 16, "lora_dropout": 0.05,
                  "learning_rate": 1e-4, "rounds": 2, "max_seq_len": 32},
        workers_cfg={"workers": 2})
    check("APP-01 create job", job["status"] == "DRAFT", f"id={job['job_id']}")
    job_id = job["job_id"]

    # ── APP-07: cannot start invalid job ─────────────────────────────────
    job_inv = svc.create_training_job(
        name="invalid-job", backend_id="dummy", model_id="dummy",
        dataset_id=ds_bad["dataset_id"],
        training={"rounds": 1}, workers_cfg={"workers": 2})
    try:
        svc.start_job(job_inv["job_id"])
        check("APP-07 cannot start invalid job", False, "start permès amb dataset FAIL")
    except ValueError as e:
        check("APP-07 cannot start invalid job", True, str(e)[:60])

    # ── APP-06: start valid job → RUNNING → COMPLETED ───────────────────
    svc.validate_job(job_id)
    j = svc.get_job(job_id)
    check("validate -> READY", j["status"] == "READY", j["status"])
    svc.start_job(job_id)
    j = wait_status(svc, job_id, ["RUNNING", "STARTING"])
    check("APP-06a start -> RUNNING", j["status"] in ("RUNNING", "STARTING"),
          j["status"])
    j = wait_status(svc, job_id, ["COMPLETED", "FAILED", "CANCELLED"], timeout=180)
    check("APP-06b job COMPLETED", j["status"] == "COMPLETED",
          f"status={j['status']} err={j['last_error'][:80]}")

    # ── APP-08: monitoring updates ───────────────────────────────────────
    events = svc.get_events(job_id)
    types = {e["type"] for e in events}
    check("APP-08 monitoring updates", "round.create" in types and
          "round.fedavg" in types and "job.status" in types,
          f"events={sorted(types)[:8]}")

    # ── APP-09: logs isolated by job ─────────────────────────────────────
    logs1 = svc.get_logs(job_id)
    other = svc.create_training_job(
        name="other", backend_id="dummy", model_id="dummy",
        dataset_id=ds_good["dataset_id"], training={}, workers_cfg={})
    logs2 = svc.get_logs(other["job_id"])
    f1 = {l["file"] for l in logs1}
    f2 = {l["file"] for l in logs2}
    check("APP-09 logs isolated by job", f1.isdisjoint(f2),
          f"job1={sorted(f1)} job2={sorted(f2)}")

    # ── APP-11/12: artifacts ─────────────────────────────────────────────
    arts = svc.list_artifacts(job_id)
    check("APP-11 completed artifact available",
          any(a["type"] == "adapter_2" for a in arts) and len(arts) >= 3,
          f"types={[a['type'] for a in arts]}")
    ok_sha = True
    for a in arts:
        if os.path.exists(a["path"]):
            with open(a["path"], "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != a["sha256"]:
                    ok_sha = False
    check("APP-12 SHA artifact valid", ok_sha)

    # ── APP-10: cancel ───────────────────────────────────────────────────
    os.environ["APP_TEST_ROUND_DELAY"] = "30"  # espai per cancel·lar
    job_c = svc.create_training_job(
        name="cancel-job", backend_id="dummy", model_id="dummy",
        dataset_id=ds_good["dataset_id"],
        training={"rounds": 2}, workers_cfg={"workers": 2})
    svc.validate_job(job_c["job_id"])
    svc.start_job(job_c["job_id"])
    j = wait_status(svc, job_c["job_id"], ["RUNNING"], timeout=60)
    check("APP-10a cancel-jobs RUNNING", j["status"] == "RUNNING", j["status"])
    j = svc.cancel_job(job_c["job_id"])
    check("APP-10b cancel -> CANCELLED", j["status"] == "CANCELLED", j["status"])
    del os.environ["APP_TEST_ROUND_DELAY"]

    # ── APP-13: restart app retains jobs ─────────────────────────────────
    svc2 = MeshTrainerService()  # mateixa APP_DB_PATH
    jobs2 = {j["job_id"]: j for j in svc2.list_jobs()}
    check("APP-13 restart app retains jobs",
          job_id in jobs2 and jobs2[job_id]["status"] == "COMPLETED")

    # ── APP-15: dummy hidden in production ───────────────────────────────
    r = subprocess.run(
        [sys.executable, "-c",
         "import os,sys; os.environ['APP_ENV']='production';"
         "sys.path.insert(0, os.getcwd());"
         "from app.services.registry import discover_backends;"
         "print(sorted(b.id for b in discover_backends()))"],
        capture_output=True, text=True, timeout=30)
    ids = r.stdout.strip()
    check("APP-15 dummy hidden in production", "dummy" not in ids,
          f"backends={ids}")

    # ── resum ────────────────────────────────────────────────────────────
    npass = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"=== APP ACCEPTANCE: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
