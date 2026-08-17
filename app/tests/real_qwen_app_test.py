"""REAL-QWEN-APP: smoke qwen3 real a través de MeshTrainerService/JobRunner.

Evidència requerida per l'ordre:
  job READY → STARTING → RUNNING → COMPLETED
  backend == qwen3 · workers model_worker reals (PIDs)
  adapter_0 + adapter_1 · ETT per worker > 0
  contribution.validate/activate PASS · FedAvg PASS
  artifact final present, mida real, SHA(bytes) == metadata
Cap Dummy en aquesta run.
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

os.environ["APP_ENV"] = "test"
_TMP = tempfile.mkdtemp(prefix="qwen_app_")
os.environ["APP_DB_PATH"] = os.path.join(_TMP, "app.db")

from app.services.mesh_trainer_service import MeshTrainerService  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def wait_status(svc, job_id, target, timeout=1800, poll=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = svc.get_job(job_id)
        if j["status"] in target:
            return j
        time.sleep(poll)
    return svc.get_job(job_id)


def main():
    print("=== REAL-QWEN-APP (qwen3 real via MeshTrainerService) ===")
    svc = MeshTrainerService()

    # dataset mínim real (6 exemples curts)
    ds_path = os.path.join(_TMP, "qwen_ds.jsonl")
    with open(ds_path, "w", encoding="utf-8") as f:
        for i in range(6):
            f.write(json.dumps({"instruction": f"Què és {i}?",
                                "response": f"La resposta és {i}."}) + "\n")
    ds = svc.add_dataset(ds_path, "qwen-smoke")
    v = svc.validate_dataset(ds["dataset_id"])
    check("dataset qwen validat", v["validation_status"] == "PASS", v["validation_status"])

    # job qwen3 real: 1 ronda, seq_len curt, LoRA default, 2 workers
    job = svc.create_training_job(
        name="qwen-smoke", backend_id="qwen3",
        model_id="/root/qwen3_0_6b_snapshot",
        local_path="/root/qwen3_0_6b_snapshot",
        dataset_id=ds["dataset_id"],
        training={"lora_rank": 8, "lora_alpha": 16, "lora_dropout": 0.05,
                  "learning_rate": 1e-4, "rounds": 1, "max_seq_len": 32},
        workers_cfg={"workers": 2, "device": "cpu"})
    job_id = job["job_id"]
    check("job qwen3 creat (DRAFT)", job["status"] == "DRAFT", job_id)

    j = svc.validate_job(job_id)
    check("job READY (validation PASS)", j["status"] == "READY" and j["validation"] == "PASS",
          f"{j['status']} {j['validation']}")

    svc.start_job(job_id)
    j = wait_status(svc, job_id, ["RUNNING", "STARTING"], timeout=120)
    check("job STARTING → RUNNING", j["status"] in ("RUNNING", "STARTING"), j["status"])
    check("runner_pid real", bool(j.get("runner_pid")), f"pid={j.get('runner_pid')}")

    j = wait_status(svc, job_id, ["COMPLETED", "FAILED", "CANCELLED"], timeout=1800)
    check("job COMPLETED (qwen3 real)", j["status"] == "COMPLETED",
          f"{j['status']} err={j['last_error'][:120]}")

    # evidència
    arts = svc.list_artifacts(job_id)
    check("adapter_0 + adapter_1 presents",
          any(a["type"] == "adapter_0" for a in arts) and
          any(a["type"] == "adapter_1" for a in arts),
          str([a["type"] for a in arts]))

    # mida real (no dummy) + SHA(bytes) == metadata
    ok_sha = True
    sizes = []
    for a in arts:
        if os.path.exists(a["path"]):
            sz = os.path.getsize(a["path"])
            sizes.append(f"{a['type']}={sz}")
            if a["type"] == "adapter_0":
                check("adapter_0 mida real (>1KB, no dummy)",
                      sz > 1024, str(sz))
            with open(a["path"], "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != a["sha256"]:
                    ok_sha = False
    check("SHA(bytes) == metadata (tots)", ok_sha, " ".join(sizes))

    # events: contribucions + validate + activate + fedavg + ETT
    evs = {e["type"] for e in svc.get_events(job_id)}
    check("contribution.submit present", "contribution.submit" in evs)
    check("contribution.validate present (R1)", "contribution.validate" in evs)
    check("contribution.activate present", "contribution.activate" in evs)
    check("round.fedavg present", "round.fedavg" in evs)

    # ETT per worker > 0 (dels events de contribució)
    etts = []
    for e in svc.get_events(job_id):
        if e["type"] == "contribution.submit":
            p = e["payload"]
            if isinstance(p, str):
                p = json.loads(p)
            etts.append(p.get("ett"))
    check("ETT per worker > 0", all(isinstance(x, int) and x > 0 for x in etts),
          f"etts={etts}")

    # workers reals: logs de model_worker amb PIDs
    wlogs = [w for w in svc.get_workers(job_id)]
    check("logs de workers reals presents", len(wlogs) >= 2, f"n={len(wlogs)}")
    pids_found = []
    for w in wlogs:
        with open(w["log"], errors="replace") as f:
            for line in f:
                if "REGISTERED" in line:
                    import re
                    m = re.search(r"\]\s+(\d+)\s+REGISTERED", line)
                    if m:
                        pids_found.append(m.group(1))
    check("PIDs de model_worker reals", len(pids_found) >= 2,
          f"pids={pids_found[:4]}")

    npass = sum(1 for _, ok, _ in CHECKS if ok)

    # ── evidència (punt 6 R1): evidence/phase1/ ──────────────────────────
    import subprocess as _sp
    ev_dir = os.path.join(_CORE, "evidence", "phase1")
    os.makedirs(ev_dir, exist_ok=True)
    commit = ""
    try:
        commit = _sp.run(["git", "-C", _CORE, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    except Exception:
        pass
    ev_json = {
        "commit": commit,
        "backend": "qwen3",
        "model": "/root/qwen3_0_6b_snapshot",
        "job_id": job_id,
        "runner_pid": j.get("runner_pid"),
        "worker_pids": pids_found,
        "ett_workers": etts,
        "adapter_0": {"sha256": next((a["sha256"] for a in arts if a["type"] == "adapter_0"), ""),
                      "size": next((os.path.getsize(a["path"]) for a in arts if a["type"] == "adapter_0" and os.path.exists(a["path"])), 0)},
        "adapter_1": {"sha256": next((a["sha256"] for a in arts if a["type"] == "adapter_1"), ""),
                      "size": next((os.path.getsize(a["path"]) for a in arts if a["type"] == "adapter_1" and os.path.exists(a["path"])), 0)},
        "contribution_submit": "contribution.submit" in evs,
        "contribution_validate": "contribution.validate" in evs,
        "contribution_activate": "contribution.activate" in evs,
        "fedavg": "round.fedavg" in evs,
        "final_status": j["status"],
        "checks": f"{npass}/{len(CHECKS)}",
    }
    ev_path = os.path.join(ev_dir, "REAL_QWEN_APP_EVIDENCE.json")
    with open(ev_path, "w", encoding="utf-8") as f:
        json.dump(ev_json, f, ensure_ascii=False, indent=2)
    log_path = os.path.join(ev_dir, "REAL_QWEN_APP.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"=== REAL-QWEN-APP {commit} ===\n")
        for name, ok, detail in CHECKS:
            f.write(f"{'PASS' if ok else 'FAIL'} {name} — {detail}\n")
        f.write(f"=== {npass}/{len(CHECKS)} PASS ===\n")
    print(f"evidència: {ev_path} + {log_path}")

    print(f"=== REAL-QWEN-APP: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
