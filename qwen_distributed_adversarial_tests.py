#!/usr/bin/env python3
"""qwen_distributed_adversarial_tests.py — ADVERSARIAL REAL (secció 12):
enforcement, no observacional. Cada anomalia -> REJECTED (o idempotent).

A1 wrong base model       -> REJECTED (worker.register base_model_hash diferent)
A2 wrong adapter SHA      -> REJECTED (backend strict, S1 del backend check)
A3 partial adapter        -> REJECTED (backend strict, S1 del backend check)
A4 stale adapter          -> REJECTED (submit amb adapter_pre_hash diferent)
A5 wrong shard            -> REJECTED (shard_manifest_sha mismatch)
A6 fake ETT               -> REJECTED (ett != ett_registered)
A7 same update_id diff payload -> REJECTED (request_sha mismatch a la unitat)
A8 expired lease          -> REJECTED
A9 wrong session          -> REJECTED (lease_binding_mismatch)
A10 duplicate contribution -> IDEMPOTENT (mateix cid, mateixa resposta)
A11 old worker after reassignment -> REJECTED
"""
import base64
import hashlib
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from qwen_http_server import serve

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def expect_rejected(fn, name):
    try:
        fn()
        check(name, False, "no REJECTED")
    except Exception as e:
        msg = str(e)
        ok = any(k in msg.lower() for k in ("rejected", "mismatch", "lease",
                                            "not_active", "expired",
                                            "no coincideix"))
        check(name, ok, f"{type(e).__name__}: {msg[:60]}")


def synth_delta(seed=1):
    import numpy as np
    import torch
    from qwen3_training_backend import Qwen3TrainingBackend
    rng = np.random.default_rng(seed)
    d = {"base_model.model.layers.0.q_proj.lora_A.weight": rng.standard_normal((2, 8)).astype("float32"),
         "base_model.model.layers.0.q_proj.lora_B.weight": rng.standard_normal((4, 2)).astype("float32")}
    b = Qwen3TrainingBackend._serialize_delta(
        {k: torch.as_tensor(v) for k, v in d.items()})
    return base64.b64encode(b).decode("ascii"), hashlib.sha256(b).hexdigest()


