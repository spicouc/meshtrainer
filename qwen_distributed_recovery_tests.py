#!/usr/bin/env python3
"""qwen_distributed_recovery_tests.py — RECOVERY DISTRIBUÏT QWEN (secció 11).

R-Q1 crash Worker A després d'APPLIED: subprocess fa train_step (APPLIED al
      journal persistent) i "mor"; un NOU procés (PID nou, mateixa BD) fa
      recover_applied + continua; l'adapter final ha de ser IDÈNTIC al cas
      sense crash.
R-Q2 lease expiry: el worker antic NO pot submit/commit/upload.
R-Q3 reassignació: després d'EXPIRED, un altre worker adquireix (revision).
R-Q4 mid-training: cap autograd persistit (unitat sense delta) -> EXPIRED ->
      nova revision completa.
R-Q5 checkpoint retry: mateixa resposta, zero contribució duplicada.
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

MODEL = os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")
DATA = os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from qwen_http_server import serve
from qwen_round_coordinator import QwenRecoveryError

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def expect_rejected(fn, name):
    try:
        fn()
        check(name, False, "no REJECTED")
    except QwenRecoveryError:
        check(name, True, "QwenRecoveryError")
    except Exception as e:
        check(name, "rejected" in str(e).lower() or "lease" in str(e).lower()
              or "mismatch" in str(e).lower(), f"{type(e).__name__}: {str(e)[:60]}")


def synth_delta(seed=1):
    """Delta sintètic vàlid (sense model): 2 tensors LoRA petits."""
    import numpy as np
    rng = np.random.default_rng(seed)
    d = {"base_model.model.layers.0.q_proj.lora_A.weight": rng.standard_normal((2, 8)).astype("float32"),
         "base_model.model.layers.0.q_proj.lora_B.weight": rng.standard_normal((4, 2)).astype("float32")}
    import torch
    from qwen3_training_backend import Qwen3TrainingBackend
    b = Qwen3TrainingBackend._serialize_delta(
        {k: torch.as_tensor(v) for k, v in d.items()})
    return base64.b64encode(b).decode("ascii"), hashlib.sha256(b).hexdigest()


def main():
    import shutil
    OUT = "/tmp/qwen_rec_dist"
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    for p in ("/tmp/qwen_rec_dist_server.db",):
        if os.path.exists(p):
            os.remove(p)

    clock = {"now": 1000.0}
    httpd, proto = serve(port=19863, db_path="/tmp/qwen_rec_dist_server.db",
                         now=lambda: clock["now"], default_ttl_seconds=30.0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.5)

    # registre de 2 workers (mateix base)
    base_h = "a" * 64
    proto.handle("worker.register", {"worker_id": "qwen-A", "session_id": "A1",
                                     "base_model_hash": base_h, "config_sha": "c" * 64})
    proto.handle("worker.register", {"worker_id": "qwen-B", "session_id": "B1",
                                     "base_model_hash": base_h, "config_sha": "c" * 64})
    proto.handle("worker.calibrate", {"worker_id": "qwen-A", "session_id": "A1",
                                      "run_id": "run1", "round_id": "r1",
                                      "assignment_id": "asg-A", "shard_id": "shard-A",
                                      "shard_manifest_sha": "m" * 64,
                                      "adapter_0_sha": "d" * 64, "ett_registered": 100})
    proto.handle("worker.calibrate", {"worker_id": "qwen-B", "session_id": "B1",
                                      "run_id": "run1", "round_id": "r1",
                                      "assignment_id": "asg-B", "shard_id": "shard-B",
                                      "shard_manifest_sha": "n" * 64,
                                      "adapter_0_sha": "d" * 64, "ett_registered": 80})
    unit = "unit-asg-A"

    def lease_for(worker, session, unit_id=unit, asg="asg-A"):
        return proto.handle("lease.acquire",
                            {"run_id": "run1", "round_id": "r1",
                             "assignment_id": asg, "micro_unit_id": unit_id,
                             "worker_id": worker, "session_id": session,
                             "ttl_seconds": 30})

    def lp(ls, worker, session, unit_id=unit, asg="asg-A"):
        return {"lease_id": ls["lease_id"], "lease_nonce": ls["lease_nonce"],
                "worker_id": worker, "session_id": session,
                "run_id": "run1", "round_id": "r1", "assignment_id": asg,
                "micro_unit_id": unit_id}

    d64, dsha = synth_delta(1)

    # ── R-Q2: lease expiry -> worker antic no pot submit ──
    lsA = lease_for("qwen-A", "A1")
    clock["now"] += 31.0
    expect_rejected(lambda: proto.handle("step.open", {**lp(lsA, "qwen-A", "A1"),
        "unit_id": unit, "base_model_hash": base_h, "adapter_pre_hash": "p" * 64,
        "shard_manifest_sha": "m" * 64, "ett_registered": 100}),
        "R-Q2 step.open amb lease EXPIRED REJECTED")

    # ── R-Q3: reassignació (altre worker adquireix després d'EXPIRED) ──
    lsB = lease_for("qwen-B", "B1", unit_id=unit)
    check("R-Q3 reassignació: worker B adquireix la unitat",
          lsB["lease_id"] != lsA["lease_id"], lsB["lease_id"][:12])
    clock["now"] += 31.0  # expira la del B

    # ── R-Q4: mid-training -> EXPIRED sense autograd ──
    # worker C adquireix, no fa submit (mor a mig training); la lease venç
    lsC = lease_for("qwen-A", "A1", unit_id="unit-mid")
    clock["now"] += 31.0
    st = proto.lm.status(lease_id=lsC["lease_id"])["status"]
    check("R-Q4 mid-training -> EXPIRED", st == "EXPIRED", st)
    u = proto._unit("unit-mid")
    check("R-Q4 cap autograd persistit (unitat sense delta)",
          u is None or not u.get("delta_bundle_sha256"))
    lsD = lease_for("qwen-B", "B1", unit_id="unit-mid")
    check("R-Q4 nova revision completa (altre worker)", lsD["lease_id"] != lsC["lease_id"])

    # ── R-Q5: checkpoint retry idempotent ──
    # flux complet sintètic amb lease fresca
    lsE = lease_for("qwen-A", "A1")
    p_le = lp(lsE, "qwen-A", "A1")
    proto.handle("step.open", {**p_le, "unit_id": unit, "base_model_hash": base_h,
                               "adapter_pre_hash": "p" * 64,
                               "shard_manifest_sha": "m" * 64, "ett_registered": 100})
    upd = proto.handle("step.worker_update.submit",
                       {**p_le, "unit_id": unit, "update_id": "u1",
                        "delta_id": "x" * 32, "example_ids": ["train:0"],
                        "shard_manifest_sha": "m" * 64, "ett": 100,
                        "delta_bundle_b64": d64, "delta_bundle_sha256": dsha,
                        "delta_bundle_byte_length": len(base64.b64decode(d64)),
                        "adapter_pre_hash": "p" * 64, "base_model_hash": base_h,
                        "request_sha": "r" * 64, "loss": 1.5})
    proto.handle("step.commit", {**p_le, "unit_id": unit})
    up1 = proto.handle("checkpoint.upload", {**p_le, "unit_id": unit,
                                             "receipt": upd["receipt"],
                                             "delta_bundle_b64": d64})
    up2 = proto.handle("checkpoint.upload", {**p_le, "unit_id": unit,
                                             "receipt": upd["receipt"],
                                             "delta_bundle_b64": d64})
    check("R-Q5 checkpoint retry: mateixa resposta", up1 == up2, up1["contribution_id"][:16])
    reg1 = proto.handle("contribution.register", {**p_le,
                                                  "contribution_id": up1["contribution_id"]})
    reg2 = proto.handle("contribution.register", {**p_le,
                                                  "contribution_id": up1["contribution_id"]})
    check("R-Q5 contribution.register idempotent (zero duplicada)",
          reg1 == reg2 and reg2["status"] == "RECEIVED", reg2["status"])
    acts = proto.coord.active_deltas("run1", "r1")
    n_contrib = len([c for c in acts])
    check("R-Q5 zero contribució duplicada (1 única)", n_contrib <= 1, f"active={n_contrib}")
    proto.handle("step.release", {**p_le})

    # ── R-Q1: crash Worker A després d'APPLIED (subprocessos reals) ──
    # adapter_0 per als workers
    import torch
    from qwen3_training_backend import Qwen3TrainingBackend
    bk = Qwen3TrainingBackend(MODEL, db_path="/tmp/qwen_rec_ad0.db", seq_len=64)
    bk.load()
    ad0 = bk.adapter_to_bundle()
    ad0_path = os.path.join(OUT, "adapter_0.bundle")
    with open(ad0_path, "wb") as f:
        f.write(ad0)
    ad0_sha = hashlib.sha256(ad0).hexdigest()
    del bk
    import gc; gc.collect()

    # C1: subprocess amb --train-only (fa 2 passos APPLIED i "mor" abans del
    # flux HTTP). MATEIX nombre d'exemples que C2/NC: la unitat s'obre amb
    # ett_registered idèntic (idempotència del step.open).
    cmd1 = [sys.executable, "qwen_worker.py", "--worker-id", "qwen-R1",
            "--session-id", "R1", "--shard", "A", "--assignment-id", "asg-R1",
            "--round-id", "rq1", "--server-url", "http://127.0.0.1:19863",
            "--model", MODEL, "--data-dir", DATA, "--adapter-0", ad0_path,
            "--adapter-0-sha", ad0_sha, "--out-dir", OUT,
            "--num-examples", "2", "--seq-len", "64", "--train-only",
            "--deterministic"]
    r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=1200)
    if r1.returncode != 0:
        print((r1.stdout + r1.stderr)[-900:], flush=True)
    check("R-Q1 procés C1 (crash simulat) exit 0", r1.returncode == 0)
    ev1 = json.load(open(os.path.join(OUT, "evidence_qwen-R1.json")))
    check("R-Q1 C1: journal APPLIED (delta present)", bool(ev1.get("delta_sha")),
          ev1.get("delta_sha", "?")[:16])

    # C2: NOU procés (PID diferent), mateixa BD -> recover + continuar + HTTP
    cmd2 = [sys.executable, "qwen_worker.py", "--worker-id", "qwen-R1",
            "--session-id", "R1", "--shard", "A", "--assignment-id", "asg-R1",
            "--round-id", "rq1", "--server-url", "http://127.0.0.1:19863",
            "--model", MODEL, "--data-dir", DATA, "--adapter-0", ad0_path,
            "--adapter-0-sha", ad0_sha, "--out-dir", OUT,
            "--num-examples", "2", "--seq-len", "64", "--deterministic"]
    r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=1200)
    out2 = r2.stdout + r2.stderr
    if r2.returncode != 0:
        print(out2[-900:], flush=True)
    check("R-Q1 procés C2 (recovery) exit 0 + WORKER_DONE",
          r2.returncode == 0 and "WORKER_DONE" in out2)
    check("R-Q1 C2: RECOVERED (recovery sense optimizer)",
          "RECOVERED" in out2)
    ev2 = json.load(open(os.path.join(OUT, "evidence_qwen-R1.json")))
    check("R-Q1 C2: PID diferent del C1", ev2["pid"] != ev1["pid"],
          f"{ev1['pid']} -> {ev2['pid']}")

    # cas sense crash: worker normal (BD nova), 2 exemples
    shutil.rmtree(os.path.join(OUT, "journal_qwen-R1.db"), ignore_errors=True)
    r3 = subprocess.run(
        [sys.executable, "qwen_worker.py", "--worker-id", "qwen-NC1",
         "--session-id", "N1", "--shard", "A", "--assignment-id", "asg-NC1",
         "--round-id", "rq1", "--server-url", "http://127.0.0.1:19863",
         "--model", MODEL, "--data-dir", DATA, "--adapter-0", ad0_path,
         "--adapter-0-sha", ad0_sha, "--out-dir", OUT,
         "--num-examples", "2", "--seq-len", "64", "--deterministic"],
        capture_output=True, text=True, timeout=1200)
    if r3.returncode != 0:
        print((r3.stdout + r3.stderr)[-900:], flush=True)
    check("R-Q1 procés no-crash exit 0", r3.returncode == 0)
    ev3 = json.load(open(os.path.join(OUT, "evidence_qwen-NC1.json")))
    check("R-Q1 adapters finals IDÈNTICS (crash vs no-crash, hash tolerant)",
          ev2["adapter_post_hash_t"] == ev3["adapter_post_hash_t"],
          f"{ev2['adapter_post_hash_t'][:12]} vs {ev3['adapter_post_hash_t'][:12]}")

    httpd.shutdown()
    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== RECOVERY DISTRIBUT: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
