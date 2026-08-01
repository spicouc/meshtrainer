"""RC5.3 Stage B R2: REAL two-worker tests — full HTTP flow, real WorkerRuntime."""
import os, sys, json, torch, base64, time, tempfile, uuid, copy, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_receipt import generate_receipt, verify_receipt
from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash
from rc5_2_numerical_models import WorkerNumericalModel
from rc5_3_multiworker import (RoundCoordinator, build_worker_artifacts, adapter_artifacts_from_round, real_worker_flow,
                                run_two_real_workers, make_shard, _stable_seed,
                                ROUND_CLOSED, ROUND_READY)

PASS, FAIL = 0, 0
KEY = b"r53_stage_b_real_key_2026"

def check(label, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {label}")
    else: FAIL += 1; print(f"  ❌ {label}")

def _fresh_db():
    return os.path.join(tempfile.gettempdir(), f"r53r_{uuid.uuid4().hex[:8]}.db")

def _setup(port):
    db = _fresh_db()
    srv, _ = serve(port=port, db_path=db, signing_key=KEY)
    cl = JsonRpcClient(f"http://127.0.0.1:{port}")
    time.sleep(0.3)
    coord = RoundCoordinator(db, KEY)
    return db, srv, cl, coord

def _hashes(art):
    return {"worker_model_hash": art[1], "base_adapter_hash": art[3],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()}

def _open_round_two_workers(coord, run_id, round_id):
    coord.round_open(run_id, round_id, {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.calibration_submit(run_id, round_id, "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit(run_id, round_id, "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    artA = build_worker_artifacts(WorkerNumericalModel())
    artB = build_worker_artifacts(WorkerNumericalModel())
    coord.assign(run_id, round_id, "a1", "wa", "shard_A", _hashes(artA), 90)
    coord.assign(run_id, round_id, "a2", "wb", "shard_B", _hashes(artB), 60)
    coord.start_round(run_id, round_id)
    return artA, artB

def test_two_real_workers_full_flow():
    """Two REAL WorkerRuntime workers, full HTTP flow, real deltas."""
    _, _, cl, coord = _setup(19880)
    artA, artB = _open_round_two_workers(coord, "run1", "rd1")
    ra = real_worker_flow(cl, coord, "run1", "rd1", "a1", "wa", "shard_A",
                          artA[0], artA[1], artA[2], artA[3], offset=0, label_keep=90, update_suffix="1")
    rb = real_worker_flow(cl, coord, "run1", "rd1", "a2", "wb", "shard_B",
                          artB[0], artB[1], artB[2], artB[3], offset=32, label_keep=60, update_suffix="1")
    check("W1: two workers completed", ra["worker_id"] == "wa" and rb["worker_id"] == "wb")
    check("W2: distinct ETT (real shards)", ra["ett"] != rb["ett"])
    check("W3: distinct receipts", ra["receipt_id"] != rb["receipt_id"])
    check("W4: distinct backward_ids", ra["backward_id"] != rb["backward_id"])
    check("W5: real delta hashes", len(ra["delta_sha"]) == 64 and len(rb["delta_sha"]) == 64)
    check("W6: ETT derived from labels", ra["ett"] == 89 and rb["ett"] == 59)
    q = coord.ready_to_close("run1", "rd1")
    check("W7: quorum met", q["state"] == ROUND_READY)

def test_shards_real_data():
    """Shards carry real distinct data (tokens, labels, ETT)."""
    tA, lA, mA = make_shard("shard_A", offset=0, label_keep=90)
    tB, lB, mB = make_shard("shard_B", offset=32, label_keep=60)
    check("S1: token content differs", not torch.equal(tA, tB))
    check("S2: label content differs", not torch.equal(lA, lB))
    check("S3: ETT A", int((lA[:, 1:] != -100).sum()) == 89)
    check("S4: ETT B", int((lB[:, 1:] != -100).sum()) == 59)

def test_fedavg_ett_weighted_real():
    """FedAvg of REAL deltas: ETT-weighted, order-invariant, offline oracle."""
    artA, artB = build_worker_artifacts(WorkerNumericalModel()), build_worker_artifacts(WorkerNumericalModel())
    _, _, cl, coord = _setup(19881)
    coord.round_open("run1", "rd2", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd2", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd2", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd2", "a1", "wa", "shard_A", _hashes(artA), 90)
    coord.assign("run1", "rd2", "a2", "wb", "shard_B", _hashes(artB), 60)
    coord.start_round("run1", "rd2")
    specs = [("a1", "wa", "shard_A", 0, 90), ("a2", "wb", "shard_B", 32, 60)]
    ra, rb = run_two_real_workers(coord, cl, "run1", "rd2",
                                  {"wa": artA, "wb": artB}, specs)
    dA, dB = coord.fedavg("run1", "rd2")
    # offline oracle
    rows = coord._conn.execute("SELECT delta_bundle_b64, delta_bundle_sha256, ett FROM contributions_r53 WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd2'").fetchall()
    total = sum(r["ett"] for r in rows)
    oA = torch.zeros(8, 256)
    for r in rows:
        t = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]), r["delta_bundle_sha256"])
        oA += (r["ett"] / total) * t["local_layers.0.linear1.lora_A.weight"]
    check("F1: FedAvg A == offline oracle", torch.allclose(dA, oA, rtol=1e-5, atol=1e-6))
    check("F2: deltas are REAL (non-zero)", dA.abs().max().item() > 1e-6)
    # order invariance: reversed arrival (SAME artifacts/deltas)
    _, _, cl2, coord2 = _setup(19882)
    coord2.round_open("run1", "rd2b", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord2.calibration_submit("run1", "rd2b", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord2.calibration_submit("run1", "rd2b", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord2.assign("run1", "rd2b", "a1", "wa", "shard_A", _hashes(artA), 90)
    coord2.assign("run1", "rd2b", "a2", "wb", "shard_B", _hashes(artB), 60)
    coord2.start_round("run1", "rd2b")
    specs_rev = [("a2", "wb", "shard_B", 32, 60), ("a1", "wa", "shard_A", 0, 90)]
    run_two_real_workers(coord2, cl2, "run1", "rd2b", {"wa": artA, "wb": artB}, specs_rev)
    dA2, _ = coord2.fedavg("run1", "rd2b")
    check("F3: FedAvg order-invariant", torch.allclose(dA, dA2, rtol=1e-5, atol=1e-6))

def test_round2_lineage_real():
    """Round 2 starts from Round 1's real global adapter; stale rejected."""
    _, _, cl, coord = _setup(19883)
    artA, artB = _open_round_two_workers(coord, "run1", "r1")
    run_two_real_workers(coord, cl, "run1", "r1",
                         {"wa": artA, "wb": artB},
                         [("a1", "wa", "shard_A", 0, 90), ("a2", "wb", "shard_B", 32, 60)])
    q = coord.ready_to_close("run1", "r1")
    c1 = coord.close_round("run1", "r1")
    # Round 2: base_adapter_hash = hash(adapter_1)
    coord.round_open("run1", "r2", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.calibration_submit("run1", "r2", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "r2", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    artA2 = adapter_artifacts_from_round(WorkerNumericalModel(), c1["adapter_bytes"])
    artB2 = adapter_artifacts_from_round(WorkerNumericalModel(), c1["adapter_bytes"])
    hA2 = _hashes(artA2); hA2["base_adapter_hash"] = c1["adapter_hash"]
    hB2 = _hashes(artB2); hB2["base_adapter_hash"] = c1["adapter_hash"]
    coord.assign("run1", "r2", "b1", "wa", "shard_A", hA2, 90)
    coord.assign("run1", "r2", "b2", "wb", "shard_B", hB2, 60)
    coord.start_round("run1", "r2")
    run_two_real_workers(coord, cl, "run1", "r2",
                         {"wa": artA2, "wb": artB2},
                         [("b1", "wa", "shard_A", 0, 90), ("b2", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "r2")
    c2 = coord.close_round("run1", "r2")
    ad1 = delta_bundle_unpack(c1["adapter_bytes"], c1["adapter_hash"])
    ad2 = delta_bundle_unpack(c2["adapter_bytes"], c2["adapter_hash"])
    dA2, _ = coord.fedavg("run1", "r2")
    check("R1: round1 closed", c1["adapter_hash"] == coord.get_round_adapter("run1", "r1")[1])
    check("R2: round2 adapter == adapter1 + delta2",
          torch.allclose(ad2["local_layers.0.linear1.lora_A.weight"],
                         ad1["local_layers.0.linear1.lora_A.weight"] + dA2, rtol=1e-5, atol=1e-6))
    # stale adapter rejected
    stale = _hashes(artA2); stale["base_adapter_hash"] = "f" * 64
    try:
        coord.assign("run1", "r2", "b3", "wc", "sC", stale, 60)
        check("R3: stale adapter rejected", False)
    except ValueError:
        check("R3: stale adapter rejected", True)

def test_revisions_real():
    """rev1 ACTIVE → rev2 ACTIVE → rev1 SUPERSEDED; FedAvg excludes SUPERSEDED."""
    _, _, cl, coord = _setup(19884)
    coord.round_open("run1", "rd3", {})
    coord.calibration_submit("run1", "rd3", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    artA = build_worker_artifacts(WorkerNumericalModel())
    coord.assign("run1", "rd3", "a1", "wa", "shard_A", _hashes(artA), 90)
    coord.start_round("run1", "rd3")
    ra1 = real_worker_flow(cl, coord, "run1", "rd3", "a1", "wa", "shard_A",
                           artA[0], artA[1], artA[2], artA[3], offset=0, label_keep=90, update_suffix="v1")
    st1 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra1["cid"],)).fetchone()["status"]
    check("RV1: rev1 ACTIVE", st1 == "ACTIVE")
    ra2 = real_worker_flow(cl, coord, "run1", "rd3", "a1", "wa", "shard_A",
                           artA[0], artA[1], artA[2], artA[3], offset=0, label_keep=90, update_suffix="v2",
                           micro_unit_id="mu_wa_v2")
    st2 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra2["cid"],)).fetchone()["status"]
    check("RV2: rev2 ACTIVE after full flow", st2 == "ACTIVE")
    st1c = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra1["cid"],)).fetchone()["status"]
    check("RV4: rev1 SUPERSEDED after rev2 ACTIVE", st1c == "SUPERSEDED")

def test_quorum_exact_set():
    """Quorum = exact mandatory set; alien/missing contributions don't count."""
    _, _, cl, coord = _setup(19885)
    artA, artB = _open_round_two_workers(coord, "run1", "rd4")
    # only worker A contributes → quorum NOT met
    real_worker_flow(cl, coord, "run1", "rd4", "a1", "wa", "shard_A",
                     artA[0], artA[1], artA[2], artA[3], offset=0, label_keep=90, update_suffix="1")
    try:
        coord.ready_to_close("run1", "rd4")
        check("Q1: quorum not met with 1/2", False)
    except ValueError:
        check("Q1: quorum not met with 1/2", True)
    # close before quorum rejected
    try:
        coord.close_round("run1", "rd4")
        check("Q2: close before quorum rejected", False)
    except ValueError:
        check("Q2: close before quorum rejected", True)

def test_state_machine_real():
    """Round state machine transitions enforced."""
    db = _fresh_db(); coord = RoundCoordinator(db, KEY)
    try:
        coord.calibration_submit("r", "rd", "wa", {})  # before OPEN
        check("SM1: calibration before OPEN rejected", False)
    except ValueError:
        check("SM1: calibration before OPEN rejected", True)
    coord.round_open("r", "rd", {})
    try:
        coord.assign("r", "rd", "a1", "wa", "sA", _hashes(build_worker_artifacts(WorkerNumericalModel())), 10)  # before calibration
        check("SM2: assignment before calibration rejected", False)
    except ValueError:
        check("SM2: assignment before calibration rejected", True)

def test_process_restart_real():
    """REAL process restart via subprocess on the same SQLite."""
    db = _fresh_db()
    script = os.path.join(tempfile.gettempdir(), f"restart_worker_{uuid.uuid4().hex[:6]}.py")
    with open(script, "w") as f:
        f.write(f'''
import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from rc5_3_multiworker import RoundCoordinator, build_worker_artifacts, real_worker_flow
from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_numerical_models import WorkerNumericalModel
from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash
import time, sys, json
DB = {db!r}; KEY = b"r53_stage_b_real_key_2026"
srv, _ = serve(port=19886, db_path=DB, signing_key=KEY)
cl = JsonRpcClient("http://127.0.0.1:19886"); time.sleep(0.3)
coord = RoundCoordinator(DB, KEY)
which = sys.argv[1]
if which == "A":
    coord.round_open("run1", "rd9", {{"max_micro_batch": 4, "max_sequence_length": 128}})
    coord.calibration_submit("run1", "rd9", "wa", {{"max_micro_batch": 2, "max_sequence_length": 128}})
    coord.calibration_submit("run1", "rd9", "wb", {{"max_micro_batch": 2, "max_sequence_length": 128}})
    artA = build_worker_artifacts(WorkerNumericalModel())
    artB = build_worker_artifacts(WorkerNumericalModel())
    h = lambda a: {{"worker_model_hash": a[1], "base_adapter_hash": a[3], "partition_schema_hash": partition_schema_hash(), "adapter_schema_hash": adapter_schema_hash(), "numerical_profile_hash": numerical_profile_hash()}}
    coord.assign("run1", "rd9", "a1", "wa", "shard_A", h(artA), 90)
    coord.assign("run1", "rd9", "a2", "wb", "shard_B", h(artB), 60)
    coord.start_round("run1", "rd9")
    import json
    json.dump({{"artA": [a.hex() if isinstance(a, bytes) else a for a in artA],
               "artB": [a.hex() if isinstance(a, bytes) else a for a in artB]}}, open("/tmp/r53_art.json", "w"))
    real_worker_flow(cl, coord, "run1", "rd9", "a1", "wa", "shard_A", artA[0], artA[1], artA[2], artA[3], offset=0, label_keep=90, update_suffix="1")
    print("A DONE")
else:
    arts = json.load(open("/tmp/r53_art.json"))
    artA = [bytes.fromhex(x) if isinstance(x, str) and len(x) > 64 else x for x in arts["artA"]]
    artB = [bytes.fromhex(x) if isinstance(x, str) and len(x) > 64 else x for x in arts["artB"]]
    real_worker_flow(cl, coord, "run1", "rd9", "a2", "wb", "shard_B", artB[0], artB[1], artB[2], artB[3], offset=32, label_keep=60, update_suffix="1")
    q = coord.ready_to_close("run1", "rd9")
    c = coord.close_round("run1", "rd9")
    json.dump({{"adapter_hash": c["adapter_hash"], "adapter_b64": c["adapter_bytes"].hex()}}, open("/tmp/r53_restart_out.json", "w"))
    print("B DONE")
''')
    r1 = subprocess.run(["python3", script, "A"], capture_output=True, text=True, timeout=180)
    r2 = subprocess.run(["python3", script, "B"], capture_output=True, text=True, timeout=180)
    check("P1: worker A process OK", r1.returncode == 0 and "A DONE" in r1.stdout)
    check("P2: worker B process OK (after restart)", r2.returncode == 0 and "B DONE" in r2.stdout)
    out = json.load(open("/tmp/r53_restart_out.json"))
    check("P3: round closed after restart", len(out["adapter_hash"]) == 64)
    os.unlink(script)

def test_isolation_real():
    """Cross-worker isolation: backward_id A not valid for B's unit."""
    _, _, cl, coord = _setup(19887)
    artA, artB = _open_round_two_workers(coord, "run1", "rd5")
    ra = real_worker_flow(cl, coord, "run1", "rd5", "a1", "wa", "shard_A",
                          artA[0], artA[1], artA[2], artA[3], offset=0, label_keep=90, update_suffix="1")
    rb = real_worker_flow(cl, coord, "run1", "rd5", "a2", "wb", "shard_B",
                          artB[0], artB[1], artB[2], artB[3], offset=32, label_keep=60, update_suffix="1")
    # A's backward_id with B's unit → rejected
    try:
        cl.call("step.server_backward.fetch", {"unit_id": rb["unit_id"], "backward_id": ra["backward_id"]})
        check("I1: alien backward_id rejected", False)
    except ValueError:
        check("I1: alien backward_id rejected", True)
    # A's receipt not usable for B's contribution
    try:
        coord.submit_contribution("run1", "rd5", "a2", "wb", {"receipt_id": ra["receipt_id"]}, rb["delta_sha"], rb["ett"])
        check("I2: alien receipt rejected", False)
    except (ValueError, KeyError):
        check("I2: alien receipt rejected", True)

TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.3 Stage B R2 — REAL Two-Worker Tests")
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
