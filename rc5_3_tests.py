"""RC5.3 Stage B: multiworker tests — two workers, FedAvg, revisions, Round 2, restart."""
import os, sys, json, torch, base64, time, tempfile, uuid, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256
from rc5_2_receipt import generate_receipt, verify_receipt
from rc5_3_multiworker import (RoundCoordinator, run_two_workers_concurrent,
                                 ROUND_CLOSED, ROUND_OPEN)

PASS, FAIL = 0, 0
KEY = b"r53_stage_b_key_1234567890"

def check(label, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {label}")
    else: FAIL += 1; print(f"  ❌ {label}")

def _fresh_db():
    return os.path.join(tempfile.gettempdir(), f"r53_{uuid.uuid4().hex[:8]}.db")

HASHES = {"worker_model_hash": "a" * 64, "base_adapter_hash": "b" * 64,
          "partition_schema_hash": "c" * 64, "adapter_schema_hash": "d" * 64,
          "numerical_profile_hash": "e" * 64}

def test_two_workers_concurrent():
    """Two real workers, distinct assignments/shard/ETT, concurrent execution."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd1", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd1", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd1", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    specs = [("asgA", "wa", "shard_A", 100), ("asgB", "wb", "shard_B", 50)]
    results = run_two_workers_concurrent(coord, "run1", "rd1", specs, HASHES)
    check("T1: two workers completed", len(results) == 2)
    check("T2: distinct workers", {r["worker_id"] for r in results} == {"wa", "wb"})
    check("T3: distinct ETT", {r["ett"] for r in results} == {100, 50})
    # isolation: each assignment has own worker
    asgs = coord.list_assignments("run1", "rd1")
    check("T4: two assignments", len(asgs) == 2)
    check("T5: shards distinct", {a["shard_id"] for a in asgs} == {"shard_A", "shard_B"})

def test_fedavg_ett_order_invariant():
    """FedAvg weighted by ETT, order-invariant, excludes non-ACTIVE."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd2", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd2", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd2", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    specs = [("a1", "wa", "sA", 100), ("a2", "wb", "sB", 50)]
    run_two_workers_concurrent(coord, "run1", "rd2", specs, HASHES)
    dA, dB = coord.fedavg("run1", "rd2")
    # offline oracle
    oracle_A = torch.zeros(8, 256); oracle_B = torch.zeros(1024, 8)
    rows = coord._conn.execute("SELECT delta_bundle_b64, delta_bundle_sha256, ett FROM contributions_r53 WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd2'").fetchall()
    total = sum(r["ett"] for r in rows)
    for r in rows:
        t = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]), r["delta_bundle_sha256"])
        oracle_A += (r["ett"] / total) * t["local_layers.0.linear1.lora_A.weight"]
        oracle_B += (r["ett"] / total) * t["local_layers.0.linear1.lora_B.weight"]
    check("F1: FedAvg A == offline oracle", torch.allclose(dA, oracle_A, rtol=1e-5, atol=1e-6))
    check("F2: FedAvg B == offline oracle", torch.allclose(dB, oracle_B, rtol=1e-5, atol=1e-6))
    # order invariance: reverse arrival order
    coord2 = RoundCoordinator(_fresh_db(), KEY)
    coord2.round_open("run1", "rd2b", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord2.calibration_submit("run1", "rd2b", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord2.calibration_submit("run1", "rd2b", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    specs_rev = [("a2", "wb", "sB", 50), ("a1", "wa", "sA", 100)]
    run_two_workers_concurrent(coord2, "run1", "rd2b", specs_rev, HASHES)
    dA2, _ = coord2.fedavg("run1", "rd2b")
    check("F3: FedAvg order-invariant", torch.allclose(dA, dA2, rtol=1e-5, atol=1e-6))

def test_revisions():
    """Rev1 ACTIVE → Rev2 RECEIVED → VALIDATED → ACTIVE → Rev1 SUPERSEDED."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd3", {})
    coord.calibration_submit("run1", "rd3", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd3", "a1", "wa", "sA", HASHES, 100)
    d = delta_bundle_pack(torch.randn(8, 256) * 0.001, torch.randn(1024, 8) * 0.001)
    dsha = bundle_sha256(d); db64 = base64.b64encode(d).decode()
    r1 = generate_receipt(run_id="run1", round_id="rd3", unit_id="u1", worker_id="wa", assignment_id="a1",
                          signing_key=KEY, delta_bundle_sha256=dsha, delta_bundle_byte_length=len(d), ett=100, loss=1.0)
    c1 = coord.submit_contribution("run1", "rd3", "a1", "wa", r1, db64, 100, revision=1)
    coord.validate_contribution(c1["contribution_id"]); coord.activate_contribution(c1["contribution_id"])
    st1 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (c1["contribution_id"],)).fetchone()["status"]
    check("R1: rev1 ACTIVE", st1 == "ACTIVE")
    # rev2 arrives
    r2 = generate_receipt(run_id="run1", round_id="rd3", unit_id="u1", worker_id="wa", assignment_id="a1",
                          signing_key=KEY, delta_bundle_sha256=dsha, delta_bundle_byte_length=len(d), ett=100, loss=1.0)
    c2 = coord.submit_contribution("run1", "rd3", "a1", "wa", r2, db64, 100, revision=2)
    st1b = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (c1["contribution_id"],)).fetchone()["status"]
    check("R2: rev1 still ACTIVE while rev2 RECEIVED", st1b == "ACTIVE")
    coord.validate_contribution(c2["contribution_id"]); coord.activate_contribution(c2["contribution_id"])
    st1c = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (c1["contribution_id"],)).fetchone()["status"]
    st2 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (c2["contribution_id"],)).fetchone()["status"]
    check("R3: rev1 SUPERSEDED after rev2 ACTIVE", st1c == "SUPERSEDED")
    check("R4: rev2 ACTIVE", st2 == "ACTIVE")

def test_round2_adapter_lineage():
    """Round 2 starts from Round 1 global adapter; stale Round 0 adapter rejected."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "r1", {})
    coord.calibration_submit("run1", "r1", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "r1", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    specs = [("a1", "wa", "sA", 100), ("a2", "wb", "sB", 50)]
    run_two_workers_concurrent(coord, "run1", "r1", specs, HASHES)
    closed = coord.close_round("run1", "r1")
    check("G1: round 1 closed", closed["adapter_hash"] == coord.get_round_adapter("run1", "r1")[1])
    # round 2 must use round 1 adapter
    coord.round_open("run1", "r2", {})
    coord.calibration_submit("run1", "r2", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "r2", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    h2 = dict(HASHES); h2["base_adapter_hash"] = closed["adapter_hash"]
    specs2 = [("b1", "wa", "sA", 120), ("b2", "wb", "sB", 60)]
    run_two_workers_concurrent(coord, "run1", "r2", specs2, h2)
    closed2 = coord.close_round("run1", "r2")
    check("G2: round 2 closed with new adapter", closed2["adapter_hash"] != closed["adapter_hash"])
    # Round 2 adapter must equal Round 1 adapter + averaged delta 2 (lineage oracle)
    ad1 = delta_bundle_unpack(closed["adapter_bytes"], closed["adapter_hash"])
    ad2 = delta_bundle_unpack(closed2["adapter_bytes"], closed2["adapter_hash"])
    dA2, _ = coord.fedavg("run1", "r2")
    check("G2b: round2 adapter == round1 adapter + delta2",
          torch.allclose(ad2["local_layers.0.linear1.lora_A.weight"],
                         ad1["local_layers.0.linear1.lora_A.weight"] + dA2, rtol=1e-5, atol=1e-6))
    # Round 2 must use round 1 adapter; stale adapter (round 0 hash) rejected
    stale = dict(HASHES); stale["base_adapter_hash"] = "f" * 64
    try:
        coord.assign("run1", "r2", "b3", "wc", "sC", stale, 60)
        check("G3: stale adapter rejected", False)
    except ValueError:
        check("G3: stale adapter rejected", True)
    # duplicate assignment for same worker rejected
    try:
        coord.assign("run1", "r2", "b4", "wa", "sA", h2, 10)
        check("G4: duplicate worker assignment rejected", False)
    except ValueError:
        check("G4: duplicate worker assignment rejected", True)

def test_budget_enforced():
    """Assignment above calibration budget must be rejected."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd11", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd11", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    over = dict(HASHES); over["max_micro_batch"] = 8  # exceeds calibration 2 and profile 4
    try:
        coord.assign("run1", "rd11", "a1", "wa", "sA", over, 100)
        check("BE1: over-budget assignment rejected", False)
    except ValueError:
        check("BE1: over-budget assignment rejected", True)

def test_restart_between_workers():
    """Worker A commits; restart coordinator; Worker B completes; same result."""
    db = _fresh_db()
    coord1 = RoundCoordinator(db, KEY)
    coord1.round_open("run1", "rd4", {})
    coord1.calibration_submit("run1", "rd4", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord1.calibration_submit("run1", "rd4", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    specs = [("a1", "wa", "sA", 100)]
    run_two_workers_concurrent(coord1, "run1", "rd4", specs, HASHES)
    # restart: new coordinator on same db
    coord2 = RoundCoordinator(db, KEY)
    specs_b = [("a2", "wb", "sB", 50)]
    run_two_workers_concurrent(coord2, "run1", "rd4", specs_b, HASHES)
    closed = coord2.close_round("run1", "rd4")
    check("S1: restart then close round", closed["adapter_hash"] == coord2.get_round_adapter("run1", "rd4")[1])
    # compare with no-restart run
    coord3 = RoundCoordinator(_fresh_db(), KEY)
    coord3.round_open("run1", "rd4", {})
    coord3.calibration_submit("run1", "rd4", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord3.calibration_submit("run1", "rd4", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    run_two_workers_concurrent(coord3, "run1", "rd4", specs + specs_b, HASHES)
    closed3 = coord3.close_round("run1", "rd4")
    check("S2: same adapter with/without restart", closed["adapter_hash"] == closed3["adapter_hash"])

def test_duplicate_contribution_rejected():
    """No duplicate contributions for same receipt; double aggregation impossible."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd5", {})
    coord.calibration_submit("run1", "rd5", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd5", "a1", "wa", "sA", HASHES, 100)
    d = delta_bundle_pack(torch.randn(8, 256) * 0.001, torch.randn(1024, 8) * 0.001)
    dsha = bundle_sha256(d); db64 = base64.b64encode(d).decode()
    r = generate_receipt(run_id="run1", round_id="rd5", unit_id="u1", worker_id="wa", assignment_id="a1",
                         signing_key=KEY, delta_bundle_sha256=dsha, delta_bundle_byte_length=len(d), ett=100, loss=1.0)
    c1 = coord.submit_contribution("run1", "rd5", "a1", "wa", r, db64, 100)
    c2 = coord.submit_contribution("run1", "rd5", "a1", "wa", r, db64, 100)
    check("D1: duplicate returns same cid", c1["contribution_id"] == c2["contribution_id"])
    n = coord._conn.execute("SELECT COUNT(*) c FROM contributions_r53 WHERE cid=?", (c1["contribution_id"],)).fetchone()["c"]
    check("D2: single row", n == 1)
    # close twice → same adapter (idempotent), not double-aggregated
    coord.validate_contribution(c1["contribution_id"]); coord.activate_contribution(c1["contribution_id"])
    cl1 = coord.close_round("run1", "rd5")
    cl2 = coord.close_round("run1", "rd5")
    check("D3: close idempotent same hash", cl1["adapter_hash"] == cl2["adapter_hash"])

def test_fedavg_excludes_superseded():
    """FedAvg must never include SUPERSEDED contributions."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd6", {})
    coord.calibration_submit("run1", "rd6", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd6", "a1", "wa", "sA", HASHES, 100)
    d1 = delta_bundle_pack(torch.randn(8, 256) * 0.001, torch.randn(1024, 8) * 0.001)
    dsha1 = bundle_sha256(d1); db64_1 = base64.b64encode(d1).decode()
    r1 = generate_receipt(run_id="run1", round_id="rd6", unit_id="u1", worker_id="wa", assignment_id="a1",
                          signing_key=KEY, delta_bundle_sha256=dsha1, delta_bundle_byte_length=len(d1), ett=100, loss=1.0)
    c1 = coord.submit_contribution("run1", "rd6", "a1", "wa", r1, db64_1, 100, revision=1)
    coord.validate_contribution(c1["contribution_id"]); coord.activate_contribution(c1["contribution_id"])
    d2 = delta_bundle_pack(torch.randn(8, 256) * 0.002, torch.randn(1024, 8) * 0.002)
    dsha2 = bundle_sha256(d2); db64_2 = base64.b64encode(d2).decode()
    r2 = generate_receipt(run_id="run1", round_id="rd6", unit_id="u1", worker_id="wa", assignment_id="a1",
                          signing_key=KEY, delta_bundle_sha256=dsha2, delta_bundle_byte_length=len(d2), ett=100, loss=1.0)
    c2 = coord.submit_contribution("run1", "rd6", "a1", "wa", r2, db64_2, 100, revision=2)
    coord.validate_contribution(c2["contribution_id"]); coord.activate_contribution(c2["contribution_id"])
    # rev1 now SUPERSEDED, only rev2 ACTIVE
    rows = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid IN (?,?)", (c1["contribution_id"], c2["contribution_id"])).fetchall()
    st = {r["status"] for r in rows}
    check("FS1: rev1 SUPERSEDED rev2 ACTIVE", st == {"SUPERSEDED", "ACTIVE"})
    dA, _ = coord.fedavg("run1", "rd6")
    # oracle: average of ONLY the ACTIVE (rev2) delta
    act = coord._conn.execute("SELECT delta_bundle_b64, delta_bundle_sha256 FROM contributions_r53 WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd6'").fetchall()
    check("FS2: fedavg excludes SUPERSEDED", len(act) == 1)
    exp = delta_bundle_unpack(base64.b64decode(act[0]["delta_bundle_b64"]), act[0]["delta_bundle_sha256"])
    check("FS3: fedavg == ACTIVE-only delta", torch.allclose(dA, exp["local_layers.0.linear1.lora_A.weight"], rtol=1e-5, atol=1e-6))

def test_adapter_oracle():
    """close_round adapter == pre + averaged delta (catches double-apply / wrong pre)."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd7", {})
    coord.calibration_submit("run1", "rd7", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd7", "a1", "wa", "sA", HASHES, 100)
    d = delta_bundle_pack(torch.randn(8, 256) * 0.001, torch.randn(1024, 8) * 0.001)
    dsha = bundle_sha256(d); db64 = base64.b64encode(d).decode()
    r = generate_receipt(run_id="run1", round_id="rd7", unit_id="u1", worker_id="wa", assignment_id="a1",
                         signing_key=KEY, delta_bundle_sha256=dsha, delta_bundle_byte_length=len(d), ett=100, loss=1.0)
    c = coord.submit_contribution("run1", "rd7", "a1", "wa", r, db64, 100)
    coord.validate_contribution(c["contribution_id"]); coord.activate_contribution(c["contribution_id"])
    closed = coord.close_round("run1", "rd7")
    ad = delta_bundle_unpack(closed["adapter_bytes"], closed["adapter_hash"])
    got_A = ad["local_layers.0.linear1.lora_A.weight"]
    dA, _ = coord.fedavg("run1", "rd7")
    check("AO1: adapter == pre(0) + delta", torch.allclose(got_A, dA, rtol=1e-5, atol=1e-6))

def test_close_early_rejected():
    """close_round before all mandatory assignments ACTIVE must raise."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd8", {})
    coord.calibration_submit("run1", "rd8", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd8", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd8", "a1", "wa", "sA", HASHES, 100)
    coord.assign("run1", "rd8", "a2", "wb", "sB", HASHES, 50)
    # only worker A contributes
    d = delta_bundle_pack(torch.randn(8, 256) * 0.001, torch.randn(1024, 8) * 0.001)
    dsha = bundle_sha256(d); db64 = base64.b64encode(d).decode()
    r = generate_receipt(run_id="run1", round_id="rd8", unit_id="u1", worker_id="wa", assignment_id="a1",
                         signing_key=KEY, delta_bundle_sha256=dsha, delta_bundle_byte_length=len(d), ett=100, loss=1.0)
    c = coord.submit_contribution("run1", "rd8", "a1", "wa", r, db64, 100)
    coord.validate_contribution(c["contribution_id"]); coord.activate_contribution(c["contribution_id"])
    try:
        coord.close_round("run1", "rd8")
        check("CE1: close-early rejected", False)
    except ValueError:
        check("CE1: close-early rejected", True)

def test_hash_verification():
    """verify_assignment_hashes rejects wrong worker_model_hash."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd9", {})
    coord.calibration_submit("run1", "rd9", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd9", "a1", "wa", "sA", HASHES, 100)
    p = dict(HASHES); p["worker_model_hash"] = "0" * 64
    try:
        coord.verify_assignment_hashes("run1", "rd9", "a1", "wa", p)
        check("HV1: wrong worker_model_hash rejected", False)
    except ValueError:
        check("HV1: wrong worker_model_hash rejected", True)

def test_cross_worker_isolation():
    """Activating worker B's contribution must NOT supersede worker A's ACTIVE."""
    coord = RoundCoordinator(_fresh_db(), KEY)
    coord.round_open("run1", "rd10", {})
    coord.calibration_submit("run1", "rd10", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd10", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd10", "a1", "wa", "sA", HASHES, 100)
    coord.assign("run1", "rd10", "a2", "wb", "sB", HASHES, 50)
    d = delta_bundle_pack(torch.randn(8, 256) * 0.001, torch.randn(1024, 8) * 0.001)
    dsha = bundle_sha256(d); db64 = base64.b64encode(d).decode()
    rA = generate_receipt(run_id="run1", round_id="rd10", unit_id="uA", worker_id="wa", assignment_id="a1",
                          signing_key=KEY, delta_bundle_sha256=dsha, delta_bundle_byte_length=len(d), ett=100, loss=1.0)
    cA = coord.submit_contribution("run1", "rd10", "a1", "wa", rA, db64, 100)
    coord.validate_contribution(cA["contribution_id"]); coord.activate_contribution(cA["contribution_id"])
    rB = generate_receipt(run_id="run1", round_id="rd10", unit_id="uB", worker_id="wb", assignment_id="a2",
                          signing_key=KEY, delta_bundle_sha256=dsha, delta_bundle_byte_length=len(d), ett=50, loss=1.0)
    cB = coord.submit_contribution("run1", "rd10", "a2", "wb", rB, db64, 50)
    coord.validate_contribution(cB["contribution_id"]); coord.activate_contribution(cB["contribution_id"])
    stA = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (cA["contribution_id"],)).fetchone()["status"]
    stB = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (cB["contribution_id"],)).fetchone()["status"]
    check("CI1: worker A stays ACTIVE after B activates", stA == "ACTIVE")
    check("CI2: worker B ACTIVE", stB == "ACTIVE")

TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.3 Stage B — Multiworker Tests")
    print("=" * 60)
    for name, fn in TESTS:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:
            import traceback
            FAIL += 1
            print(f"  ❌ {name}: {e}")
            traceback.print_exc()
    print(f"\n{'='*60}")
    print(f"  Resultat: {PASS}/{PASS+FAIL} PASS, {FAIL} FAIL")
    print(f"{'='*60}")
    sys.exit(0 if FAIL == 0 else 1)
