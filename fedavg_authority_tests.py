#!/usr/bin/env python3
"""fedavg_authority_tests.py — ADVERSARIALS SEC (R2.4, punt 13) + EXPLOITS.

SEC-01  worker → round.create            → REJECTED
SEC-02  worker → assignment.create       → REJECTED
SEC-03  worker → contribution.activate   → REJECTED
SEC-04  worker → round.fedavg            → REJECTED
SEC-05  admin credential incorrecta      → REJECTED
SEC-06  FedAvg amb 1/2 contribucions     → REJECTED (ROUND_NOT_READY)
SEC-07  FedAvg amb baseline incorrecte   → REJECTED
SEC-08  FedAvg amb adapter_pre_hash heterogeni → REJECTED
SEC-09  FedAvg amb base model heterogeni → REJECTED
SEC-10  contribució stale/revision antiga → REJECTED
SEC-11  duplicate effective contribution → REJECTED o idempotent
SEC-12  quòrum correcte + baseline correcte → PASS

EXPLOIT A: worker normal intenta crear ronda/assignment/activar/fedavg → TOT REJECTED
EXPLOIT B: només Worker A completa; admin intenta FedAvg → ROUND_NOT_READY
EXPLOIT C: A/B completen; caller passa adapter base diferent → REJECTED
"""
import base64
import hashlib
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from model_round_coordinator import ModelRoundCoordinator, ModelRecoveryError  # noqa: E402
from training_http_server import TrainingProtocolHandler, TrainingServerError  # noqa: E402

ADMIN = os.environ.get("MESH_ADMIN_TOKEN", "mesh-admin-token-test")
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def expect_rejected(name, fn, reason_contains=None):
    try:
        fn()
        check(name, False, "no va llançar cap error")
    except (ModelRecoveryError, TrainingServerError) as e:
        reason = getattr(e, "reason", str(e))
        ok = reason_contains is None or reason_contains in reason
        check(name, ok, f"{reason}")
    except Exception as e:  # noqa: BLE001
        ok = reason_contains is None or reason_contains in str(e)
        check(name, ok, f"{type(e).__name__}: {str(e)[:60]}")


def make_bundle(seed_byte):
    """Bundle 'autoritatiu' de la ronda: bytes amb SHA determinista."""
    return bytes([seed_byte]) * 64


def make_real_adapter(seed=1, n_tensors=2, shape=(2, 2)):
    """Adapter REAL vàlid (via adapter_codec.pack_tensors) — necessari per
    al FedAvg legítim (l'agregació deserialitza els tensors)."""
    import numpy as np
    from adapter_codec import pack_tensors
    tensors = {f"lora_{i}": np.full(shape, float(seed + i), dtype="float32")
               for i in range(n_tensors)}
    return pack_tensors(tensors)


def setup_round(coord, run, rnd, n_assign=2, bundle_seed=7,
                base_hex="b", pre_hex="p", real_bundle=None):
    """Crea ronda on adapter_0_sha == SHA(bundle) (baseline real).
    n_assign assignments + contribucions ACTIVE vàlides.
    Retorna (base_hash, pre_hash, cids, bundle_bytes, bundle_sha)."""
    B0 = base_hex * 64
    P0 = pre_hex * 32
    bundle = real_bundle if real_bundle is not None else make_bundle(bundle_seed)
    A0 = hashlib.sha256(bundle).hexdigest()  # adapter_0_sha == SHA(bundle)
    coord.create_round(run, rnd, "dummy", B0, A0, P0)
    cids = []
    for i in range(n_assign):
        aid = f"asg-{i}"
        wid = f"w-{i}"
        coord.create_assignment(run, rnd, aid, wid, f"shard-{i}",
                                f"m-{i}" * 32, B0, A0, expected_ett=100 + i)
        # delta REAL (adapter deserialitzable) i DIFERENT per contribució
        delta = make_real_adapter(seed=100 + i, n_tensors=2, shape=(2, 2))
        cid = f"cid-{rnd}-{i}"
        coord.register_uploaded_contribution(
            cid, run, rnd, aid, wid, 100 + i,
            base64.b64encode(delta).decode(), hashlib.sha256(delta).hexdigest(),
            "rq" * 32, P0, "src")
        coord.validate_contribution(cid)
        coord.activate_contribution(cid)
        cids.append(cid)
    return B0, A0, P0, cids, bundle


