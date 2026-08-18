#!/usr/bin/env python3
"""qwen_distributed_run.py — QWEN PILOT R2 DISTRIBUÏT END-TO-END (driver).

Executa el flux complet amb control HTTP real:
  1. servidor (serve54 Qwen, thread) + adapter_0
  2. QUALITY BEFORE (val loss/ppl + holdouts, subprocess)
  3. Worker A (SUBPROCÉS real, shard A) -> evidència
  4. Worker B (SUBPROCÉS real, shard B) -> evidència  (seqüencial: RAM)
  5. FedAvg Coordinator (RC5.3 pattern) -> adapter_1
  6. oracle independent -> comparar tensor per tensor
  7. ROUND 2: adapter_1 distribuït, 2 workers -> adapter_2
  8. QUALITY AFTER (amb adapter_2)

Ús: qwen_distributed_run.py [--num-examples N] [--seq-len L]
"""
import argparse
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
import numpy as np
from qwen_http_server import serve
from qwen3_training_backend import Qwen3TrainingBackend, fedavg_qwen

CHECKS = []
SERVER_URL = "http://127.0.0.1:19862"


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def run_worker(worker_id, session, shard, assignment, round_id, adapter_path,
               adapter_sha, out_dir, num_examples, seq_len):
    cmd = [sys.executable, "qwen_worker.py", "--worker-id", worker_id,
           "--session-id", session, "--shard", shard, "--assignment-id",
           assignment, "--round-id", round_id, "--server-url", SERVER_URL,
           "--model", MODEL, "--data-dir", DATA, "--adapter-0", adapter_path,
           "--adapter-0-sha", adapter_sha, "--out-dir", out_dir,
           "--num-examples", str(num_examples), "--seq-len", str(seq_len)]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    out = r.stdout + r.stderr
    print(f"worker {worker_id} exit={r.returncode} ({time.time()-t0:.0f}s)", flush=True)
    if r.returncode != 0:
        print(out[-1200:], flush=True)
    return r.returncode == 0 and "WORKER_DONE" in out


def run_quality(adapter_path, adapter_sha, out_json, out_dir):
    cmd = [sys.executable, "qwen_quality.py", "--model", MODEL,
           "--adapter", adapter_path, "--adapter-sha", adapter_sha,
           "--data-dir", DATA, "--out", out_json]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    print((r.stdout + r.stderr).strip()[-800:], flush=True)
    if r.returncode != 0 or not os.path.exists(out_json):
        return None
    with open(out_json) as f:
        return json.load(f)


def fedavg_via_coordinator(proto, run_id, round_id, adapter_0_bytes):
    ad0_b64 = base64.b64encode(adapter_0_bytes).decode("ascii")
    return proto.handle("round_fedavg",
                        {"run_id": run_id, "round_id": round_id,
                         "assignment_id": "asg-A", "worker_id": "qwen-A",
                         "adapter_0_bundle_b64": ad0_b64})


def oracle_fedavg(coordinator, run_id, round_id, adapter_0_bytes):
    rows = coordinator.active_deltas(run_id, round_id)
    deltas, etts = [], []
    for r in rows:
        dt = Qwen3TrainingBackend._deserialize_delta(
            base64.b64decode(r["delta_bundle_b64"]))
        deltas.append({k: np.asarray(v, dtype=np.float32) for k, v in dt.items()})
        etts.append(r["ett"])
    adapter_0 = {k: np.asarray(v, dtype=np.float32)
                 for k, v in Qwen3TrainingBackend._deserialize_delta(adapter_0_bytes).items()}
    return fedavg_qwen(adapter_0, list(zip(deltas, etts)))


