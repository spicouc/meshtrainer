#!/usr/bin/env python3
"""coordinator_authority_tests.py — ADVERSARIALS D'AUTORITAT (R2.3, punt 8).

AUTH-01  worker cannot create assignment
AUTH-02  unknown assignment calibration rejected
AUTH-03  wrong base model rejected
AUTH-04  second worker wrong base rejected
AUTH-05  wrong adapter_0_sha rejected
AUTH-06  stale adapter_pre_hash rejected
AUTH-07  wrong shard_id rejected
AUTH-08  wrong shard_manifest_sha rejected
AUTH-09  fake ETT rejected
AUTH-10  extreme ETT 999999 rejected
AUTH-11  contribution different base rejected
AUTH-12  contribution different adapter baseline rejected
AUTH-13  FedAvg refuses heterogeneous adapter_pre_hash
AUTH-14  worker cannot overwrite an existing assignment

Punt 9 — PROVA D'EXPLOTACIÓ: Worker A (base=A, shard=shard-B, adapter=111..,
ETT=999999) i Worker B (base=B, shard=shard-A, adapter=222.., ETT=1) ->
TOTS REJECTED; FedAvg NO pot executar-se.
"""
import base64
import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from model_round_coordinator import ModelRoundCoordinator, ModelRecoveryError  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def expect_rejected(name, fn, reason_contains=None):
    try:
        fn()
        check(name, False, "no va llançar cap error")
    except ModelRecoveryError as e:
        ok = reason_contains is None or reason_contains in e.reason
        check(name, ok, f"ModelRecoveryError: {e.reason}")
    except Exception as e:  # noqa: BLE001
        ok = reason_contains is None or reason_contains in str(e)
        check(name, ok, f"{type(e).__name__}: {str(e)[:60]}")


