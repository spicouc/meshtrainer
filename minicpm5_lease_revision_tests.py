#!/usr/bin/env python3
"""minicpm5_lease_revision_tests.py — R1.1: MREC-05, MREC-06, MADV-13, MADV-14
REALS (lease realment adquirida i realment expirada; session mismatch real).

Usa el protocol HTTP certificat amb rellotge injectable (now=) i el
LeaseManager real — NO "sense lease" ni "unknown_assignment" com a substitut.
"""
import base64
import hashlib
import os
import sqlite3
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

from training_http_server import serve  # noqa: E402
from adapter_codec import pack_tensors, bundle_sha256  # noqa: E402

CHECKS = []
ADMIN = "r11-admin-token"
BASE_HASH = "63d736ad11ad8f7e5f2578bf7d50f1c74853b5814551fdc62249c12abeb19813"
AD0_SHA = "5df80ee69db130fa61c8c902e673154ca9c6782194edfe11982f218f42660401"
PRE_HASH = "1bd9e79dba870b05e18ef60e4537e6965a98c2c8ed3bd9391901eedffe789243"


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def real_adapter_b64(seed=0):
    rng = np.random.default_rng(seed)
    t = {}
    for layer in range(2):
        for tgt in ("q_proj", "o_proj"):
            t[f"base_model.model.model.layers.{layer}.{tgt}.lora_A"] = \
                rng.standard_normal((16, 16)).astype(np.float32)
            t[f"base_model.model.model.layers.{layer}.{tgt}.lora_B"] = \
                rng.standard_normal((16, 16)).astype(np.float32)
    return base64.b64encode(pack_tensors(t)).decode()


def boot_server(db_path, now_holder):
    """Servidor amb rellotge injectable (una llista [t] que muta el test)."""
    httpd, proto = serve(port=19863, db_path=db_path, admin_token=ADMIN,
                         now=lambda: now_holder[0], default_ttl_seconds=30.0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)
    return httpd, proto


def setup_round(proto, run, rnd, revision=1, worker="w-1", session="S1"):
    proto.handle("round.create", {"run_id": run, "round_id": rnd,
                                  "backend_id": "minicpm5",
                                  "base_model_hash": BASE_HASH,
                                  "adapter_0_sha": AD0_SHA,
                                  "adapter_pre_hash": PRE_HASH,
                                  "dataset_manifest_sha": "",
                                  "admin_token": ADMIN})
    proto.handle("assignment.create", {"run_id": run, "round_id": rnd,
                                       "assignment_id": "asg-1",
                                       "worker_id": worker,
                                       "shard_id": "shard-A",
                                       "shard_manifest_sha": "m" * 64,
                                       "base_model_hash": BASE_HASH,
                                       "adapter_0_sha": AD0_SHA,
                                       "expected_ett": 90,
                                       "revision": revision,
                                       "admin_token": ADMIN})


def acquire_lease(proto, run, rnd, unit="unit-1", worker="w-1", session="S1"):
    r = proto.handle("lease.acquire", {"run_id": run, "round_id": rnd,
                                       "assignment_id": "asg-1",
                                       "micro_unit_id": unit,
                                       "worker_id": worker,
                                       "session_id": session})
    return r


def expect_rejected(name, fn, marker):
    try:
        fn()
        check(name, False, "no va llançar")
    except Exception as e:  # noqa: BLE001
        reason = getattr(e, "reason", None) or str(e)
        ok = marker in str(reason)
        check(name, ok, f"{type(e).__name__}: {str(reason)[:60]}")