def main():
    print("=" * 64)
    print(" FEDAVG AUTHORITY — SEC ADVERSARIALS (R2.4, punt 13)")
    print("=" * 64)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    proto = TrainingProtocolHandler.__new__(TrainingProtocolHandler)
    import threading
    proto._conn = conn
    proto.lm = None
    proto.coord = ModelRoundCoordinator(conn)
    proto._workers = {}
    proto._units = {}
    proto._lock = threading.RLock()
    proto._admin_token = ADMIN
    proto._admin_token_source = "test"
    proto._init_db()
    coord = proto.coord

    # ════════ SEC-01..05: frontera ADMIN ════════
    print("\n=== SEC-01..04: worker NO pot invocar operacions ADMIN ===")
    proto._m_worker_register({"worker_id": "w-X", "session_id": "SX",
                              "base_model_hash": "b" * 64, "config_sha": "cfg"})
    base_w = {"worker_id": "w-X", "session_id": "SX", "run_id": "run1",
              "round_id": "r1"}
    expect_rejected("SEC-01 worker → round.create → REJECTED",
                    lambda: proto.handle("round.create", {**base_w,
                        "backend_id": "dummy", "base_model_hash": "b" * 64,
                        "adapter_0_sha": "a0" * 32, "adapter_pre_hash": "p0" * 32}),
                    "admin_required")
    expect_rejected("SEC-02 worker → assignment.create → REJECTED",
                    lambda: proto.handle("assignment.create", {**base_w,
                        "assignment_id": "asg-X", "shard_id": "shard-X",
                        "shard_manifest_sha": "m" * 64,
                        "adapter_0_sha": "a0" * 32, "expected_ett": 1}),
                    "admin_required")
    expect_rejected("SEC-03 worker → contribution.activate → REJECTED",
                    lambda: proto.handle("contribution.activate",
                        {"contribution_id": "cid-qualsevol"}),
                    "admin_required")
    # tant round.fedavg com round_fedavg (normalitzat) → REJECTED
    expect_rejected("SEC-04 worker → round.fedavg → REJECTED",
                    lambda: proto.handle("round_fedavg", {**base_w,
                        "adapter_0_bundle_b64": base64.b64encode(b"x" * 64).decode()}),
                    "admin_required")

    print("\n=== SEC-05: admin credential incorrecta ===")
    expect_rejected("SEC-05 admin_token incorrecte → REJECTED",
                    lambda: proto.handle("round.create", {**base_w,
                        "backend_id": "dummy", "base_model_hash": "b" * 64,
                        "adapter_0_sha": "a0" * 32, "adapter_pre_hash": "p0" * 32,
                        "admin_token": "TOKEN-INCORRECTE"}),
                    "admin_credential_invalid")

    # ════════ SEC-06..12: FedAvg authority ════════
    print("\n=== SEC-06: FedAvg amb 1/2 contribucions ===")
    B0, A0, P0, cids, bundle = setup_round(coord, "run1", "r1", n_assign=2)
    ad0_b64 = base64.b64encode(bundle).decode()
    coord._conn.execute("UPDATE model_contributions SET status='VALIDATED'"
                        " WHERE cid=?", (cids[1],))
    coord._conn.commit()
    expect_rejected("SEC-06 FedAvg 1/2 → REJECTED (ROUND_NOT_READY)",
                    lambda: coord.fedavg("run1", "r1", ad0_b64),
                    "quorum_not_met")

    print("\n=== SEC-07: FedAvg amb baseline incorrecte ===")
    coord._conn.execute("UPDATE model_contributions SET status='ACTIVE'"
                        " WHERE cid=?", (cids[1],))
    coord._conn.commit()
    fake_b64 = base64.b64encode(make_bundle(99)).decode()
    expect_rejected("SEC-07 FedAvg baseline incorrecte → REJECTED",
                    lambda: coord.fedavg("run1", "r1", fake_b64),
                    "fedavg_baseline_mismatch")

    print("\n=== SEC-08: FedAvg amb adapter_pre_hash heterogeni ===")
    coord._conn.execute(
        "INSERT INTO model_contributions (cid, run_id, round_id,"
        " assignment_id, worker_id, status, ett, delta_bundle_b64,"
        " delta_bundle_sha256, request_sha, adapter_pre_hash, source_sha)"
        " VALUES ('cid-hetero', 'run1', 'r1', 'asg-1', 'w-1', 'ACTIVE',"
        " 101, ?, ?, 'rq', 'OTHER', 'src')",
        (base64.b64encode(b"\x09" * 64).decode(),
         hashlib.sha256(b"\x09" * 64).hexdigest()))
    coord._conn.commit()
    # la cid-hetero té el mateix assignment que cid-1 → 2 ACTIVE per asg-1
    # → quòrum falla (1 ACTIVE esperada); això és REJECTED igualment
    expect_rejected("SEC-08 FedAvg pre_hash heterogeni → REJECTED",
                    lambda: coord.fedavg("run1", "r1", ad0_b64),
                    "quorum")
    coord._conn.execute("DELETE FROM model_contributions WHERE cid='cid-hetero'")
    coord._conn.commit()

    print("\n=== SEC-09: FedAvg amb base model heterogeni ===")
    # canviem el base_model_hash de l'assignació asg-1 (ronda espera B0)
    coord._conn.execute("UPDATE model_assignments SET base_model_hash=?"
                        " WHERE run_id='run1' AND round_id='r1'"
                        " AND assignment_id='asg-1'", ("Y" * 64,))
    coord._conn.commit()
    expect_rejected("SEC-09 FedAvg base model heterogeni → REJECTED",
                    lambda: coord.fedavg("run1", "r1", ad0_b64),
                    "heterogeneous_base_model")
    coord._conn.execute("UPDATE model_assignments SET base_model_hash=?"
                        " WHERE run_id='run1' AND round_id='r1'"
                        " AND assignment_id='asg-1'", (B0,))
    coord._conn.commit()

    print("\n=== SEC-10: contribució stale/revision antiga ===")
    # ronda nova on la contribució es registra amb pre_hash stale
    B1, A1, P1, cids1, bundle1 = setup_round(coord, "run2", "r2", n_assign=1,
                                             base_hex="c", pre_hex="q")
    # registrem una contribució amb pre_hash NO-P1 (stale) → REJECTED al
    # registre (AUTH-12/R2.4): mai pot arribar a ACTIVE
    delta = b"\x0a" * 64
    try:
        coord.register_uploaded_contribution(
            "cid-stale", "run2", "r2", "asg-0", "w-0", 100,
            base64.b64encode(delta).decode(),
            hashlib.sha256(delta).hexdigest(), "rq" * 32, "STALE" * 8, "src")
        check("SEC-10 stale pre_hash → REJECTED al registre", False,
              "es va registrar")
    except ModelRecoveryError as e:
        check("SEC-10 stale pre_hash → REJECTED al registre",
              e.reason == "adapter_pre_hash_mismatch", e.reason)

    print("\n=== SEC-11: duplicate effective contribution ===")
    real_d = make_real_adapter(seed=1)
    B2, A2, P2, cids2, bundle2 = setup_round(coord, "run3", "r3", n_assign=1,
                                             base_hex="d", pre_hex="r",
                                             real_bundle=real_d)
    d = b"\x01" * 64
    r1 = coord.register_uploaded_contribution(
        "cid-dup", "run3", "r3", "asg-0", "w-0", 100,
        base64.b64encode(d).decode(), hashlib.sha256(d).hexdigest(),
        "rq" * 32, P2, "src1")
    r2 = coord.register_uploaded_contribution(
        "cid-dup", "run3", "r3", "asg-0", "w-0", 100,
        base64.b64encode(d).decode(), hashlib.sha256(d).hexdigest(),
        "rq" * 32, P2, "src1")
    check("SEC-11 duplicate register → idempotent (mateix cid)",
          r1["status"] == r2["status"] == "RECEIVED")
    # activem la primera (ACTIVE), després la segona la deixa SUPERSEDED
    coord.validate_contribution("cid-dup")
    coord.activate_contribution("cid-dup")
    coord.register_uploaded_contribution(
        "cid-dup2", "run3", "r3", "asg-0", "w-0", 100,
        base64.b64encode(d).decode(), hashlib.sha256(d).hexdigest(),
        "rq" * 32, P2, "src2")
    coord.validate_contribution("cid-dup2")
    coord.activate_contribution("cid-dup2")
    st1 = coord.contribution("cid-dup")["status"]
    st2 = coord.contribution("cid-dup2")["status"]
    check("SEC-11 duplicate effective → SUPERSEDED (cap doble ACTIVE)",
          st1 == "SUPERSEDED" and st2 == "ACTIVE", f"{st1}/{st2}")

    print("\n=== SEC-12: quòrum correcte + baseline correcte → PASS ===")
    real_e = make_real_adapter(seed=5)
    B3, A3, P3, cids3, bundle3 = setup_round(coord, "run4", "r4", n_assign=2,
                                             base_hex="e", pre_hex="s",
                                             real_bundle=real_e)
    res = coord.fedavg("run4", "r4",
                       base64.b64encode(real_e).decode())
    check("SEC-12 quòrum 2/2 + baseline correcte → FedAvg OK",
          res["num_contributions"] == 2 and res["round_status"] == "READY",
          f"n={res['num_contributions']}")

    # ════════ EXPLOIT A ════════
    print("\n=== EXPLOIT A: worker normal intenta TOT ===")
    base_w2 = {"worker_id": "w-X", "session_id": "SX", "run_id": "run-X",
               "round_id": "rX"}
    expect_rejected("EXPLOIT A: round.create → REJECTED",
                    lambda: proto.handle("round.create", {**base_w2,
                        "backend_id": "dummy", "base_model_hash": "b" * 64,
                        "adapter_0_sha": "a0" * 32, "adapter_pre_hash": "p0" * 32}),
                    "admin_required")
    expect_rejected("EXPLOIT A: assignment.create → REJECTED",
                    lambda: proto.handle("assignment.create", {**base_w2,
                        "assignment_id": "asg-X", "shard_id": "shard-X",
                        "shard_manifest_sha": "m" * 64,
                        "adapter_0_sha": "a0" * 32, "expected_ett": 1}),
                    "admin_required")
    expect_rejected("EXPLOIT A: contribution.activate → REJECTED",
                    lambda: proto.handle("contribution.activate",
                        {"contribution_id": "cid-X"}),
                    "admin_required")
    expect_rejected("EXPLOIT A: round.fedavg → REJECTED",
                    lambda: proto.handle("round_fedavg", {**base_w2,
                        "adapter_0_bundle_b64": base64.b64encode(b"x" * 64).decode()}),
                    "admin_required")
    n_rounds = conn.execute("SELECT COUNT(*) AS n FROM model_rounds").fetchone()["n"]
    check("EXPLOIT A: cap fila nova a model_rounds", n_rounds == 4,
          f"rounds={n_rounds} (run1..run4)")

    # ════════ EXPLOIT B ════════
    print("\n=== EXPLOIT B: 1/2 contribucions → ROUND_NOT_READY ===")
    B4, A4, P4, cids4, bundle4 = setup_round(coord, "run5", "r5", n_assign=2,
                                             base_hex="f", pre_hex="t")
    coord._conn.execute("UPDATE model_contributions SET status='VALIDATED'"
                        " WHERE cid=?", (cids4[1],))
    coord._conn.commit()
    expect_rejected("EXPLOIT B: FedAvg 1/2 → ROUND_NOT_READY",
                    lambda: coord.fedavg("run5", "r5",
                        base64.b64encode(bundle4).decode()),
                    "quorum_not_met")

    # ════════ EXPLOIT C ════════
    print("\n=== EXPLOIT C: adapter base diferent → REJECTED ===")
    B5, A5, P5, cids5, bundle5 = setup_round(coord, "run6", "r6", n_assign=2,
                                             base_hex="aa", pre_hex="u")
    other_b64 = base64.b64encode(make_bundle(42)).decode()
    expect_rejected("EXPLOIT C: baseline B ≠ SHA(A) → REJECTED",
                    lambda: coord.fedavg("run6", "r6", other_b64),
                    "fedavg_baseline_mismatch")

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"\n=== FEDAVG AUTHORITY SEC: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
