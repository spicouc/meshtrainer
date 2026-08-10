#!/usr/bin/env python3
"""qwen_worker.py — Qwen Worker com a PROCÉS REAL (subprocess independent).

Flux de control (12 passos, secció 6 de l'ordre):
  1 worker.register      2 worker.calibrate      3 assignment.get
  4 lease.acquire        5 step.open             6 training Qwen local
  7 step.worker_update.submit (APPLIED + delta)  8 (metadata dins submit)
  9 step.commit         10 checkpoint.upload    11 contribution.register
                                                      +validate +activate
 12 step.release

AUTO-RECUPERABLE: abans d'entrenar un pas, si el journal persistent ja té
l'update_id APPLIED, fa recover_applied() (reconstrueix adapter_post sense
optimizer.step) i continua. Un relançament amb la mateixa BD (nou PID)
reprèn exactament on va morir el procés anterior.

Ús:
  qwen_worker.py --worker-id qwen-A --session-id A1 --shard A \
      --run-id run1 --round-id r1 --assignment-id asg-A \
      --server-url http://127.0.0.1:19862 --data-dir ... --model ... \
      [--adapter-0 path --adapter-0-sha sha] --out-dir ...
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.request

MODEL_DEFAULT = os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")
DATA_DIR_DEFAULT = os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import torch as _torch
from qwen3_training_backend import Qwen3TrainingBackend
from qwen3_pilot_dataset import Qwen3PilotDataset


def rpc(server_url, method, params, timeout=120):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params,
                       "id": 1}).encode("utf-8")
    req = urllib.request.Request(server_url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    if "error" in out:
        err = out["error"]
        raise RuntimeError(f"{method}: {err.get('message', err)} "
                           f"{json.dumps(err.get('data', {}))}")
    return out.get("result")


def shard_manifest_sha(example_ids):
    canon = json.dumps(sorted(example_ids), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def sum_deltas(backend, prefix, n):
    acc = None
    for i in range(n):
        dt = backend.delta_tensors(f"{prefix}-{i}")
        if acc is None:
            acc = {k: dt[k].astype("float64") for k in dt}
        else:
            for k in dt:
                acc[k] += dt[k].astype("float64")
    return {k: v.astype("float32") for k, v in acc.items()} if acc else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker-id", required=True)
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--shard", required=True, choices=["A", "B"])
    ap.add_argument("--run-id", default="run1")
    ap.add_argument("--round-id", default="r1")
    ap.add_argument("--assignment-id", required=True)
    ap.add_argument("--server-url", default="http://127.0.0.1:19862")
    ap.add_argument("--model", default=MODEL_DEFAULT)
    ap.add_argument("--data-dir", default=DATA_DIR_DEFAULT)
    ap.add_argument("--adapter-0", default=None)
    ap.add_argument("--adapter-0-sha", default=None)
    ap.add_argument("--out-dir", default="/tmp/qwen_worker_out")
    ap.add_argument("--seq-len", type=int, default=128)
    ap.add_argument("--num-examples", type=int, default=10)
    ap.add_argument("--train-only", action="store_true",
                    help="fa el training local i surt SENSE el flux HTTP "
                    "(simula un crash després d'APPLIED)")
    ap.add_argument("--deterministic", action="store_true",
                    help="set_num_threads(1): forwards bit-exactes entre "
                    "execucions (per als tests de recovery)")
    args = ap.parse_args()
    if args.deterministic:
        _torch.set_num_threads(1)

    os.makedirs(args.out_dir, exist_ok=True)
    db_path = os.path.join(args.out_dir, f"journal_{args.worker_id}.db")
    ev_path = os.path.join(args.out_dir, f"evidence_{args.worker_id}.json")
    pid = os.getpid()

    # ── càrrega del model + adapter_0 ──
    bk = Qwen3TrainingBackend(args.model, db_path=db_path, seq_len=args.seq_len)
    bk.load()
    if args.adapter_0:
        with open(args.adapter_0, "rb") as f:
            ad0 = f.read()
        sha = args.adapter_0_sha or hashlib.sha256(ad0).hexdigest()
        bk.load_adapter_from_bundle(ad0, sha, strict=True)
    base_hash = bk.base_model_hash()
    adapter_pre_hash = bk.hash_adapter()

    # ── shard: exemples + manifest ──
    ds = Qwen3PilotDataset(args.data_dir)
    train = ds.train()
    lo = 0 if args.shard == "A" else 10
    examples = train[lo:lo + args.num_examples]
    example_ids = [f"{e['source']}" for e in examples]
    shard_mf = shard_manifest_sha(example_ids)

    # ── ETT registrat (dels labels reals) ──
    ett_registered = sum(
        bk.compute_ett(bk.tokenize_chat(e["instruction"], e["response"])[2])
        for e in examples)

    # ── 1. registrar-se ──
    rpc(args.server_url, "worker.register",
        {"worker_id": args.worker_id, "session_id": args.session_id,
         "base_model_hash": base_hash,
         "config_sha": bk._base_identity["config_sha"]})
    print(f"[{args.worker_id}] {pid} REGISTERED base={base_hash[:16]}", flush=True)

    # ── 2. calibrar-se (ETT registrat + manifest) ──
    rpc(args.server_url, "worker.calibrate",
        {"worker_id": args.worker_id, "session_id": args.session_id,
         "run_id": args.run_id, "round_id": args.round_id,
         "assignment_id": args.assignment_id, "shard_id": f"shard-{args.shard}",
         "shard_manifest_sha": shard_mf,
         "adapter_0_sha": hashlib.sha256(open(args.adapter_0, "rb").read()).hexdigest()
         if args.adapter_0 else "",
         "ett_registered": ett_registered})
    print(f"[{args.worker_id}] CALIBRATED ett={ett_registered}", flush=True)

    # ── 3. obtenir assignment ──
    a = rpc(args.server_url, "assignment.get",
            {"worker_id": args.worker_id, "session_id": args.session_id,
             "run_id": args.run_id, "round_id": args.round_id,
             "assignment_id": args.assignment_id})
    print(f"[{args.worker_id}] ASSIGNMENT shard={a['shard_id']}", flush=True)

    # ── 4. lease: renew de l'anterior (recovery) o acquire ──
    unit_id = f"unit-{args.assignment_id}"
    ls = None
    ev_prev = None
    if os.path.exists(ev_path):
        try:
            with open(ev_path) as f:
                ev_prev = json.load(f)
        except Exception:
            ev_prev = None
    if ev_prev and ev_prev.get("lease_id"):
        try:
            ls = rpc(args.server_url, "lease.renew",
                     {"lease_id": ev_prev["lease_id"],
                      "worker_id": args.worker_id,
                      "session_id": args.session_id,
                      "lease_nonce": ev_prev["lease_nonce"],
                      "ttl_seconds": 600})
            if not ls.get("lease_nonce"):
                ls["lease_nonce"] = ev_prev["lease_nonce"]  # el renew no el canvia
            print(f"[{args.worker_id}] LEASE RENEWED "
                  f"{ev_prev['lease_id'][:12]}", flush=True)
        except Exception:
            ls = None
    if ls is None:
        ls = rpc(args.server_url, "lease.acquire",
                 {"run_id": args.run_id, "round_id": args.round_id,
                  "assignment_id": args.assignment_id, "micro_unit_id": unit_id,
                  "worker_id": args.worker_id, "session_id": args.session_id,
                  "ttl_seconds": 600})
    lease = {"lease_id": ls["lease_id"], "lease_nonce": ls["lease_nonce"],
             "worker_id": args.worker_id, "session_id": args.session_id,
             "run_id": args.run_id, "round_id": args.round_id,
             "assignment_id": args.assignment_id, "micro_unit_id": unit_id}
    print(f"[{args.worker_id}] LEASE {ls['lease_id'][:12]} ttl={ls['expires_at']}", flush=True)

    # ── 5. step.open ──
    rpc(args.server_url, "step.open",
        {**lease, "unit_id": unit_id, "base_model_hash": base_hash,
         "adapter_pre_hash": adapter_pre_hash,
         "shard_manifest_sha": shard_mf, "ett_registered": ett_registered})
    print(f"[{args.worker_id}] step.open OK", flush=True)

    # ── 6. training Qwen local (auto-recuperable) ──
    t0 = time.time()
    ett_actual = 0
    for i, ex in enumerate(examples):
        uid = f"{args.worker_id}-{i}"
        if bk.journal_status(uid) == "APPLIED":
            bk.recover_applied(uid)   # reconstruir sense optimizer.step
            print(f"[{args.worker_id}] pas {i}: RECOVERED {uid}", flush=True)
        else:
            rs = bk._make_request_sha(uid, args.worker_id, args.assignment_id,
                                      f"shard-{args.shard}", bk.hash_adapter(),
                                      example_ids[i], ex["instruction"], ex["response"])
            _, _ = bk.train_step(uid, ex["instruction"], ex["response"],
                                 worker_id=args.worker_id,
                                 assignment_id=args.assignment_id,
                                 shard_id=f"shard-{args.shard}",
                                 example_id=example_ids[i], request_sha=rs)
        ett_actual += bk.compute_ett(
            bk.tokenize_chat(ex["instruction"], ex["response"])[2])
    train_time = time.time() - t0
    delta = sum_deltas(bk, args.worker_id, len(examples))
    delta_b64 = base64.b64encode(
        bk._serialize_delta({k: __import__("torch").as_tensor(v) for k, v in delta.items()})).decode("ascii")
    delta_sha = hashlib.sha256(base64.b64decode(delta_b64)).hexdigest()
    loss = bk._last_loss
    if loss is None:
        # procés de recovery pur (cap pas nou): mitjana de les losses del journal
        rows = bk._conn.execute(
            "SELECT loss FROM qwen_journal WHERE update_id LIKE ?",
            (f"{args.worker_id}-%",)).fetchall()
        losses = [r["loss"] for r in rows if r["loss"] is not None]
        loss = sum(losses) / len(losses) if losses else 0.0
    print(f"[{args.worker_id}] TRAINED {len(examples)} exemples ett={ett_actual} "
          f"delta={delta_sha[:16]} {train_time:.0f}s", flush=True)

    if args.train_only:
        # ── CRASH SIMULAT: el procés mor després d'APPLIED (journal persistent) ──
        ev = {"worker_id": args.worker_id, "session_id": args.session_id,
              "shard": args.shard, "pid": pid, "db_path": db_path,
              "base_model_hash": base_hash, "adapter_pre_hash": adapter_pre_hash,
              "shard_manifest_sha": shard_mf, "ett_registered": ett_registered,
              "ett_actual": ett_actual, "delta_sha": delta_sha,
              "lease_id": ls["lease_id"], "lease_nonce": ls["lease_nonce"],
              "train_only": True, "loss": loss,
              "adapter_post_hash": bk.hash_adapter(),
              "adapter_post_hash_t": bk.hash_adapter_tolerant()}
        with open(ev_path, "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False, indent=2)
        print(f"[{args.worker_id}] CRASH SIMULAT (train-only, sense HTTP)", flush=True)
        print("WORKER_DONE train-only", flush=True)
        sys.exit(0)

    # ── 7. step.worker_update.submit (APPLIED + delta/metadata) ──
    rs_unit = bk._make_request_sha(f"{args.worker_id}-0", args.worker_id,
                                   args.assignment_id, f"shard-{args.shard}",
                                   adapter_pre_hash, example_ids[0],
                                   examples[0]["instruction"], examples[0]["response"])
    upd = rpc(args.server_url, "step.worker_update.submit",
              {**lease, "unit_id": unit_id, "update_id": f"{args.worker_id}-all",
               "delta_id": delta_sha[:32], "example_ids": example_ids,
               "shard_manifest_sha": shard_mf, "ett": ett_actual,
               "delta_bundle_b64": delta_b64,
               "delta_bundle_sha256": delta_sha,
               "delta_bundle_byte_length": len(base64.b64decode(delta_b64)),
               "adapter_pre_hash": adapter_pre_hash,
               "base_model_hash": base_hash, "request_sha": rs_unit,
               "loss": loss})
    receipt = upd["receipt"]
    print(f"[{args.worker_id}] APPLIED receipt={receipt['receipt_id'][:16]}", flush=True)

    # ── 9. commit ──
    rpc(args.server_url, "step.commit", {**lease, "unit_id": unit_id})
    print(f"[{args.worker_id}] COMMITTED", flush=True)

    # ── 10. checkpoint.upload ──
    up = rpc(args.server_url, "checkpoint.upload",
             {**lease, "unit_id": unit_id, "receipt": receipt,
              "delta_bundle_b64": delta_b64})
    cid = up["contribution_id"]
    print(f"[{args.worker_id}] CHECKPOINT cid={cid[:16]}", flush=True)

    # ── 11. contribution.register + validate + activate ──
    rpc(args.server_url, "contribution.register",
        {**lease, "contribution_id": cid})
    rpc(args.server_url, "contribution.validate", {"contribution_id": cid})
    rpc(args.server_url, "contribution.activate", {"contribution_id": cid})
    print(f"[{args.worker_id}] CONTRIBUTION ACTIVE {cid[:16]}", flush=True)

    # ── 12. release ──
    rpc(args.server_url, "step.release", {**lease})
    print(f"[{args.worker_id}] RELEASED", flush=True)

    ev = {"worker_id": args.worker_id, "session_id": args.session_id,
          "shard": args.shard, "pid": pid, "db_path": db_path,
          "base_model_hash": base_hash, "adapter_pre_hash": adapter_pre_hash,
          "shard_manifest_sha": shard_mf, "ett_registered": ett_registered,
          "ett_actual": ett_actual, "delta_sha": delta_sha,
          "delta_b64_len": len(delta_b64), "receipt_id": receipt["receipt_id"],
          "contribution_id": cid, "loss": loss, "train_time_s": train_time,
          "lease_id": ls["lease_id"], "lease_nonce": ls["lease_nonce"],
          "adapter_post_hash": bk.hash_adapter(),
          "adapter_post_hash_t": bk.hash_adapter_tolerant()}
    with open(ev_path, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print(f"[{args.worker_id}] EVIDENCE {ev_path}", flush=True)
    print(f"WORKER_DONE {args.worker_id}", flush=True)


if __name__ == "__main__":
    main()
