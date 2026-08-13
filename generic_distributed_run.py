#!/usr/bin/env python3
"""generic_distributed_run.py — DRIVER END-TO-END GENERIC (R2.1, punt 9).

Executa el flux distribuït complet utilitzant EXCLUSIVAMENT:
  Generic HTTP server (training_http_server)
  Generic Coordinator (ModelRoundCoordinator)
  Generic Worker (model_worker)
  Backend plugin (--backend qwen3 | dummy)

Workers A i B com a SUBPROCESSOS reals (seqüencial per RAM):
  - worker_ids diferents, sessions diferents, leases diferents, shards
    diferents, HTTP real, Coordinator real, ETT real, FedAvg real,
    Round 2, recovery real (SIGKILL opcional).

Ús:
  generic_distributed_run.py --backend qwen3 [--num-examples N] [--seq-len L]
  generic_distributed_run.py --backend dummy  [--num-examples N]
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from training_http_server import serve
from model_round_coordinator import ModelRoundCoordinator
from adapter_codec import unpack_tensors, to_numpy_dict, fedavg

CHECKS = []
PORT = 19862
ADMIN_TOKEN = os.environ.get("MESH_ADMIN_TOKEN", "mesh-admin-token-test")
SERVER_URL = f"http://127.0.0.1:{PORT}"


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def make_adapter_0(backend_name, model, seq_len, out_dir, bcfg):
    """Genera adapter_0 amb el mateix backend (plugin) i el retorna."""
    from model_worker import load_backend
    cls = load_backend(backend_name)
    mp = model or ("dummy" if backend_name == "dummy"
                   else "/root/qwen3_0_6b_snapshot")
    dbp = f"/tmp/generic_ad0_{backend_name}.db"
    if os.path.exists(dbp):
        os.remove(dbp)
    bk = cls(mp, db_path=dbp, seq_len=seq_len, **bcfg)
    bk.load_model()
    ad0 = bk.adapter_to_bundle()
    identity = bk.base_model_identity()
    bk.close()
    return ad0, identity


def run_worker(backend_name, worker_id, session, shard, assignment, round_id,
               adapter_path, adapter_sha, out_dir, num_examples, seq_len,
               extra=None, train_only=False):
    cmd = [sys.executable, "model_worker.py", "--backend", backend_name,
           "--worker-id", worker_id, "--session-id", session, "--shard", shard,
           "--assignment-id", assignment, "--round-id", round_id,
           "--server-url", SERVER_URL, "--adapter-0", adapter_path,
           "--adapter-0-sha", adapter_sha, "--out-dir", out_dir,
           "--num-examples", str(num_examples), "--seq-len", str(seq_len)]
    if backend_name == "qwen3":
        cmd += ["--model", os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")]
        cmd += ["--data-dir", os.environ.get("QWEN3_DATA",
                                             "/root/meshtrainer/qwen3_pilot_data")]
    elif backend_name == "dummy":
        cmd += ["--model", "dummy"]
        cmd += ["--data-dir", os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "qwen3_pilot_data")]
    if extra:
        cmd += ["--backend-config", json.dumps(extra)]
    if train_only:
        cmd += ["--train-only"]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    out = r.stdout + r.stderr
    # R2.5.1 (punt 6/8): l'output del worker és EVIDÈNCIA — es reemet a
    # stdout perquè els logs del gate mostrin "CONTRIBUTION SUBMITTED"
    # (i mai "CONTRIBUTION VALIDATED" com a acció del worker).
    if out.strip():
        for line in out.splitlines():
            if line.strip():
                print(f"[worker:{worker_id}] {line}", flush=True)
    if r.returncode != 0:
        print(out[-1500:], flush=True)
    return r.returncode, out


def fedavg_via_coordinator(proto, run_id, round_id, adapter_0_bytes):
    ad0_b64 = base64.b64encode(adapter_0_bytes).decode("ascii")
    return proto.handle("round_fedavg",
                        {"run_id": run_id, "round_id": round_id,
                         "assignment_id": "asg-A", "worker_id": "w-A",
                         "adapter_0_bundle_b64": ad0_b64,
                         "admin_token": ADMIN_TOKEN})


def admin_activate(proto, cids):
    """R2.4/R2.5: contribution.validate + activate són operacions ADMIN —
    el driver (admin) valida i activa les contribucions SUBMITTED després
    que els workers les enviïn (el worker NO pot decidir l'estat)."""
    for cid in cids:
        proto.handle("contribution.validate",
                     {"contribution_id": cid, "admin_token": ADMIN_TOKEN})
        proto.handle("contribution.activate",
                     {"contribution_id": cid, "admin_token": ADMIN_TOKEN})
        print(f"  [admin] contribution validate+activate {cid[:16]}",
              flush=True)


def create_round_and_assignments(proto, run_id, round_id, backend_name,
                                 base_model_hash, adapter_0_sha,
                                 adapter_pre_hash, assignments, shard_mf_fn,
                                 expected_ett_fn, dataset_manifest_sha=""):
    """R2.3: el Coordinator crea la ronda i les assignacions (autoritat).
    assignments: llista de dicts {assignment_id, worker_id, shard, num_ex}"""
    proto.handle("round.create", {
        "run_id": run_id, "round_id": round_id, "backend_id": backend_name,
        "base_model_hash": base_model_hash, "adapter_0_sha": adapter_0_sha,
        "adapter_pre_hash": adapter_pre_hash,
        "dataset_manifest_sha": dataset_manifest_sha,
        "admin_token": ADMIN_TOKEN})
    for a in assignments:
        shard_mf = shard_mf_fn(a["shard"])
        exp_ett = expected_ett_fn(a["shard"], a["num_ex"])
        proto.handle("assignment.create", {
            "run_id": run_id, "round_id": round_id,
            "assignment_id": a["assignment_id"], "worker_id": a["worker_id"],
            "shard_id": f"shard-{a['shard']}", "shard_manifest_sha": shard_mf,
            "base_model_hash": base_model_hash, "adapter_0_sha": adapter_0_sha,
            "expected_ett": exp_ett, "revision": 1,
            "admin_token": ADMIN_TOKEN})
        print(f"  [coord] assignment {a['assignment_id']} -> shard-{a['shard']} "
              f"expected_ett={exp_ett}", flush=True)


def oracle_fedavg(coordinator, run_id, round_id, adapter_0_bytes):
    rows = coordinator.active_deltas(run_id, round_id)
    deltas, etts = [], []
    for r in rows:
        dt = unpack_tensors(base64.b64decode(r["delta_bundle_b64"]))
        deltas.append(to_numpy_dict(dt))
        etts.append(r["ett"])
    adapter_0 = to_numpy_dict(unpack_tensors(adapter_0_bytes))
    return fedavg(adapter_0, list(zip(deltas, etts)))


def compare_oracle(fed_a1_bytes, oracle, tol=1e-6):
    fed_a1 = unpack_tensors(fed_a1_bytes)
    return max(float(np.abs(np.asarray(fed_a1[n], dtype="float64") -
                            oracle[n].astype("float64")).max()) for n in oracle)


def expected_ett_for_shard(backend_name, model, seq_len, shard, num_ex,
                           bcfg=None):
    """R2.3, punt 7: expected_ett calculat PRÈVIAMENT amb el backend
    (etapa trusted/backend-aware). El Coordinator només guarda el valor."""
    from model_worker import load_backend, load_examples
    cls = load_backend(backend_name)
    mp = model or ("dummy" if backend_name == "dummy"
                   else "/root/qwen3_0_6b_snapshot")
    dbp = f"/tmp/generic_plan_{backend_name}_{shard}.db"
    if os.path.exists(dbp):
        os.remove(dbp)
    bk = cls(mp, db_path=dbp, seq_len=seq_len, **(bcfg or {}))
    bk.load_model()
    exs = load_examples(data_dir_for(backend_name), shard, num_ex)
    ett = 0
    for e in exs:
        tok = bk.tokenize_example(e["instruction"], e["response"])
        ett += bk.compute_ett(tok[2] if isinstance(tok, tuple) else tok)
    bk.close()
    return ett


def data_dir_for(backend_name):
    if backend_name == "qwen3":
        return os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "qwen3_pilot_data")