def compare_oracle(fed_a1_bytes, oracle, tol=1e-6):
    fed_a1 = Qwen3TrainingBackend._deserialize_delta(fed_a1_bytes)
    return max(float(np.abs(np.asarray(fed_a1[n], dtype="float64") -
                            oracle[n].astype("float64")).max()) for n in oracle)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--num-examples", type=int, default=10)
    ap.add_argument("--seq-len", type=int, default=128)
    args = ap.parse_args()

    OUT = "/root/meshtrainer/qwen_distributed_output"
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    for p in ("/tmp/qwen_dist_server.db",):
        if os.path.exists(p):
            os.remove(p)

    # ── 1. servidor + adapter_0 ──
    httpd, proto = serve(port=19862, db_path="/tmp/qwen_dist_server.db")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.5)
    bk = Qwen3TrainingBackend(MODEL, db_path="/tmp/qwen_dist_ad0.db", seq_len=128)
    bk.load()
    ad0 = bk.adapter_to_bundle()
    ad0_path = os.path.join(OUT, "adapter_0.bundle")
    with open(ad0_path, "wb") as f:
        f.write(ad0)
    ad0_sha = hashlib.sha256(ad0).hexdigest()
    print(f"adapter_0 {ad0_sha[:16]} base={bk.base_model_hash()[:16]}", flush=True)
    del bk
    import gc; gc.collect()

    # ── 2. QUALITY BEFORE (adapter_0) ──
    print("=== QUALITY BEFORE ===", flush=True)
    qb = run_quality(ad0_path, ad0_sha, os.path.join(OUT, "quality_before.json"), OUT)

    # ── 3/4. Worker A i B (subprocessos reals) ──
    wA = run_worker("qwen-A", "A1", "A", "asg-A", "r1", ad0_path, ad0_sha,
                    OUT, args.num_examples, args.seq_len)
    check("worker A (subprocess, shard A)", wA)
    wB = run_worker("qwen-B", "B1", "B", "asg-B", "r1", ad0_path, ad0_sha,
                    OUT, args.num_examples, args.seq_len)
    check("worker B (subprocess, shard B)", wB)

    evA = json.load(open(os.path.join(OUT, "evidence_qwen-A.json")))
    evB = json.load(open(os.path.join(OUT, "evidence_qwen-B.json")))
    check("mateix base_model_hash (A==B)", evA["base_model_hash"] == evB["base_model_hash"])
    check("shard manifests diferents", evA["shard_manifest_sha"] != evB["shard_manifest_sha"])
    check("ETT_A != ETT_B", evA["ett_actual"] != evB["ett_actual"],
          f"{evA['ett_actual']} vs {evB['ett_actual']}")

    # ── 5/6. FedAvg Coordinator + oracle ──
    fed1 = fedavg_via_coordinator(proto, "run1", "r1", ad0)
    check("FedAvg Coordinator: adapter_1 (r1)", fed1["num_contributions"] == 2,
          f"sha={fed1['adapter_1_sha'][:16]} ett={fed1['total_ett']}")
    oracle1 = oracle_fedavg(proto.coord, "run1", "r1", ad0)
    md1 = compare_oracle(base64.b64decode(fed1["adapter_1_b64"]), oracle1)
    check("oracle == FedAvg Coordinator (r1, tensor per tensor)", md1 <= 1e-6,
          f"maxdiff={md1:.2e}")
    a1_path = os.path.join(OUT, "adapter_1.bundle")
    with open(a1_path, "wb") as f:
        f.write(base64.b64decode(fed1["adapter_1_b64"]))

    # ── 7. ROUND 2 (adapter_1 distribuït) ──
    wA2 = run_worker("qwen-A2", "A2", "A", "asg-A2", "r2", a1_path,
                     fed1["adapter_1_sha"], OUT, 5, args.seq_len)
    check("worker A2 (round 2)", wA2)
    wB2 = run_worker("qwen-B2", "B2", "B", "asg-B2", "r2", a1_path,
                     fed1["adapter_1_sha"], OUT, 5, args.seq_len)
    check("worker B2 (round 2)", wB2)
    evA2 = json.load(open(os.path.join(OUT, "evidence_qwen-A2.json")))
    evB2 = json.load(open(os.path.join(OUT, "evidence_qwen-B2.json")))
    check("round 2: adapter_pre_hash_A == adapter_pre_hash_B (adapter_1)",
          evA2["adapter_pre_hash"] == evB2["adapter_pre_hash"],
          evA2["adapter_pre_hash"][:16])
    fed2 = fedavg_via_coordinator(proto, "run1", "r2",
                                  base64.b64decode(fed1["adapter_1_b64"]))
    check("FedAvg Coordinator: adapter_2 (r2)", fed2["num_contributions"] == 2,
          f"sha={fed2['adapter_1_sha'][:16]}")
    oracle2 = oracle_fedavg(proto.coord, "run1", "r2",
                            base64.b64decode(fed1["adapter_1_b64"]))
    md2 = compare_oracle(base64.b64decode(fed2["adapter_1_b64"]), oracle2)
    check("oracle == FedAvg Coordinator (r2, tensor per tensor)", md2 <= 1e-6,
          f"maxdiff={md2:.2e}")
    a2_path = os.path.join(OUT, "adapter_2.bundle")
    with open(a2_path, "wb") as f:
        f.write(base64.b64decode(fed2["adapter_1_b64"]))

    # ── 8. QUALITY AFTER (adapter_2) ──
    print("=== QUALITY AFTER ===", flush=True)
    qa = run_quality(a2_path, fed2["adapter_1_sha"],
                     os.path.join(OUT, "quality_after.json"), OUT)
    check("quality after present", qa is not None)
    if qb and qa:
        check("val loss AFTER <= BEFORE + marge",
              qa["validation_loss"] <= qb["validation_loss"] + 0.25,
              f"{qb['validation_loss']:.4f} -> {qa['validation_loss']:.4f}")
        check("ppl AFTER <= BEFORE + marge",
              qa["validation_perplexity"] <= qb["validation_perplexity"] + 1.5,
              f"{qb['validation_perplexity']:.3f} -> {qa['validation_perplexity']:.3f}")
        check("score factual no empitjora (AFTER >= BEFORE)",
              qa["score_total"] >= qb["score_total"],
              f"{qb['score_total']} -> {qa['score_total']}")

    httpd.shutdown()

    out = {"adapter_0_sha": ad0_sha,
           "adapter_1_sha": fed1["adapter_1_sha"],
           "adapter_2_sha": fed2["adapter_1_sha"],
           "ett_a": evA["ett_actual"], "ett_b": evB["ett_actual"],
           "maxdiff_oracle_r1": md1, "maxdiff_oracle_r2": md2,
           "quality_before": {k: qb[k] for k in ("validation_loss", "validation_perplexity", "score_total")} if qb else None,
           "quality_after": {k: qa[k] for k in ("validation_loss", "validation_perplexity", "score_total")} if qa else None}
    with open(os.path.join(OUT, "distributed_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, indent=2, ensure_ascii=False))

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== DISTRIBUTED RUN: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