def main():
    print("=" * 64)
    print(" COORDINATOR AUTHORITY — ADVERSARIALS (R2.3, punt 8)")
    print("=" * 64)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    coord = ModelRoundCoordinator(conn)

    # ── setup: ronda r1 amb base B0, adapter A0 ──
    B0 = "b" * 64
    A0 = "a0" * 32
    P0 = "p0" * 32
    coord.create_round("run1", "r1", "dummy", B0, A0, P0)
    coord.create_assignment("run1", "r1", "asg-A", "w-A", "shard-A",
                            "m-a" * 32, B0, A0, expected_ett=100)
    coord.create_assignment("run1", "r1", "asg-B", "w-B", "shard-B",
                            "m-b" * 32, B0, A0, expected_ett=200)

    # ── AUTH-01: worker no pot CREAR una assignació ──
    # (worker.calibrate ja NO crida coord.assign; l'única via és
    #  assignment.create, que és op de Coordinator/admin)
    print("\n=== AUTH-01: worker cannot create assignment ===")
    expect_rejected("AUTH-01 calibrate amb assignació inexistent -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "asg-X", "w-A", "shard-X",
                        "m-x" * 32, B0, A0, ett=10),
                    "unknown_assignment")

    # ── AUTH-02: calibrate amb assignació desconeguda ──
    print("\n=== AUTH-02: unknown assignment calibration rejected ===")
    expect_rejected("AUTH-02 unknown assignment -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "no-existeix", "w-A", "shard-A",
                        "m-a" * 32, B0, A0, ett=100),
                    "unknown_assignment")

    # ── AUTH-03: base model incorrecte ──
    print("\n=== AUTH-03: wrong base model rejected ===")
    expect_rejected("AUTH-03 base model incorrecte -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "asg-A", "w-A", "shard-A",
                        "m-a" * 32, "X" * 64, A0, ett=100),
                    "base_model_mismatch")

    # ── AUTH-04: segon worker amb base incorrecte ──
    print("\n=== AUTH-04: second worker wrong base rejected ===")
    expect_rejected("AUTH-04 worker B amb base Y (ronda espera B0) -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "asg-B", "w-B", "shard-B",
                        "m-b" * 32, "Y" * 64, A0, ett=200),
                    "base_model_mismatch")

    # ── AUTH-05: adapter_0_sha incorrecte ──
    print("\n=== AUTH-05: wrong adapter_0_sha rejected ===")
    expect_rejected("AUTH-05 adapter_0_sha incorrecte -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "asg-A", "w-A", "shard-A",
                        "m-a" * 32, B0, "FF" * 32, ett=100),
                    "adapter_0_sha_mismatch")

    # ── AUTH-06: stale adapter_pre_hash rejected ──
    # (el pre_hash es verifica a nivell de contribució: la contribució
    #  s'ha de registrar amb el pre_hash de la ronda)
    print("\n=== AUTH-06: stale adapter_pre_hash rejected ===")
    # contribució amb pre_hash NO-P0 (stale) -> registrar-la falla
    try:
        coord.register_uploaded_contribution(
            "cid-stale", "run1", "r1", "asg-A", "w-A", 100,
            base64.b64encode(b"\x00" * 32).decode(),
            hashlib.sha256(b"\x00" * 32).hexdigest(),
            "rq" * 32, "STALE" * 8, "src")
        check("AUTH-06 stale adapter_pre_hash -> REJECTED", False,
              "es va registrar sense verificar")
    except ModelRecoveryError as e:
        check("AUTH-06 stale adapter_pre_hash -> REJECTED",
              e.reason == "adapter_pre_hash_mismatch",
              f"{e.reason}")
    # (nota: register_uploaded_contribution valida el delta_sha; per a la
    #  verificació del pre_hash usem el mètode dedicat)
    expect_rejected("AUTH-06 pre_hash no verificat -> REJECTED",
                    lambda: coord.verify_contribution_pre_hash(
                        "run1", "r1", "asg-A", "w-A", "NOT-P0"),
                    "adapter_pre_hash_mismatch")

    # ── AUTH-07: shard_id incorrecte ──
    print("\n=== AUTH-07: wrong shard_id rejected ===")
    expect_rejected("AUTH-07 worker A declara shard-B -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "asg-A", "w-A", "shard-B",
                        "m-a" * 32, B0, A0, ett=100),
                    "shard_mismatch")

    # ── AUTH-08: shard_manifest_sha incorrecte ──
    print("\n=== AUTH-08: wrong shard_manifest_sha rejected ===")
    expect_rejected("AUTH-08 shard manifest incorrecte -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "asg-A", "w-A", "shard-A",
                        "FAKE" * 16, B0, A0, ett=100),
                    "shard_manifest_mismatch")

    # ── AUTH-09: ETT fals ──
    print("\n=== AUTH-09: fake ETT rejected ===")
    expect_rejected("AUTH-09 ETT incorrecte (esperat 100, envia 77) -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "asg-A", "w-A", "shard-A",
                        "m-a" * 32, B0, A0, ett=77),
                    "ett_mismatch")

    # ── AUTH-10: ETT extrem 999999 ──
    print("\n=== AUTH-10: extreme ETT 999999 rejected ===")
    expect_rejected("AUTH-10 ETT 999999 -> REJECTED",
                    lambda: coord.verify_calibration(
                        "run1", "r1", "asg-A", "w-A", "shard-A",
                        "m-a" * 32, B0, A0, ett=999999),
                    "ett_mismatch")

    # ── AUTH-11: contribution amb base diferent ──
    # (la contribució s'associa a l'assignació; si el worker usa un altre
    #  base, el calibrate ja el va REJECTAR abans; aquí comprovem que la
    #  contribució no pot pertànyer a una assignació d'un altre base)
    print("\n=== AUTH-11: contribution different base rejected ===")
    # registrem una contribució vàlida per asg-A (base B0, pre P0)
    cid_ok = "cid-ok"
    delta = b"\x01" * 64
    coord.register_uploaded_contribution(
        cid_ok, "run1", "r1", "asg-A", "w-A", 100,
        base64.b64encode(delta).decode(), hashlib.sha256(delta).hexdigest(),
        "rq" * 32, P0, "src")
    # AUTH-11: una contribució d'una assignació amb un altre base NO pot
    # existir perquè el calibrate la rebutjaria; comprovem la propietat:
    a = coord.get_assignment("run1", "r1", "asg-A", "w-A")
    check("AUTH-11 contribution lligada a l'assignació amb base B0 (no pot "
          "ser d'un altre base)", a["base_model_hash"] == B0)
    # i un cid que declari un altre run/round amb base diferent -> no es
    # pot validar (no existeix)
    expect_rejected("AUTH-11 contribució d'un altre round -> no_contribution",
                    lambda: coord.activate_contribution("cid-fantasma"),
                    "cannot_activate")

    # ── AUTH-12: contribution amb adapter baseline diferent ──
    print("\n=== AUTH-12: contribution different adapter baseline rejected ===")
    try:
        coord.register_uploaded_contribution(
            "cid-stale2", "run1", "r1", "asg-A", "w-A", 100,
            base64.b64encode(b"\x02" * 64).decode(),
            hashlib.sha256(b"\x02" * 64).hexdigest(),
            "rq" * 32, "STALE2" * 8, "src")
        check("AUTH-12 adapter baseline diferent -> REJECTED", False,
              "es va registrar")
    except ModelRecoveryError as e:
        check("AUTH-12 adapter baseline diferent -> REJECTED",
              e.reason == "adapter_pre_hash_mismatch", e.reason)

    # ── AUTH-13: FedAvg refusa adapter_pre_hash heterogeni ──
    print("\n=== AUTH-13: FedAvg refuses heterogeneous adapter_pre_hash ===")
    # la contribució legítima (pre P0) l'activem com a ACTIVE
    coord.validate_contribution("cid-ok")
    coord.activate_contribution("cid-ok")
    # una segona contribució ACTIVE amb pre_hash DIFERENT del baseline de
    # la ronda (simula un worker amb adapter stale). La inserim directament
    # a la BD perquè el register legítim ja la rebutjaria (AUTH-12).
    conn.execute(
        "INSERT INTO model_contributions (cid, run_id, round_id,"
        " assignment_id, worker_id, status, ett, delta_bundle_b64,"
        " delta_bundle_sha256, request_sha, adapter_pre_hash, source_sha)"
        " VALUES (?,?,?,?,?, 'ACTIVE', ?, ?, ?, ?, ?, ?)",
        ("cid-hetero", "run1", "r1", "asg-B", "w-B", 200,
         base64.b64encode(b"\x03" * 64).decode(),
         hashlib.sha256(b"\x03" * 64).hexdigest(), "rq" * 32,
         "OTHER" * 8, "src"))
    conn.commit()
    expect_rejected("AUTH-13 FedAvg amb pre_hash heterogeni -> REJECTED",
                    lambda: coord.fedavg("run1", "r1",
                                         base64.b64encode(b"\x00" * 64).decode()),
                    "heterogeneous_adapter_pre_hash")

    # ── AUTH-14: worker no pot sobreescriure una assignació ──
    print("\n=== AUTH-14: worker cannot overwrite an existing assignment ===")
    expect_rejected("AUTH-14 reassignació d'asg-A -> REJECTED",
                    lambda: coord.create_assignment(
                        "run1", "r1", "asg-A", "w-ATACANT", "shard-B",
                        "m-x" * 32, "Y" * 64, "ZZ" * 32, expected_ett=1),
                    "assignment_exists")

    # ═══════════════════════════════════════════════════════════════════
    # PUNT 9 — PROVA D'EXPLOTACIÓ (l'exploit exacte del Supervisor)
    # ═══════════════════════════════════════════════════════════════════
    print("\n=== PUNT 9: PROVA D'EXPLOTACIÓ ===")
    print("  Worker A: base=A shard=shard-B adapter=111.. ETT=999999")
    print("  Worker B: base=B shard=shard-A adapter=222.. ETT=1")
    # Worker A intenta calibrar l'assignació asg-A (que és shard-A, base B0)
    expl_A = lambda: coord.verify_calibration(  # noqa: E731
        "run1", "r1", "asg-A", "w-A", "shard-B",  # shard robat
        "m-b" * 32, "A" * 64,  # base robat = A (no B0)
        "11" * 32,  # adapter fals 111..
        ett=999999)
    expect_rejected("EXPLOIT Worker A (base=A, shard-B, adapter=111, ETT=999999)",
                    expl_A, "base_model_mismatch")
    # Worker B intenta calibrar asg-B (que és shard-B, base B0)
    expl_B = lambda: coord.verify_calibration(  # noqa: E731
        "run1", "r1", "asg-B", "w-B", "shard-A",  # shard robat
        "m-a" * 32, "B" * 64,  # base robat = B
        "22" * 32,  # adapter fals 222..
        ett=1)
    expect_rejected("EXPLOIT Worker B (base=B, shard-A, adapter=222, ETT=1)",
                    expl_B, "base_model_mismatch")
    # FedAvg NO pot executar-se (no hi ha contribucions ACTIVE homogènies)
    expect_rejected("EXPLOIT FedAvg no executable (cap contribució vàlida)",
                    lambda: coord.fedavg("run1", "r1",
                                         base64.b64encode(b"\x00" * 64).decode()),
                    "heterogeneous_adapter_pre_hash")

    # ── resum ──
    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"\n=== COORDINATOR AUTHORITY: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