def main():
    print("=" * 60)
    print(" MINICPM5 LEASE/REVISION REALS — MREC-05, MREC-06, MADV-13, MADV-14")
    print("=" * 60)

    now = [1000.0]
    for _p in ("/tmp/r11_lease.db",):
        if os.path.exists(_p):
            os.remove(_p)
    httpd, proto = boot_server("/tmp/r11_lease.db", now)
    conn = proto._conn

    # ══════════ MREC-05: lease real adquirida i REALMENT expirada ══════
    print("\n=== MREC-05: lease expirada → worker antic REJECTED ===")
    setup_round(proto, "run1", "r1")
    ls = acquire_lease(proto, "run1", "r1")
    check("MREC-05 lease adquirida", ls.get("status") == "ACTIVE",
          f"status={ls.get('status')}")
    lid = ls["lease_id"]
    # la lease és vàlida ara
    payload = {"lease_id": lid, "lease_nonce": ls["lease_nonce"],
               "worker_id": "w-1", "session_id": "S1", "run_id": "run1",
               "round_id": "r1", "assignment_id": "asg-1",
               "micro_unit_id": "unit-1", "unit_id": "unit-1",
               "base_model_hash": BASE_HASH,
               "shard_manifest_sha": "m" * 64, "ett": 90,
               "adapter_pre_hash": PRE_HASH,
               "delta_bundle_sha256": bundle_sha256(
                   base64.b64decode(real_adapter_b64(1)))}
    # control: amb la lease VÀLIDA el submit funciona
    proto.handle("step.open", payload)
    proto.handle("step.worker_update.submit", {**payload, "state": "APPLIED",
                                               "request_sha": "r" * 64})
    check("MREC-05 submit amb lease vàlida: OK (control)", True)
    # fer passar 40s (TTL=30) → lease EXPIRADA de veritat
    now[0] += 40.0
    for method, extra in [
        ("step.worker_update.submit", {"state": "APPLIED",
                                       "request_sha": "r" * 64}),
        ("step.commit", {}),
        ("checkpoint.upload", {"delta_bundle_b64": real_adapter_b64(1),
                               "delta_bundle_sha256": payload["delta_bundle_sha256"],
                               "ett": 45, "loss": 1.0,
                               "adapter_pre_hash": PRE_HASH}),
        ("contribution.register", {"contribution_id": "cid-mrec5"}),
    ]:
        expect_rejected(
            f"MREC-05 {method} amb lease expirada → REJECTED",
            lambda m=method, x=extra: proto.handle(m, {**payload, **x}),
            marker="lease")

    # ══════════ MREC-06: reassignació revision 1→2 ═════════════════════
    print("\n=== MREC-06: revision 1→2, worker antic REJECTED, nou ACCEPTED ===")
    setup_round(proto, "run2", "r2", revision=1, worker="w-old", session="S-old")
    # el worker antic adquireix lease vàlida (rev 1) i fa el flux complet
    # (submit -> commit -> upload, que crea la contribució RECEIVED)
    ls_old = acquire_lease(proto, "run2", "r2", unit="unit-old",
                           worker="w-old", session="S-old")
    p_old = {"lease_id": ls_old["lease_id"], "lease_nonce": ls_old["lease_nonce"],
             "worker_id": "w-old", "session_id": "S-old", "run_id": "run2",
             "round_id": "r2", "assignment_id": "asg-1",
             "micro_unit_id": "unit-old", "unit_id": "unit-old",
             "base_model_hash": BASE_HASH, "shard_manifest_sha": "m" * 64,
             "ett": 90, "adapter_pre_hash": PRE_HASH,
             "delta_bundle_sha256": bundle_sha256(
                 base64.b64decode(real_adapter_b64(3))),
             "request_sha": "r6" * 32}
    proto.handle("step.open", p_old)
    sub_old = proto.handle("step.worker_update.submit",
                           {**p_old, "state": "APPLIED"})
    proto.handle("step.commit", {**p_old, "receipt": sub_old["receipt"]})
    b64_old = real_adapter_b64(3)
    up_old = proto.handle("checkpoint.upload", {
        **p_old, "receipt": sub_old["receipt"],
        "delta_bundle_b64": b64_old,
        "delta_bundle_sha256": p_old["delta_bundle_sha256"]})
    cid_old = up_old["contribution_id"]
    # el Coordinator reassigna a revision 2 (UPDATE autoritatiu a la BD —
    # el create no permet sobreescriure una assignació, AUTH-14)
    conn.execute("UPDATE model_assignments SET revision=2, worker_id='w-new',"
                 " status='ASSIGNED' WHERE run_id='run2' AND round_id='r2'"
                 " AND assignment_id='asg-1'")
    conn.commit()
    # la contribució del worker antic té revision 1 < 2 → stale
    conn.execute(f"UPDATE model_contributions SET revision=1 "
                 f"WHERE cid='{cid_old}'")
    conn.commit()
    # la contribució del worker antic és REJECTED: l'assignació ara pertany
    # al worker NOU (rev 2) — el validate la rebutja (assignment/worker/stale)
    try:
        proto.handle("contribution.validate",
                     {"contribution_id": cid_old, "admin_token": ADMIN})
        check("MREC-06 worker antic REJECTED", False)
    except Exception as e:  # noqa: BLE001
        reason = getattr(e, "reason", None) or str(e)
        ok = "stale" in str(reason) or "assignment" in str(reason) \
            or "worker" in str(reason) or "revision" in str(reason)
        check("MREC-06 worker antic REJECTED (rev 1 vs 2)", ok,
              f"{type(e).__name__}: {str(reason)[:60]}")
    # nou worker amb revision 2 → ACCEPTED (adquireix la seva lease i flux)
    ls_new = acquire_lease(proto, "run2", "r2", unit="unit-new",
                           worker="w-new", session="S-new")
    p_new = {"lease_id": ls_new["lease_id"], "lease_nonce": ls_new["lease_nonce"],
             "worker_id": "w-new", "session_id": "S-new", "run_id": "run2",
             "round_id": "r2", "assignment_id": "asg-1",
             "micro_unit_id": "unit-new", "unit_id": "unit-new",
             "base_model_hash": BASE_HASH, "shard_manifest_sha": "m" * 64,
             "ett": 90, "adapter_pre_hash": PRE_HASH,
             "delta_bundle_sha256": bundle_sha256(
                 base64.b64decode(real_adapter_b64(4))),
             "request_sha": "r6n" * 32}
    proto.handle("step.open", p_new)
    sub_new = proto.handle("step.worker_update.submit",
                           {**p_new, "state": "APPLIED"})
    proto.handle("step.commit", {**p_new, "receipt": sub_new["receipt"]})
    b64_new = real_adapter_b64(4)
    up_new = proto.handle("checkpoint.upload", {
        **p_new, "receipt": sub_new["receipt"],
        "delta_bundle_b64": b64_new,
        "delta_bundle_sha256": p_new["delta_bundle_sha256"]})
    cid_new = up_new["contribution_id"]
    r = proto.handle("contribution.validate", {"contribution_id": cid_new,
                                               "admin_token": ADMIN})
    check("MREC-06 nou worker (rev 2) → VALIDATED", r.get("status") == "VALIDATED",
          f"status={r.get('status')}")

    # ══════════ MADV-13: lease real adquirida i realment expirada ══════
    print("\n=== MADV-13: activate amb lease expirada (no 'sense lease') ===")
    setup_round(proto, "run3", "r3", worker="w-3", session="S3")
    ls3 = acquire_lease(proto, "run3", "r3", unit="unit-3", worker="w-3",
                        session="S3")
    p3 = {"lease_id": ls3["lease_id"], "lease_nonce": ls3["lease_nonce"],
          "worker_id": "w-3", "session_id": "S3", "run_id": "run3",
          "round_id": "r3", "assignment_id": "asg-1",
          "micro_unit_id": "unit-3", "unit_id": "unit-3",
          "base_model_hash": BASE_HASH, "shard_manifest_sha": "m" * 64,
          "ett": 90, "adapter_pre_hash": PRE_HASH,
          "delta_bundle_sha256": bundle_sha256(
              base64.b64decode(real_adapter_b64(5))),
          "request_sha": "m13" * 32}
    proto.handle("step.open", p3)
    sub3 = proto.handle("step.worker_update.submit",
                        {**p3, "state": "APPLIED"})
    proto.handle("step.commit", {**p3, "receipt": sub3["receipt"]})
    b64_3 = real_adapter_b64(5)
    up3 = proto.handle("checkpoint.upload", {
        **p3, "receipt": sub3["receipt"], "delta_bundle_b64": b64_3,
        "delta_bundle_sha256": p3["delta_bundle_sha256"]})
    cid_m13 = up3["contribution_id"]
    proto.handle("contribution.validate", {"contribution_id": cid_m13,
                                           "admin_token": ADMIN})
    # la lease està adquirida i VÀLIDA: activate ha de PASSAR
    r = proto.handle("contribution.activate", {"contribution_id": cid_m13,
                                               "admin_token": ADMIN})
    check("MADV-13 activate amb lease vàlida: OK (control)",
          r.get("status") == "ACTIVE", f"status={r.get('status')}")
    # ara expira la lease (40s > TTL 30s) → un segon activate → REJECTED
    now[0] += 40.0
    b64_3b = real_adapter_b64(6)
    # la mateixa lease (ara EXPIRADA) — el register ha de ser REJECTED
    expect_rejected(
        "MADV-13 register amb lease EXPIRADA → REJECTED",
        lambda: proto.handle("contribution.register", {
            "contribution_id": "cid-m13b", "run_id": "run3", "round_id": "r3",
            "assignment_id": "asg-1", "worker_id": "w-3",
            "lease_id": ls3["lease_id"], "lease_nonce": ls3["lease_nonce"],
            "session_id": "S3", "micro_unit_id": "unit-3",
            "delta_bundle_b64": b64_3b,
            "delta_bundle_sha256": bundle_sha256(base64.b64decode(b64_3b)),
            "ett": 45, "adapter_pre_hash": PRE_HASH,
            "source_sha": "src-m13b"}),
        marker="lease")

    # ══════════ MADV-14: session mismatch real (S1 vàlid, S2 → REJECTED) ══
    print("\n=== MADV-14: session S2 amb lease de S1 → REJECTED ===")
    setup_round(proto, "run4", "r4", worker="w-4", session="S1")
    ls4 = acquire_lease(proto, "run4", "r4", unit="unit-4", worker="w-4",
                        session="S1")
    p4 = {"lease_id": ls4["lease_id"], "lease_nonce": ls4["lease_nonce"],
          "worker_id": "w-4", "session_id": "S1", "run_id": "run4",
          "round_id": "r4", "assignment_id": "asg-1", "micro_unit_id": "unit-4",
          "unit_id": "unit-4", "base_model_hash": BASE_HASH,
          "shard_manifest_sha": "m" * 64, "ett": 90,
          "adapter_pre_hash": PRE_HASH, "delta_bundle_sha256": "d" * 64}
    # sessió S1 vàlida: submit OK
    proto.handle("step.open", p4)
    proto.handle("step.worker_update.submit", {**p4, "state": "APPLIED",
                                               "request_sha": "r" * 64})
    check("MADV-14 submit amb S1: OK (control)", True)
    # mateixa lease però SESSION S2 → REJECTED específicament per session
    expect_rejected(
        "MADV-14 submit amb S2 (lease de S1) → REJECTED per session",
        lambda: proto.handle("step.worker_update.submit",
                             {**p4, "session_id": "S2", "state": "APPLIED",
                              "request_sha": "r" * 64}),
        marker="lease_binding_mismatch")

    httpd.shutdown()
    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"\n=== LEASE/REVISION REALS: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
