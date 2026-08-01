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

def _round_hashes(art, server_hash):
    return {"server_model_hash": server_hash, "worker_model_hash": art[1], "base_adapter_hash": art[3],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()}

def _open_round_two_workers(coord, run_id, round_id, adapter_0_bytes=None):
    """Round with ONE SHARED model: one worker_base, ONE real base_adapter (== adapter_0),
    byte-for-byte for both workers."""
    coord.round_open(run_id, round_id, {"max_micro_batch": 4, "max_sequence_length": 128})
    art = build_worker_artifacts(WorkerNumericalModel())
    if adapter_0_bytes is None:
        adapter_0_bytes = art[2]  # the SAME real adapter the workers load IS adapter_0
    a0_hash = bundle_sha256(adapter_0_bytes)
    art = (art[0], art[1], adapter_0_bytes, a0_hash)  # worker adapter == adapter_0
    coord.register_round_model(run_id, round_id, _round_hashes(art, "s" * 64), adapter_0_bytes)
    coord.calibration_submit(run_id, round_id, "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit(run_id, round_id, "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign(run_id, round_id, "a1", "wa", "shard_A", _round_hashes(art, "s" * 64), 90)
    coord.assign(run_id, round_id, "a2", "wb", "shard_B", _round_hashes(art, "s" * 64), 60)
    coord.start_round(run_id, round_id)
    return art, art  # both workers load the SAME artifact bytes (adapter == adapter_0)

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
    # R4: single real base adapter — 4 hashes equal
    rnd = coord._conn.execute("SELECT * FROM rounds_r53 WHERE run_id='run1' AND round_id='rd1'").fetchone()
    check("W8: worker A adapter hash == worker B", ra["baeh"] == rb["baeh"])
    check("W9: worker adapter == round.base_adapter_hash", ra["baeh"] == rnd["base_adapter_hash"])
    check("W10: worker adapter == round.adapter_0_hash", ra["baeh"] == rnd["adapter_0_hash"])
    check("W11: round.base_adapter_hash == adapter_0_hash", rnd["base_adapter_hash"] == rnd["adapter_0_hash"])
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
    art = build_worker_artifacts(WorkerNumericalModel())  # SHARED model for both workers
    _, _, cl, coord = _setup(19881)
    coord.round_open("run1", "rd2", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.register_round_model("run1", "rd2", _round_hashes(art, "s" * 64), adapter_0_bytes=art[2])
    coord.calibration_submit("run1", "rd2", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "rd2", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd2", "a1", "wa", "shard_A", _round_hashes(art, "s" * 64), 90)
    coord.assign("run1", "rd2", "a2", "wb", "shard_B", _round_hashes(art, "s" * 64), 60)
    coord.start_round("run1", "rd2")
    specs = [("a1", "wa", "shard_A", 0, 90), ("a2", "wb", "shard_B", 32, 60)]
    ra, rb = run_two_real_workers(coord, cl, "run1", "rd2", {"wa": art, "wb": art}, specs)
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
    check("F4: shared worker model bytes", ra["wmh"] == rb["wmh"])
    # order invariance: reversed arrival (SAME artifacts/deltas)
    _, _, cl2, coord2 = _setup(19882)
    coord2.round_open("run1", "rd2b", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord2.register_round_model("run1", "rd2b", _round_hashes(art, "s" * 64), adapter_0_bytes=art[2])
    coord2.calibration_submit("run1", "rd2b", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord2.calibration_submit("run1", "rd2b", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord2.assign("run1", "rd2b", "a1", "wa", "shard_A", _round_hashes(art, "s" * 64), 90)
    coord2.assign("run1", "rd2b", "a2", "wb", "shard_B", _round_hashes(art, "s" * 64), 60)
    coord2.start_round("run1", "rd2b")
    specs_rev = [("a2", "wb", "shard_B", 32, 60), ("a1", "wa", "shard_A", 0, 90)]
    run_two_real_workers(coord2, cl2, "run1", "rd2b", {"wa": art, "wb": art}, specs_rev)
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
    # Round 2: SAME worker_base_model_v1 bytes; base_adapter = adapter_1 (exactly)
    coord.round_open("run1", "r2", {"max_micro_batch": 4, "max_sequence_length": 128})
    art2 = (artA[0], artA[1], c1["adapter_bytes"], c1["adapter_hash"])  # same base, adapter = adapter_1
    coord.register_round_model("run1", "r2", _round_hashes(art2, "s" * 64), adapter_0_bytes=c1["adapter_bytes"])
    coord.calibration_submit("run1", "r2", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.calibration_submit("run1", "r2", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "r2", "b1", "wa", "shard_A", _round_hashes(art2, "s" * 64), 90)
    coord.assign("run1", "r2", "b2", "wb", "shard_B", _round_hashes(art2, "s" * 64), 60)
    coord.start_round("run1", "r2")
    run_two_real_workers(coord, cl, "run1", "r2",
                         {"wa": art2, "wb": art2},
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
    check("R4: adapter1 != adapter0 (real training)", c1["adapter_hash"] != coord._conn.execute(
        "SELECT adapter_0_hash FROM rounds_r53 WHERE run_id='run1' AND round_id='r1'").fetchone()["adapter_0_hash"])
    check("R7: worker base invariant across rounds",
          coord._conn.execute("SELECT worker_model_hash FROM rounds_r53 WHERE run_id='run1' AND round_id='r1'").fetchone()["worker_model_hash"]
          == coord._conn.execute("SELECT worker_model_hash FROM rounds_r53 WHERE run_id='run1' AND round_id='r2'").fetchone()["worker_model_hash"])
    # negative: Round 2 with a DIFFERENT worker base model must be rejected
    other = build_worker_artifacts(WorkerNumericalModel())  # different base bytes
    other_art = (other[0], other[1], c1["adapter_bytes"], c1["adapter_hash"])
    try:
        coord.assign("run1", "r2", "b6", "wc", "sC", _round_hashes(other_art, "s" * 64), 60)
        check("R8: different worker base in round 2 rejected", False)
    except ValueError:
        check("R8: different worker base in round 2 rejected", True)
    # stale adapter rejected
    stale = _round_hashes(art2, "s" * 64); stale["base_adapter_hash"] = "f" * 64
    try:
        coord.assign("run1", "r2", "b3", "wc", "sC", stale, 60)
        check("R3: stale adapter rejected", False)
    except ValueError:
        check("R3: stale adapter rejected", True)
    # wrong worker_model_hash (shared invariant) rejected
    bad_wmh = _round_hashes(art2, "s" * 64); bad_wmh["worker_model_hash"] = "0" * 64
    try:
        coord.assign("run1", "r2", "b4", "wc", "sC", bad_wmh, 60)
        check("R5: wrong worker_model_hash rejected", False)
    except ValueError:
        check("R5: wrong worker_model_hash rejected", True)
    # duplicate assignment for same worker rejected
    try:
        coord.assign("run1", "r2", "b5", "wa", "sA", _round_hashes(art2, "s" * 64), 10)
        check("R6: duplicate worker assignment rejected", False)
    except ValueError:
        check("R6: duplicate worker assignment rejected", True)

def test_budget_enforced():
    """Assignment above calibration budget must be rejected."""
    art = build_worker_artifacts(WorkerNumericalModel())
    _, _, cl, coord = _setup(19889)
    coord.round_open("run1", "rd11", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.register_round_model("run1", "rd11", _round_hashes(art, "s" * 64), adapter_0_bytes=art[2])
    coord.calibration_submit("run1", "rd11", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    over = _round_hashes(art, "s" * 64); over["max_micro_batch"] = 8  # exceeds calibration 2 and profile 4
    try:
        coord.assign("run1", "rd11", "a1", "wa", "sA", over, 100)
        check("BE1: over-budget assignment rejected", False)
    except ValueError:
        check("BE1: over-budget assignment rejected", True)

def test_revisions_real():
    """rev1 ACTIVE → rev2 ACTIVE → rev1 SUPERSEDED; FedAvg excludes SUPERSEDED."""
    _, _, cl, coord = _setup(19884)
    art = build_worker_artifacts(WorkerNumericalModel())
    coord.round_open("run1", "rd3", {})
    coord.register_round_model("run1", "rd3", _round_hashes(art, "s" * 64), adapter_0_bytes=art[2])
    coord.calibration_submit("run1", "rd3", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord.assign("run1", "rd3", "a1", "wa", "shard_A", _round_hashes(art, "s" * 64), 90)
    coord.start_round("run1", "rd3")
    ra1 = real_worker_flow(cl, coord, "run1", "rd3", "a1", "wa", "shard_A",
                           art[0], art[1], art[2], art[3], offset=0, label_keep=90, update_suffix="v1")
    st1 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra1["cid"],)).fetchone()["status"]
    check("RV1: rev1 ACTIVE", st1 == "ACTIVE")
    ra2 = real_worker_flow(cl, coord, "run1", "rd3", "a1", "wa", "shard_A",
                           art[0], art[1], art[2], art[3], offset=64, label_keep=30, update_suffix="v2",
                           micro_unit_id="mu_wa_v2")
    st2 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra2["cid"],)).fetchone()["status"]
    check("RV2: rev2 ACTIVE after full flow", st2 == "ACTIVE")
    st1c = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra1["cid"],)).fetchone()["status"]
    check("RV4: rev1 SUPERSEDED after rev2 ACTIVE", st1c == "SUPERSEDED")
    # FedAvg must exclude SUPERSEDED (independent oracle from stored deltas)
    dA, _ = coord.fedavg("run1", "rd3")
    act_rows = coord._conn.execute(
        "SELECT delta_bundle_b64, delta_bundle_sha256 FROM contributions_r53 WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd3'").fetchall()
    check("RV5: only rev2 ACTIVE", len(act_rows) == 1)
    exp = delta_bundle_unpack(base64.b64decode(act_rows[0]["delta_bundle_b64"]), act_rows[0]["delta_bundle_sha256"])
    check("RV6: FedAvg excludes SUPERSEDED", torch.allclose(dA, exp["local_layers.0.linear1.lora_A.weight"], rtol=1e-5, atol=1e-6))

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
    """REAL process restart (F): A reaches COMMITTED → kill → restart → recover receipt
    byte-for-byte → idempotent upload → B completes → close → compare with no-restart."""
    db = _fresh_db()
    workdir = tempfile.mkdtemp(prefix="r53_restart_")
    art_json = os.path.join(workdir, "art.json")
    out_json = os.path.join(workdir, "out.json")
    script = os.path.join(workdir, "restart_worker.py")
    with open(script, "w") as f:
        f.write(f'''
import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from rc5_3_multiworker import RoundCoordinator, build_worker_artifacts, real_worker_flow, zero_adapter_artifact
from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_numerical_models import WorkerNumericalModel
from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash
from rc5_2_tensor_bundle import bundle_sha256
import time, sys, json, base64
DB = {db!r}; KEY = b"r53_stage_b_real_key_2026"; ART = {art_json!r}; OUT = {out_json!r}
srv, _ = serve(port=19886, db_path=DB, signing_key=KEY)
cl = JsonRpcClient("http://127.0.0.1:19886"); time.sleep(0.3)
coord = RoundCoordinator(DB, KEY)
def hashes(art):
    return {{"server_model_hash": "s"*64, "worker_model_hash": art[1], "base_adapter_hash": art[3],
             "partition_schema_hash": partition_schema_hash(), "adapter_schema_hash": adapter_schema_hash(),
             "numerical_profile_hash": numerical_profile_hash()}}
which = sys.argv[1]
if which == "A":
    coord.round_open("run1", "rd9", {{"max_micro_batch": 4, "max_sequence_length": 128}})
    art = build_worker_artifacts(WorkerNumericalModel())  # SHARED model (adapter == adapter_0)
    coord.register_round_model("run1", "rd9", hashes(art), adapter_0_bytes=art[2])
    coord.calibration_submit("run1", "rd9", "wa", {{"max_micro_batch": 2, "max_sequence_length": 128}})
    coord.calibration_submit("run1", "rd9", "wb", {{"max_micro_batch": 2, "max_sequence_length": 128}})
    coord.assign("run1", "rd9", "a1", "wa", "shard_A", hashes(art), 90)
    coord.assign("run1", "rd9", "a2", "wb", "shard_B", hashes(art), 60)
    coord.start_round("run1", "rd9")
    json.dump({{"art": [a.hex() if isinstance(a, bytes) else a for a in art]}}, open(ART, "w"))
    # Worker A stops EXACTLY at COMMITTED (no upload, no activation)
    res = real_worker_flow(cl, coord, "run1", "rd9", "a1", "wa", "shard_A",
                           art[0], art[1], art[2], art[3], offset=0, label_keep=90,
                           update_suffix="1", stop_at_commit=True)
    json.dump({{"receipt": res["receipt"], "delta_b64": res["delta_bundle_b64"], "delta_sha": res["delta_bundle_sha256"]}}, open(OUT, "w"))
    print("A DONE")
else:
    arts = json.load(open(ART))["art"]
    art = [bytes.fromhex(x) if isinstance(x, str) and len(x) > 64 else x for x in arts]
    saved = json.load(open(OUT))
    receipt = saved["receipt"]; delta_b64 = saved["delta_b64"]; delta_sha = saved["delta_sha"]
    # recover receipt byte-for-byte (persisted in units table at commit)
    row = coord._conn.execute("SELECT receipt_json FROM units WHERE unit_id=?", (receipt["unit_id"],)).fetchone()
    recovered = json.loads(row["receipt_json"]) if row else None
    same = recovered == receipt
    # idempotent upload of A (same receipt + same payload → same response, no nonce error)
    up1 = cl.call("checkpoint.upload", {{"receipt": receipt, "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha}})
    up2 = cl.call("checkpoint.upload", {{"receipt": receipt, "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha}})
    up_cid = up1.get("contribution_id", receipt["receipt_id"])
    cid = coord.register_uploaded_contribution(up_cid, "run1", "rd9", "a1", "wa")
    coord.validate_contribution(cid["contribution_id"]); coord.activate_contribution(cid["contribution_id"])
    # Worker B completes full flow
    rb = real_worker_flow(cl, coord, "run1", "rd9", "a2", "wb", "shard_B",
                          art[0], art[1], art[2], art[3], offset=32, label_keep=60, update_suffix="1")
    coord.ready_to_close("run1", "rd9")
    c = coord.close_round("run1", "rd9")
    json.dump({{"adapter_hash": c["adapter_hash"], "adapter_b64": c["adapter_bytes"].hex(),
                "receipt_same": same, "upload_idem": up1.get("status") == up2.get("status"),
                "cids": cid["contribution_id"]}}, open(OUT, "w"))
    print("B DONE")
''')
    r1 = subprocess.run(["python3", script, "A"], capture_output=True, text=True, timeout=240)
    r2 = subprocess.run(["python3", script, "B"], capture_output=True, text=True, timeout=240)
    check("P1: worker A process OK (COMMITTED)", r1.returncode == 0 and "A DONE" in r1.stdout)
    check("P2: worker B process OK (after restart)", r2.returncode == 0 and "B DONE" in r2.stdout)
    if r2.returncode != 0:
        print("   B stderr:", r2.stderr[-300:])
    out = json.load(open(out_json))
    check("P3: receipt recovered byte-for-byte", out["receipt_same"] is True)
    check("P4: upload idempotent after restart", out["upload_idem"] is True)
    check("P5: round closed after restart", len(out["adapter_hash"]) == 64)
    # compare with no-restart run on the SAME artifacts/round data
    arts = json.load(open(art_json))["art"]
    art2 = [bytes.fromhex(x) if isinstance(x, str) and len(x) > 64 else x for x in arts]
    _, _, cl3, coord3 = _setup(19888)
    coord3.round_open("run1", "rd9", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord3.register_round_model("run1", "rd9", _round_hashes(art2, "s" * 64), adapter_0_bytes=art2[2])
    coord3.calibration_submit("run1", "rd9", "wa", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord3.calibration_submit("run1", "rd9", "wb", {"max_micro_batch": 2, "max_sequence_length": 128})
    coord3.assign("run1", "rd9", "a1", "wa", "shard_A", _round_hashes(art2, "s" * 64), 90)
    coord3.assign("run1", "rd9", "a2", "wb", "shard_B", _round_hashes(art2, "s" * 64), 60)
    coord3.start_round("run1", "rd9")
    run_two_real_workers(coord3, cl3, "run1", "rd9", {"wa": art2, "wb": art2},
                         [("a1", "wa", "shard_A", 0, 90), ("a2", "wb", "shard_B", 32, 60)])
    coord3.ready_to_close("run1", "rd9")
    c3 = coord3.close_round("run1", "rd9")
    check("P6: restart adapter == no-restart adapter", out["adapter_hash"] == c3["adapter_hash"])
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
        coord.register_uploaded_contribution(ra["receipt_id"], "run1", "rd5", "a2", "wb")
        check("I2: alien contribution rejected", False)
    except ValueError:
        check("I2: alien contribution rejected", True)

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