def compute_pre_hash(backend_name, model, seq_len, adapter_bytes):
    """R2.3: adapter_pre_hash = hash de l'estat del model AMB l'adapter
    carregat (baseline de la ronda). Etapa trusted del Coordinator."""
    from model_worker import load_backend
    cls = load_backend(backend_name)
    mp = model or ("dummy" if backend_name == "dummy"
                   else "/root/qwen3_0_6b_snapshot")
    dbp = f"/tmp/generic_prehash_{backend_name}_{hashlib.sha256(adapter_bytes).hexdigest()[:8]}.db"
    bk = cls(mp, db_path=dbp, seq_len=seq_len)
    bk.load_model()
    sha = hashlib.sha256(adapter_bytes).hexdigest()
    bk.load_adapter(adapter_bytes, sha, strict=True)
    pre = bk.adapter_hash()
    bk.close()
    return pre


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True, choices=["qwen3", "dummy"])
    ap.add_argument("--num-examples", type=int, default=10)
    ap.add_argument("--seq-len", type=int, default=128)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--server-db", default="/tmp/generic_dist_server.db")
    args = ap.parse_args()

    OUT = args.out_dir or os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "qwen_distributed_output")
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    for p in (args.server_db,):
        if os.path.exists(p):
            os.remove(p)

    # ── 1. adapter_0 (backend plugin) + servidor genèric ──
    ad0, identity = make_adapter_0(args.backend, args.model, args.seq_len,
                                   OUT, {})
    ad0_path = os.path.join(OUT, "adapter_0.bundle")
    with open(ad0_path, "wb") as f:
        f.write(ad0)
    ad0_sha = hashlib.sha256(ad0).hexdigest()
    print(f"adapter_0 {ad0_sha[:16]} base={identity['base_model_hash'][:16]} "
          f"backend={args.backend}", flush=True)

    httpd, proto = serve(port=19862, db_path=args.server_db,
                         admin_token=ADMIN_TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.5)

    # ── R2.3: el Coordinator crea la ronda i les assignacions (autoritat).
    #    expected_ett i shard_manifest es calculen PRÈVIAMENT amb el backend
    #    (etapa trusted) — mateixa lògica que el worker (load_examples).
    from model_worker import load_examples as _lex, shard_manifest_sha as _sms

    def _shard_mf(shard, n):
        exs = _lex(data_dir_for(args.backend), shard, n)
        return _sms([e["source"] for e in exs])

    def _expected_ett(shard, n):
        return expected_ett_for_shard(args.backend, args.model, args.seq_len,
                                      shard, n)
    base_hash = identity["base_model_hash"]
    adapter_pre_hash = compute_pre_hash(args.backend, args.model,
                                        args.seq_len, ad0)
    create_round_and_assignments(
        proto, "run1", "r1", args.backend, base_hash, ad0_sha,
        adapter_pre_hash,
        [{"assignment_id": "asg-A", "worker_id": "w-A", "shard": "A",
          "num_ex": args.num_examples},
         {"assignment_id": "asg-B", "worker_id": "w-B", "shard": "B",
          "num_ex": args.num_examples}],
        shard_mf_fn=lambda sh, n=args.num_examples: _shard_mf(sh, n),
        expected_ett_fn=_expected_ett)
    check("coordinator: round r1 creada (model_rounds)",
          proto.coord.get_round("run1", "r1") is not None)
    check("coordinator: assignments A/B creades",
          proto.coord.get_assignment("run1", "r1", "asg-A", "w-A") is not None
          and proto.coord.get_assignment("run1", "r1", "asg-B", "w-B") is not None)

    # ── 3/4. Worker A i B (subprocessos reals) ──
    wA_rc, wA_out = run_worker(args.backend, "w-A", "A1", "A", "asg-A", "r1",
                               ad0_path, ad0_sha, OUT, args.num_examples,
                               args.seq_len)
    check("worker A (subprocess, shard A)", wA_rc == 0 and "WORKER_DONE" in wA_out)
    wB_rc, wB_out = run_worker(args.backend, "w-B", "B1", "B", "asg-B", "r1",
                               ad0_path, ad0_sha, OUT, args.num_examples,
                               args.seq_len)
    check("worker B (subprocess, shard B)", wB_rc == 0 and "WORKER_DONE" in wB_out)

    evA = json.load(open(os.path.join(OUT, f"evidence_w-A.json")))
    evB = json.load(open(os.path.join(OUT, f"evidence_w-B.json")))
    check("mateix base_model_hash (A==B)", evA["base_model_hash"] == evB["base_model_hash"])
    check("shard manifests diferents", evA["shard_manifest_sha"] != evB["shard_manifest_sha"])
    check("worker_ids diferents", evA["worker_id"] != evB["worker_id"])
    check("sessions diferents", evA["session_id"] != evB["session_id"])
    check("leases diferents", evA["lease_id"] != evB["lease_id"])
    if args.backend == "qwen3":
        check("ETT_A != ETT_B", evA["ett_actual"] != evB["ett_actual"],
              f"{evA['ett_actual']} vs {evB['ett_actual']}")
    else:
        check("ETT_A > 0 i ETT_B > 0 (dummy: ETT real per exemple)",
              evA["ett_actual"] > 0 and evB["ett_actual"] > 0,
              f"{evA['ett_actual']} vs {evB['ett_actual']}")

    # R2.4: l'ADMIN activa les contribucions (el worker només les envia)
    admin_activate(proto, [evA["contribution_id"], evB["contribution_id"]])
    check("ready_to_close: quòrum exacte 2/2 (r1)",
          proto.coord.ready_to_close("run1", "r1")["state"] == "READY")

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
    # R2.3: nova ronda r2 amb adapter_1 com a adapter global + assignments
    n2 = max(1, args.num_examples // 2)
    a1_bytes = base64.b64decode(fed1["adapter_1_b64"])
    a1_pre = compute_pre_hash(args.backend, args.model, args.seq_len, a1_bytes)
    create_round_and_assignments(
        proto, "run1", "r2", args.backend, base_hash,
        fed1["adapter_1_sha"], a1_pre,
        [{"assignment_id": "asg-A2", "worker_id": "w-A2", "shard": "A",
          "num_ex": n2},
         {"assignment_id": "asg-B2", "worker_id": "w-B2", "shard": "B",
          "num_ex": n2}],
        shard_mf_fn=lambda sh, n=n2: _shard_mf(sh, n),
        expected_ett_fn=_expected_ett)
    wA2_rc, wA2_out = run_worker(args.backend, "w-A2", "A2", "A", "asg-A2",
                                 "r2", a1_path, fed1["adapter_1_sha"], OUT,
                                 n2, args.seq_len)
    check("worker A2 (round 2)", wA2_rc == 0 and "WORKER_DONE" in wA2_out)
    wB2_rc, wB2_out = run_worker(args.backend, "w-B2", "B2", "B", "asg-B2",
                                 "r2", a1_path, fed1["adapter_1_sha"], OUT,
                                 max(1, args.num_examples // 2), args.seq_len)
    check("worker B2 (round 2)", wB2_rc == 0 and "WORKER_DONE" in wB2_out)
    evA2 = json.load(open(os.path.join(OUT, f"evidence_w-A2.json")))
    evB2 = json.load(open(os.path.join(OUT, f"evidence_w-B2.json")))
    check("round 2: adapter_pre_hash_A == adapter_pre_hash_B (adapter_1)",
          evA2["adapter_pre_hash"] == evB2["adapter_pre_hash"],
          evA2["adapter_pre_hash"][:16])
    # R2.4: l'ADMIN activa les contribucions de la ronda 2
    admin_activate(proto, [evA2["contribution_id"], evB2["contribution_id"]])
    check("ready_to_close: quòrum exacte 2/2 (r2)",
          proto.coord.ready_to_close("run1", "r2")["state"] == "READY")
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

    httpd.shutdown()

    out = {"backend": args.backend,
           "adapter_0_sha": ad0_sha,
           "adapter_1_sha": fed1["adapter_1_sha"],
           "adapter_2_sha": fed2["adapter_1_sha"],
           "ett_a": evA["ett_actual"], "ett_b": evB["ett_actual"],
           "maxdiff_oracle_r1": md1, "maxdiff_oracle_r2": md2}
    with open(os.path.join(OUT, "distributed_metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, indent=2, ensure_ascii=False))

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== GENERIC DISTRIBUTED RUN ({args.backend}): "
          f"{npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