def main():
    for p in ("/tmp/qwen_adv_dist.db",):
        if os.path.exists(p):
            os.remove(p)
    httpd, proto = serve(port=19864, db_path="/tmp/qwen_adv_dist.db")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.5)

    base_h = "b" * 64
    proto.handle("worker.register", {"worker_id": "qwen-A", "session_id": "A1",
                                     "base_model_hash": base_h, "config_sha": "c" * 64})
    proto.handle("worker.calibrate", {"worker_id": "qwen-A", "session_id": "A1",
                                      "run_id": "run1", "round_id": "r1",
                                      "assignment_id": "asg-A", "shard_id": "shard-A",
                                      "shard_manifest_sha": "m" * 64,
                                      "adapter_0_sha": "d" * 64, "ett_registered": 100})

    # ── A1: wrong base model ──
    expect_rejected(lambda: proto.handle("worker.register", {
        "worker_id": "qwen-A", "session_id": "A1",
        "base_model_hash": "f" * 64, "config_sha": "c" * 64}),
        "A1 wrong base model REJECTED (register)")
    expect_rejected(lambda: proto.handle("step.open", {
        "run_id": "run1", "round_id": "r1", "assignment_id": "asg-A",
        "micro_unit_id": "u1", "worker_id": "qwen-A", "session_id": "A1",
        "base_model_hash": "f" * 64, "adapter_pre_hash": "p" * 64,
        "shard_manifest_sha": "m" * 64, "ett_registered": 100}),
        "A1 wrong base model REJECTED (open, sense lease)")

    # ── A4: stale adapter (submit amb adapter_pre_hash diferent del open) ──
    ls = proto.handle("lease.acquire", {"run_id": "run1", "round_id": "r1",
                                        "assignment_id": "asg-A",
                                        "micro_unit_id": "u2",
                                        "worker_id": "qwen-A", "session_id": "A1",
                                        "ttl_seconds": 600})
    le = {"lease_id": ls["lease_id"], "lease_nonce": ls["lease_nonce"],
          "worker_id": "qwen-A", "session_id": "A1", "run_id": "run1",
          "round_id": "r1", "assignment_id": "asg-A", "micro_unit_id": "u2"}
    d64, dsha = synth_delta(1)
    proto.handle("step.open", {**le, "unit_id": "u2", "base_model_hash": base_h,
                               "adapter_pre_hash": "p" * 64,
                               "shard_manifest_sha": "m" * 64, "ett_registered": 100})
    expect_rejected(lambda: proto.handle("step.worker_update.submit", {
        **le, "unit_id": "u2", "update_id": "u2-x", "delta_id": "y" * 32,
        "example_ids": ["train:0"], "shard_manifest_sha": "m" * 64, "ett": 100,
        "delta_bundle_b64": d64, "delta_bundle_sha256": dsha,
        "delta_bundle_byte_length": len(base64.b64decode(d64)),
        "adapter_pre_hash": "9" * 64,   # stale (differeix del open)
        "base_model_hash": base_h, "request_sha": "r" * 64, "loss": 1.0}),
        "A4 stale adapter REJECTED (adapter_pre_hash mismatch)")

    # ── A5: wrong shard ──
    expect_rejected(lambda: proto.handle("step.worker_update.submit", {
        **le, "unit_id": "u2", "update_id": "u2-y", "delta_id": "y" * 32,
        "example_ids": ["train:0"], "shard_manifest_sha": "9" * 64,  # shard B
        "ett": 100, "delta_bundle_b64": d64, "delta_bundle_sha256": dsha,
        "delta_bundle_byte_length": len(base64.b64decode(d64)),
        "adapter_pre_hash": "p" * 64, "base_model_hash": base_h,
        "request_sha": "r" * 64, "loss": 1.0}),
        "A5 wrong shard REJECTED (shard_manifest_sha mismatch)")

    # ── A6: fake ETT ──
    expect_rejected(lambda: proto.handle("step.worker_update.submit", {
        **le, "unit_id": "u2", "update_id": "u2-z", "delta_id": "y" * 32,
        "example_ids": ["train:0"], "shard_manifest_sha": "m" * 64,
        "ett": 999999,   # inventat (registrat=100)
        "delta_bundle_b64": d64, "delta_bundle_sha256": dsha,
        "delta_bundle_byte_length": len(base64.b64decode(d64)),
        "adapter_pre_hash": "p" * 64, "base_model_hash": base_h,
        "request_sha": "r" * 64, "loss": 1.0}),
        "A6 fake ETT REJECTED (ett != registrat)")

    # ── A7: mateix update_id, payload diferent (request_sha mismatch) ──
    upd = proto.handle("step.worker_update.submit", {
        **le, "unit_id": "u2", "update_id": "u2-ok", "delta_id": "y" * 32,
        "example_ids": ["train:0"], "shard_manifest_sha": "m" * 64, "ett": 100,
        "delta_bundle_b64": d64, "delta_bundle_sha256": dsha,
        "delta_bundle_byte_length": len(base64.b64decode(d64)),
        "adapter_pre_hash": "p" * 64, "base_model_hash": base_h,
        "request_sha": "r" * 64, "loss": 1.0})
    check("A7 submit vàlid (base)", upd["state"] == "APPLIED")
    expect_rejected(lambda: proto.handle("step.worker_update.submit", {
        **le, "unit_id": "u2", "update_id": "u2-ok", "delta_id": "y" * 32,
        "example_ids": ["train:0"], "shard_manifest_sha": "m" * 64, "ett": 100,
        "delta_bundle_b64": d64, "delta_bundle_sha256": dsha,
        "delta_bundle_byte_length": len(base64.b64decode(d64)),
        "adapter_pre_hash": "p" * 64, "base_model_hash": base_h,
        "request_sha": "9" * 64, "loss": 1.0}),   # request_sha diferent
        "A7 mateixa unitat, request_sha diferent REJECTED")

    # ── A8: expired lease ──
    proto.handle("step.commit", {**le, "unit_id": "u2"})
    ls2 = proto.handle("lease.acquire", {"run_id": "run1", "round_id": "r1",
                                         "assignment_id": "asg-A",
                                         "micro_unit_id": "u3",
                                         "worker_id": "qwen-A", "session_id": "A1",
                                         "ttl_seconds": 1})
    le2 = {**le, "lease_id": ls2["lease_id"], "lease_nonce": ls2["lease_nonce"],
           "micro_unit_id": "u3"}
    time.sleep(1.5)   # expira (ttl 1s)
    expect_rejected(lambda: proto.handle("step.open", {
        **le2, "unit_id": "u3", "base_model_hash": base_h,
        "adapter_pre_hash": "p" * 64, "shard_manifest_sha": "m" * 64,
        "ett_registered": 100}),
        "A8 expired lease REJECTED")

    # ── A9: wrong session ──
    ls3 = proto.handle("lease.acquire", {"run_id": "run1", "round_id": "r1",
                                         "assignment_id": "asg-A",
                                         "micro_unit_id": "u4",
                                         "worker_id": "qwen-A", "session_id": "A1",
                                         "ttl_seconds": 600})
    le3 = {**le, "lease_id": ls3["lease_id"], "lease_nonce": ls3["lease_nonce"],
           "micro_unit_id": "u4"}
    expect_rejected(lambda: proto.handle("step.open", {
        **le3, "session_id": "SESSION-ALIENA", "unit_id": "u4",
        "base_model_hash": base_h, "adapter_pre_hash": "p" * 64,
        "shard_manifest_sha": "m" * 64, "ett_registered": 100}),
        "A9 wrong session REJECTED (lease_binding_mismatch)")

    # ── A10: duplicate contribution -> idempotent ──
    proto.handle("step.open", {**le3, "unit_id": "u4", "base_model_hash": base_h,
                               "adapter_pre_hash": "p" * 64,
                               "shard_manifest_sha": "m" * 64, "ett_registered": 100})
    upd4 = proto.handle("step.worker_update.submit", {
        **le3, "unit_id": "u4", "update_id": "u4-ok", "delta_id": "y" * 32,
        "example_ids": ["train:0"], "shard_manifest_sha": "m" * 64, "ett": 100,
        "delta_bundle_b64": d64, "delta_bundle_sha256": dsha,
        "delta_bundle_byte_length": len(base64.b64decode(d64)),
        "adapter_pre_hash": "p" * 64, "base_model_hash": base_h,
        "request_sha": "r" * 64, "loss": 1.0})
    proto.handle("step.commit", {**le3, "unit_id": "u4"})
    up1 = proto.handle("checkpoint.upload", {**le3, "unit_id": "u4",
                                             "receipt": upd4["receipt"],
                                             "delta_bundle_b64": d64})
    up2 = proto.handle("checkpoint.upload", {**le3, "unit_id": "u4",
                                             "receipt": upd4["receipt"],
                                             "delta_bundle_b64": d64})
    check("A10 duplicate contribution IDEMPOTENT (mateixa resposta)", up1 == up2,
          up1["contribution_id"][:16])

    # ── A11: old worker after reassignment ──
    # reassignem la unitat u2 (ja COMMITTED) a un altre worker: la lease del
    # worker antic ja no és ACTIVE -> REJECTED
    proto.handle("step.release", {**le})
    expect_rejected(lambda: proto.handle("step.commit", {**le, "unit_id": "u2"}),
                    "A11 old worker after reassignment REJECTED")

    httpd.shutdown()
    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== ADVERSARIAL DISTRIBUT: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
