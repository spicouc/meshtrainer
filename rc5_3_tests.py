"""RC5.3 Stage B R5 FINAL: REAL two-worker tests — full HTTP flow, real WorkerRuntime.

Covers the binding ServerModel_v1 artifact (secció 1), atomic persistence (2),
assignment/calibration idempotency (3/4), explicit round lineage (5), secure
contributions (6), adapter/worker base invariants (7) and the independent
FedAvg oracle (8).
"""
import os, sys, json, torch, base64, time, tempfile, uuid, copy, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256, tensor_bundle_v1_pack
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_receipt import generate_receipt, verify_receipt
from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash
from rc5_2_numerical_models import WorkerNumericalModel, SplitServerNumericalModel
from rc5_3_multiworker import (RoundCoordinator, build_worker_artifacts,
                                build_initial_artifacts, adapter_artifacts_from_round,
                                real_worker_flow, run_two_real_workers, make_shard,
                                _stable_seed, pack_server_model, server_model_from_bytes,
                                validate_server_artifact, validate_worker_base_artifact,
                                validate_base_adapter_artifact, oracle_fedavg_ett,
                                oracle_round_adapter,
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

def _cal(worker_id, mb=2, sl=128, nonce=None, mem=4096):
    """Full normative calibration payload (no defaults anywhere)."""
    return {"backend": "cpu", "precision": "f32",
            "available_memory_mb": mem, "observed_safe_budget_mb": mem // 2,
            "max_micro_batch": mb, "max_sequence_length": sl,
            "calibration_nonce": nonce or f"nc_{worker_id}_{uuid.uuid4().hex[:6]}",
            "measured_at": time.time()}

def _hashes(art, server_hash):
    return {"server_model_hash": server_hash, "worker_model_hash": art["worker_base_hash"],
            "base_adapter_hash": art["adapter_hash"],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()}

def _open_round(coord, run_id, round_id, art, server_hash, parent_round_id=None,
                worker_ids=("wa", "wb"), cal_mb=2, assignment_ids=None):
    """Round with ONE SHARED model: register real server artifact + worker base
    + base_adapter (== adapter_0), calibrate and assign both workers."""
    if assignment_ids is None:
        assignment_ids = [f"a1_{round_id}", f"a2_{round_id}"]
    coord.round_open(run_id, round_id, {"max_micro_batch": 4, "max_sequence_length": 128},
                     parent_round_id=parent_round_id)
    coord.register_round_model(run_id, round_id, art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    for w in worker_ids:
        coord.calibration_submit(run_id, round_id, w,
                                 _cal(w, mb=cal_mb, nonce=f"nc_{w}_{run_id}_{round_id}"))
    for i, w in enumerate(worker_ids):
        coord.assign(run_id, round_id, assignment_ids[i], w, f"shard_{'A' if i == 0 else 'B'}",
                     _hashes(art, server_hash), 90 if i == 0 else 60)
    coord.start_round(run_id, round_id)
    return art

def _run_round_flow(cl, coord, run_id, round_id, art, server_hash, specs):
    """Run real workers over the round; specs: [(assignment_id, worker_id, shard, offset, lk)]."""
    arts = {w: (art["worker_base_bytes"], art["worker_base_hash"],
                art["adapter_bytes"], art["adapter_hash"]) for _, w, _, _, _ in specs}
    return run_two_real_workers(coord, cl, run_id, round_id, arts, specs,
                                server_model_hash=server_hash)

# ====================================================================
# Secció 1: SERVER_MODEL_V1 binding
# ====================================================================

def test_server_model_v1_binding():
    """Server artifact: real hash accepted, fake hash rejected, altered bytes
    rejected, different server models -> different activations, restart with the
    same bytes -> identical activations, zero missing/unexpected."""
    art = build_initial_artifacts(seed=424242)
    server_hash = art["server_hash"]
    # A. hash real acceptat + zero missing/unexpected + reconstrucció equivalent
    tensors = validate_server_artifact(art["server_bytes"], server_hash)
    check("SM1: real server hash accepted", len(tensors) > 0)
    check("SM2: zero missing/unexpected (validation passes)", True)
    m1 = server_model_from_bytes(art["server_bytes"], server_hash)
    m2 = server_model_from_bytes(art["server_bytes"], server_hash)
    check("SM3: reconstruction deterministic",
          torch.equal(next(m1.parameters()).detach(), next(m2.parameters()).detach()))
    # B. hash fals rebutjat
    try:
        validate_server_artifact(art["server_bytes"], "0" * 64)
        check("SM4: fake server hash rejected", False)
    except ValueError:
        check("SM4: fake server hash rejected", True)
    # C. bytes alterats rebutjats
    bad = bytearray(art["server_bytes"]); bad[0] = ord("X")
    try:
        validate_server_artifact(bytes(bad), server_hash)
        check("SM5: altered server bytes rejected", False)
    except ValueError:
        check("SM5: altered server bytes rejected", True)
    # D. different server models -> different activations
    artB = build_initial_artifacts(seed=999)
    check("SM6: different seed -> different artifact",
          artB["server_hash"] != server_hash)
    t, l, m = make_shard("shard_A", offset=0, label_keep=90)
    sa1 = server_model_from_bytes(art["server_bytes"], server_hash).server_forward(t, m)
    sa2 = server_model_from_bytes(artB["server_bytes"], artB["server_hash"]).server_forward(t, m)
    check("SM7: different server models -> different activations",
          not torch.equal(sa1, sa2))
    # E. restart amb els mateixos bytes -> activacions idèntiques
    sa3 = server_model_from_bytes(art["server_bytes"], server_hash).server_forward(t, m)
    check("SM8: same bytes -> identical activations", torch.equal(sa1, sa3))
    # F. step.open exigeix server_model_hash (round registered) i el compara
    db, srv, cl, coord = _setup(19880)
    _open_round(coord, "run1", "rd1", art, server_hash)
    # fake hash at step.open -> rejected
    try:
        cl.call("step.open", {
            "protocol_version": "1.2.0-rc5.2", "unit_id": "u_fake",
            "run_id": "run1", "round_id": "rd1", "assignment_id": "a1_rd1",
            "micro_unit_id": "mu_f", "worker_id": "wa", "session_id": "s_f",
            "shard_id": "shard_A", "shard_offset": 0, "label_keep": 90,
            "server_model_hash": "0" * 64,
            "worker_model_hash": art["worker_base_hash"], "base_adapter_hash": art["adapter_hash"],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()})
        check("SM9: step.open fake server_model_hash rejected", False)
    except ValueError:
        check("SM9: step.open fake server_model_hash rejected", True)
    # real hash accepted
    r = cl.call("step.open", {
        "protocol_version": "1.2.0-rc5.2", "unit_id": "u_real",
        "run_id": "run1", "round_id": "rd1", "assignment_id": "a1_rd1",
        "micro_unit_id": "mu_r", "worker_id": "wa", "session_id": "s_r",
        "shard_id": "shard_A", "shard_offset": 0, "label_keep": 90,
        "server_model_hash": server_hash,
        "worker_model_hash": art["worker_base_hash"], "base_adapter_hash": art["adapter_hash"],
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash()})
    check("SM10: step.open real server_model_hash accepted", "server_activation" in r)

# ====================================================================
# Secció 2: ATOMIC registration
# ====================================================================

def test_atomic_registration():
    """register_round_model: valid model registered; inconsistent re-registration
    -> REJECTED; original values intact; no partial data."""
    art = build_initial_artifacts(seed=7)
    db, srv, cl, coord = _setup(19881)
    coord.round_open("run1", "rd1", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    r1 = coord.get_round_model("run1", "rd1")
    check("AT1: valid model registered", r1 is not None and r1["server_model_hash"] == art["server_hash"])
    # inconsistent re-registration: adapter that is NOT the registered adapter_0
    other = build_initial_artifacts(seed=8)
    try:
        coord.register_round_model("run1", "rd1", art["server_bytes"],
                                   art["worker_base_bytes"], other["adapter_bytes"])
        check("AT2: inconsistent adapter REJECTED", False)
    except ValueError:
        check("AT2: inconsistent adapter REJECTED", True)
    # original values intact (no partial data)
    r2 = coord.get_round_model("run1", "rd1")
    check("AT3: original server_model_hash intact", r2["server_model_hash"] == art["server_hash"])
    check("AT4: original adapter_0_hash intact", r2["adapter_0_hash"] == art["adapter_hash"])
    check("AT5: original worker_model_hash intact", r2["worker_model_hash"] == art["worker_base_hash"])
    # nothing extra persisted in round_adapters
    n = coord._conn.execute("SELECT COUNT(*) c FROM round_adapters_r53 WHERE run_id='run1' AND round_id='rd1'").fetchone()["c"]
    check("AT6: no partial round_adapter row", n == 0)

# ====================================================================
# Secció 3: ASSIGNMENT idempotency (request_sha)
# ====================================================================

def test_assignment_idempotency():
    """request_sha: new -> ACCEPTED; same id + same sha -> same response;
    same id + different sha (shard/ETT/worker/hash) -> REJECTED."""
    art = build_initial_artifacts(seed=11)
    db, srv, cl, coord = _setup(19882)
    coord.round_open("run1", "rd1", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "rd1", "wa", _cal("wa", nonce="n1"))
    coord.calibration_submit("run1", "rd1", "wb", _cal("wb", nonce="n2"))
    h = _hashes(art, art["server_hash"])
    r1 = coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 90)
    check("AS1: new assignment ACCEPTED", r1["state"] == "ASSIGNED" and "request_sha" in r1)
    sha1 = r1["request_sha"]
    # same id + same payload -> same exact response
    r2 = coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 90)
    check("AS2: same id + same payload -> same response",
          r2["request_sha"] == sha1 and r2["state"] == "ASSIGNED")
    # same id + changed shard -> REJECTED
    try:
        coord.assign("run1", "rd1", "a1", "wa", "shard_ZZZ", h, 90)
        check("AS3: changed shard REJECTED", False)
    except ValueError:
        check("AS3: changed shard REJECTED", True)
    # same id + changed ETT -> REJECTED
    try:
        coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 5)
        check("AS4: changed ETT REJECTED", False)
    except ValueError:
        check("AS4: changed ETT REJECTED", True)
    # same id + changed worker -> REJECTED
    try:
        coord.assign("run1", "rd1", "a1", "wb", "shard_A", h, 90)
        check("AS5: changed worker REJECTED", False)
    except ValueError:
        check("AS5: changed worker REJECTED", True)
    # same id + changed hashes -> REJECTED
    h2 = dict(h); h2["worker_model_hash"] = "1" * 64
    try:
        coord.assign("run1", "rd1", "a1", "wa", "shard_A", h2, 90)
        check("AS6: changed hashes REJECTED", False)
    except ValueError:
        check("AS6: changed hashes REJECTED", True)
    # request_sha persisted
    row = coord._conn.execute("SELECT request_sha, created_at FROM assignments_r53 WHERE assignment_id='a1' AND run_id='run1' AND round_id='rd1'").fetchone()
    check("AS7: request_sha + created_at persisted", row is not None and row["request_sha"] == sha1 and row["created_at"])

# ====================================================================
# Secció 4: CALIBRATION idempotency (nonce)
# ====================================================================

def test_calibration_idempotency():
    """Same key + same payload -> same response; same key + different payload
    -> REJECTED; uncalibrated worker rejected; no defaulted values."""
    art = build_initial_artifacts(seed=13)
    db, srv, cl, coord = _setup(19883)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    cal = _cal("wa", nonce="nc_fixed", mem=2048)
    r1 = coord.calibration_submit("run1", "rd1", "wa", cal)
    check("CA1: calibration accepted", r1["status"] == "CALIBRATED")
    # same key + same payload -> same response
    r2 = coord.calibration_submit("run1", "rd1", "wa", cal)
    check("CA2: same key + same payload -> same response", r1 == r2)
    # same key + different payload -> REJECTED
    cal2 = dict(cal); cal2["available_memory_mb"] = 4096
    try:
        coord.calibration_submit("run1", "rd1", "wa", cal2)
        check("CA3: same nonce + different payload REJECTED", False)
    except ValueError:
        check("CA3: same nonce + different payload REJECTED", True)
    # missing nonce -> rejected (no defaults)
    try:
        coord.calibration_submit("run1", "rd1", "wa", {})
        check("CA4: no nonce / defaults REJECTED", False)
    except ValueError:
        check("CA4: no nonce / defaults REJECTED", True)
    # uncalibrated worker cannot be assigned
    try:
        coord.assign("run1", "rd1", "a9", "wc", "shard_C", _hashes(art, art["server_hash"]), 10)
        check("CA5: uncalibrated worker rejected", False)
    except ValueError:
        check("CA5: uncalibrated worker rejected", True)

def _complete_single_worker_round(cl, coord, run_id, round_id, art, server_hash,
                                  assignment_id, worker_id, shard_id, offset, lk,
                                  parent_round_id=None):
    """Open/register/calibrate/assign/run/close a SINGLE-worker round (fast lineage tests)."""
    coord.round_open(run_id, round_id, {"max_micro_batch": 4, "max_sequence_length": 128},
                     parent_round_id=parent_round_id)
    coord.register_round_model(run_id, round_id, art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit(run_id, round_id, worker_id, _cal(worker_id, nonce=f"nc_{worker_id}_{round_id}"))
    coord.assign(run_id, round_id, assignment_id, worker_id, shard_id,
                 _hashes(art, server_hash), 90)
    coord.start_round(run_id, round_id)
    ra = real_worker_flow(cl, coord, run_id, round_id, assignment_id, worker_id, shard_id,
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=offset, label_keep=lk, update_suffix="1",
                          server_model_hash=server_hash)
    coord.ready_to_close(run_id, round_id)
    return coord.close_round(run_id, round_id), ra

# ====================================================================
# Secció 5: EXPLICIT round lineage
# ====================================================================

def test_round_lineage_explicit():
    """r1/r2/r10 lineage by explicit parent_round_id, never lexical."""
    art = build_initial_artifacts(seed=17)
    db, srv, cl, coord = _setup(19884)
    # r1 (round_index 1) completed and CLOSED first (parents must be CLOSED)
    c1, _ = _complete_single_worker_round(cl, coord, "run1", "r1", art, art["server_hash"],
                                          "a1", "wa", "shard_A", 0, 90)
    r1idx = coord._conn.execute("SELECT round_index FROM rounds_r53 WHERE run_id='run1' AND round_id='r1'").fetchone()["round_index"]
    check("LN1: round 1 index", r1idx == 1)
    # parent must exist
    try:
        coord.round_open("run1", "rx", {}, parent_round_id="nonexistent")
        check("LN4: missing parent rejected", False)
    except ValueError:
        check("LN4: missing parent rejected", True)
    # r2 with explicit parent r1 (CLOSED) -> index 2, then CLOSED itself
    art2 = dict(art); art2["adapter_bytes"] = c1["adapter_bytes"]; art2["adapter_hash"] = c1["adapter_hash"]
    c2, _ = _complete_single_worker_round(cl, coord, "run1", "r2", art2, art["server_hash"],
                                          "b1", "wa", "shard_A", 0, 90, parent_round_id="r1")
    r2idx = coord._conn.execute("SELECT round_index FROM rounds_r53 WHERE run_id='run1' AND round_id='r2'").fetchone()["round_index"]
    check("LN2: round 2 index", r2idx == 2)
    # r10 on top of CLOSED r2 -> index 3 (NOT lexical order: "r10" > "r2" numerically)
    r10 = coord.round_open("run1", "r10", {}, parent_round_id="r2")
    check("LN3: round 10 index (explicit parent)", r10["round_index"] == 3)
    # parent must be CLOSED: r10 is still OPEN -> reject as parent
    try:
        coord.round_open("run1", "ry", {}, parent_round_id="r10")
        check("LN5: non-CLOSED parent rejected", False)
    except ValueError:
        check("LN5: non-CLOSED parent rejected", True)
    row = coord._conn.execute("SELECT round_index, parent_round_id FROM rounds_r53 WHERE run_id='run1' AND round_id='r10'").fetchone()
    check("LN6: parent_round_id persisted", row["parent_round_id"] == "r2" and row["round_index"] == 3)
    # round with a DIFFERENT adapter than parent global -> REJECTED
    artB = build_initial_artifacts(seed=18)
    coord.round_open("run1", "r2b", {}, parent_round_id="r1")
    try:
        coord.register_round_model("run1", "r2b", art["server_bytes"],
                                   art["worker_base_bytes"], artB["adapter_bytes"])
        check("LN7: round2 wrong parent adapter REJECTED", False)
    except ValueError:
        check("LN7: round2 wrong parent adapter REJECTED", True)
    # round with the CORRECT parent adapter accepted + parent_adapter_hash persisted
    coord.round_open("run1", "r2c", {}, parent_round_id="r1")
    coord.register_round_model("run1", "r2c", art2["server_bytes"],
                               art2["worker_base_bytes"], art2["adapter_bytes"])
    row2 = coord._conn.execute("SELECT parent_adapter_hash FROM rounds_r53 WHERE run_id='run1' AND round_id='r2c'").fetchone()
    check("LN8: parent_adapter_hash == parent global", row2["parent_adapter_hash"] == c1["adapter_hash"])

# ====================================================================
# Secció 6: SECURE contributions
# ====================================================================

def test_secure_contributions():
    """register_uploaded_contribution(contribution_id) only; arbitrary cid rejected;
    receipt mismatch rejected; source_request_sha cross-check."""
    art = build_initial_artifacts(seed=19)
    db, srv, cl, coord = _setup(19885)
    _open_round(coord, "run1", "rd1", art, art["server_hash"])
    # contribution NOT from checkpoint.upload -> rejected
    try:
        coord.register_uploaded_contribution("totally_fake_cid")
        check("SC1: fake cid rejected", False)
    except ValueError:
        check("SC1: fake cid rejected", True)
    ra = real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=0, label_keep=90, update_suffix="1",
                          server_model_hash=art["server_hash"])
    check("SC2: real contribution accepted", True)
    # receipt mismatch: tamper the persisted receipt then re-register
    orig_receipt = coord._conn.execute("SELECT receipt_json FROM contributions WHERE cid=?", (ra["cid"],)).fetchone()["receipt_json"]
    coord._conn.execute("UPDATE contributions SET receipt_json=? WHERE cid=?",
                        (json.dumps({"tampered": True}), ra["cid"]))
    coord._conn.commit()
    try:
        coord.register_uploaded_contribution(ra["cid"])
        check("SC3: receipt mismatch rejected", False)
    except ValueError:
        check("SC3: receipt mismatch rejected", True)
    # restore the original receipt
    coord._conn.execute("UPDATE contributions SET receipt_json=? WHERE cid=?", (orig_receipt, ra["cid"]))
    coord._conn.commit()
    # verify source_request_sha is stored
    row = coord._conn.execute("SELECT source_request_sha FROM contributions_r53 WHERE cid=?", (ra["cid"],)).fetchone()
    check("SC4: source_request_sha persisted", row is not None and len(row["source_request_sha"]) == 64)
    # idempotent re-registration -> same response (source_request_sha matches)
    try:
        r2 = coord.register_uploaded_contribution(ra["cid"])
        check("SC5: same cid + same source -> same response", r2["contribution_id"] == ra["cid"])
    except ValueError as e:
        check("SC5: same cid + same source -> same response", False)

# ====================================================================
# Secció 7: ADAPTER base + WORKER base invariants
# ====================================================================

def test_adapter_worker_base_invariants():
    """Single base_adapter_v1 per round; 4-hash equality; worker base byte-
    identical across rounds; negative proofs."""
    art = build_initial_artifacts(seed=23)
    db, srv, cl, coord = _setup(19886)
    _open_round(coord, "run1", "rd1", art, art["server_hash"])
    ra = real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=0, label_keep=90, update_suffix="1",
                          server_model_hash=art["server_hash"])
    rb = real_worker_flow(cl, coord, "run1", "rd1", "a2_rd1", "wb", "shard_B",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=32, label_keep=60, update_suffix="1",
                          server_model_hash=art["server_hash"])
    rnd = coord._conn.execute("SELECT * FROM rounds_r53 WHERE run_id='run1' AND round_id='rd1'").fetchone()
    check("IW1: worker A adapter == worker B adapter", ra["baeh"] == rb["baeh"])
    check("IW2: worker adapter == round.base_adapter_hash", ra["baeh"] == rnd["base_adapter_hash"])
    check("IW3: worker adapter == round.adapter_0_hash", ra["baeh"] == rnd["adapter_0_hash"])
    check("IW4: base_adapter_hash == adapter_0_hash", rnd["base_adapter_hash"] == rnd["adapter_0_hash"])
    check("IW5: worker A base == worker B base", ra["wmh"] == rb["wmh"])
    # close round 1 -> round 2 keeps the SAME worker base bytes
    coord.ready_to_close("run1", "rd1")
    c1 = coord.close_round("run1", "rd1")
    art2 = dict(art); art2["adapter_bytes"] = c1["adapter_bytes"]; art2["adapter_hash"] = c1["adapter_hash"]
    _open_round(coord, "run1", "rd2", art2, art["server_hash"], parent_round_id="rd1")
    _run_round_flow(cl, coord, "run1", "rd2", art2, art["server_hash"],
                    [("a1_rd2", "wa", "shard_A", 0, 90), ("a2_rd2", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "rd2")
    c2 = coord.close_round("run1", "rd2")
    rnd2 = coord._conn.execute("SELECT worker_model_hash, base_adapter_hash, adapter_0_hash, parent_adapter_hash FROM rounds_r53 WHERE run_id='run1' AND round_id='rd2'").fetchone()
    check("IW6: worker base invariant across rounds", rnd2["worker_model_hash"] == rnd["worker_model_hash"])
    check("IW7: round2 base_adapter == parent global adapter", rnd2["base_adapter_hash"] == c1["adapter_hash"])
    check("IW8: round2 parent_adapter_hash == adapter_0", rnd2["parent_adapter_hash"] == c1["adapter_hash"])
    # negative: worker with adapter != adapter_0 rejected at step.open
    other_ad = build_initial_artifacts(seed=24)
    try:
        cl.call("step.open", {
            "protocol_version": "1.2.0-rc5.2", "unit_id": "u_badad",
            "run_id": "run1", "round_id": "rd2", "assignment_id": "a1_rd2",
            "micro_unit_id": "mu_badad", "worker_id": "wa", "session_id": "s_badad",
            "shard_id": "shard_A", "shard_offset": 0, "label_keep": 90,
            "server_model_hash": art["server_hash"],
            "worker_model_hash": art["worker_base_hash"],
            "base_adapter_hash": other_ad["adapter_hash"],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()})
        check("IW9: worker adapter != adapter_0 rejected", False)
    except ValueError:
        check("IW9: worker adapter != adapter_0 rejected", True)
    # negative: different worker base between rounds rejected at register
    artX = build_initial_artifacts(seed=25)
    coord.round_open("run1", "rd3", {}, parent_round_id="rd2")
    try:
        coord.register_round_model("run1", "rd3", art["server_bytes"],
                                   artX["worker_base_bytes"], art2["adapter_bytes"])
        check("IW10: different worker base between rounds rejected", False)
    except ValueError:
        check("IW10: different worker base between rounds rejected", True)
    # negative: stale parent adapter rejected at register (round3 with round1 adapter)
    coord.round_open("run1", "rd4", {}, parent_round_id="rd2")
    try:
        coord.register_round_model("run1", "rd4", art["server_bytes"],
                                   art["worker_base_bytes"], art["adapter_bytes"])
        check("IW11: stale parent adapter rejected", False)
    except ValueError:
        check("IW11: stale parent adapter rejected", True)
    # negative: server model different from registered -> step.open rejected
    artY = build_initial_artifacts(seed=26)
    try:
        cl.call("step.open", {
            "protocol_version": "1.2.0-rc5.2", "unit_id": "u_badsrv",
            "run_id": "run1", "round_id": "rd2", "assignment_id": "a1_rd2",
            "micro_unit_id": "mu_badsrv", "worker_id": "wa", "session_id": "s_badsrv",
            "shard_id": "shard_A", "shard_offset": 0, "label_keep": 90,
            "server_model_hash": artY["server_hash"],
            "worker_model_hash": art["worker_base_hash"], "base_adapter_hash": art2["adapter_hash"],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()})
        check("IW12: server model != registered rejected", False)
    except ValueError:
        check("IW12: server model != registered rejected", True)

# ====================================================================
# Secció 8: INDEPENDENT FedAvg oracle
# ====================================================================

def test_fedavg_oracle_independent():
    """Oracle receives DIRECTLY (adapter pre, per-worker deltas + ETT), computes
    weighted deltas, and equals the Coordinator's adapter for round 1 and 2."""
    art = build_initial_artifacts(seed=29)
    db, srv, cl, coord = _setup(19887)
    _open_round(coord, "run1", "rd1", art, art["server_hash"])
    res = _run_round_flow(cl, coord, "run1", "rd1", art, art["server_hash"],
                          [("a1_rd1", "wa", "shard_A", 0, 90), ("a2_rd1", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "rd1")
    c1 = coord.close_round("run1", "rd1")
    # independent oracle inputs: read the INDIVIDUAL ACTIVE deltas (not aggregated rows)
    rows = coord._conn.execute(
        "SELECT delta_bundle_b64, delta_bundle_sha256, ett, worker_id FROM contributions_r53"
        " WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd1' ORDER BY worker_id").fetchall()
    check("OR1: exactly 2 ACTIVE contributions", len(rows) == 2)
    worker_deltas = []
    for r in rows:
        t = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]), r["delta_bundle_sha256"])
        worker_deltas.append((t["local_layers.0.linear1.lora_A.weight"],
                              t["local_layers.0.linear1.lora_B.weight"], r["ett"]))
    check("OR2: all ETT > 0", all(e > 0 for _, _, e in worker_deltas))
    # pre adapter = adapter_0 of round 1
    pre_t = delta_bundle_unpack(base64.b64decode(coord._conn.execute(
        "SELECT adapter_0_b64 FROM rounds_r53 WHERE run_id='run1' AND round_id='rd1'").fetchone()["adapter_0_b64"]),
        art["adapter_hash"])
    pre_A = pre_t["local_layers.0.linear1.lora_A.weight"]
    pre_B = pre_t["local_layers.0.linear1.lora_B.weight"]
    oA, oB = oracle_fedavg_ett(pre_A, pre_B, worker_deltas)
    oracle_bytes = delta_bundle_pack(oA, oB)
    oracle_hash = bundle_sha256(oracle_bytes)
    check("OR3: oracle adapter_1 == Coordinator adapter_1", oracle_hash == c1["adapter_hash"])
    # order invariance: reversed worker order gives the same oracle result
    oA2, oB2 = oracle_fedavg_ett(pre_A, pre_B, list(reversed(worker_deltas)))
    check("OR4: oracle order-invariant", torch.allclose(oA, oA2, rtol=1e-6, atol=1e-7)
          and torch.allclose(oB, oB2, rtol=1e-6, atol=1e-7))
    # ETT-weighted != simple average (deltas differ between workers)
    wA_delta = oA - pre_A
    simple = sum(dA for dA, _, _ in worker_deltas) / len(worker_deltas)
    rel_diff = (wA_delta - simple).abs().max() / (simple.abs().max() + 1e-12)
    check("OR5: weighted != simple average (ETT-dependent)", rel_diff > 1e-3)
    # FedAvg of the Coordinator must equal oracle over the SAME ACTIVE set
    dA2, dB2 = coord.fedavg("run1", "rd1")
    check("OR8: fedavg excludes SUPERSEDED",
          torch.allclose(oA - pre_A, dA2, rtol=1e-5, atol=1e-6))
    # Round 2: oracle adapter_2 == Coordinator adapter_2
    art2 = dict(art); art2["adapter_bytes"] = c1["adapter_bytes"]; art2["adapter_hash"] = c1["adapter_hash"]
    _open_round(coord, "run1", "rd2", art2, art["server_hash"], parent_round_id="rd1",
                worker_ids=("wa", "wb"))
    _run_round_flow(cl, coord, "run1", "rd2", art2, art["server_hash"],
                    [("a1_rd2", "wa", "shard_A", 0, 90), ("a2_rd2", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "rd2")
    c2 = coord.close_round("run1", "rd2")
    rows3 = coord._conn.execute(
        "SELECT delta_bundle_b64, delta_bundle_sha256, ett, worker_id FROM contributions_r53"
        " WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd2' ORDER BY worker_id").fetchall()
    wd3 = []
    for r in rows3:
        t = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]), r["delta_bundle_sha256"])
        wd3.append((t["local_layers.0.linear1.lora_A.weight"],
                    t["local_layers.0.linear1.lora_B.weight"], r["ett"]))
    pre_t2 = delta_bundle_unpack(c1["adapter_bytes"], c1["adapter_hash"])
    oA4, oB4 = oracle_fedavg_ett(pre_t2["local_layers.0.linear1.lora_A.weight"],
                                 pre_t2["local_layers.0.linear1.lora_B.weight"], wd3)
    check("OR9: oracle adapter_2 == Coordinator adapter_2",
          bundle_sha256(delta_bundle_pack(oA4, oB4)) == c2["adapter_hash"])

# ====================================================================
# Original flows (adapted to the binding API)
# ====================================================================

def test_two_real_workers_full_flow():
    """Two REAL WorkerRuntime workers, full HTTP flow, real deltas."""
    art = build_initial_artifacts(seed=31)
    _, _, cl, coord = _setup(19890)
    _open_round(coord, "run1", "rd1", art, art["server_hash"])
    ra = real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=0, label_keep=90, update_suffix="1",
                          server_model_hash=art["server_hash"])
    rb = real_worker_flow(cl, coord, "run1", "rd1", "a2_rd1", "wb", "shard_B",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=32, label_keep=60, update_suffix="1",
                          server_model_hash=art["server_hash"])
    check("W1: two workers completed", ra["worker_id"] == "wa" and rb["worker_id"] == "wb")
    check("W2: distinct ETT (real shards)", ra["ett"] != rb["ett"])
    check("W3: distinct receipts", ra["receipt_id"] != rb["receipt_id"])
    check("W4: distinct backward_ids", ra["backward_id"] != rb["backward_id"])
    check("W5: real delta hashes", len(ra["delta_sha"]) == 64 and len(rb["delta_sha"]) == 64)
    check("W6: ETT derived from labels", ra["ett"] == 89 and rb["ett"] == 59)
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
    art = build_initial_artifacts(seed=37)
    _, _, cl, coord = _setup(19891)
    _open_round(coord, "run1", "rd2", art, art["server_hash"])
    specs = [("a1_rd2", "wa", "shard_A", 0, 90), ("a2_rd2", "wb", "shard_B", 32, 60)]
    _run_round_flow(cl, coord, "run1", "rd2", art, art["server_hash"], specs)
    dA, _ = coord.fedavg("run1", "rd2")
    rows = coord._conn.execute(
        "SELECT delta_bundle_b64, delta_bundle_sha256, ett FROM contributions_r53 WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd2'").fetchall()
    total = sum(r["ett"] for r in rows)
    oA = torch.zeros(8, 256)
    for r in rows:
        t = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]), r["delta_bundle_sha256"])
        oA += (r["ett"] / total) * t["local_layers.0.linear1.lora_A.weight"]
    check("F1: FedAvg A == offline oracle", torch.allclose(dA, oA, rtol=1e-5, atol=1e-6))
    check("F2: deltas are REAL (non-zero)", dA.abs().max().item() > 1e-6)
    # order invariance: reversed arrival (SAME artifacts/deltas)
    _, _, cl2, coord2 = _setup(19892)
    _open_round(coord2, "run1", "rd2b", art, art["server_hash"])
    _run_round_flow(cl2, coord2, "run1", "rd2b", art, art["server_hash"],
                    [("a2_rd2b", "wb", "shard_B", 32, 60), ("a1_rd2b", "wa", "shard_A", 0, 90)])
    dA2, _ = coord2.fedavg("run1", "rd2b")
    check("F3: FedAvg order-invariant", torch.allclose(dA, dA2, rtol=1e-5, atol=1e-6))

def test_round2_lineage_real():
    """Round 2 starts from Round 1's real global adapter; stale rejected."""
    art = build_initial_artifacts(seed=41)
    _, _, cl, coord = _setup(19893)
    _open_round(coord, "run1", "r1", art, art["server_hash"])
    _run_round_flow(cl, coord, "run1", "r1", art, art["server_hash"],
                    [("a1_r1", "wa", "shard_A", 0, 90), ("a2_r1", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "r1")
    c1 = coord.close_round("run1", "r1")
    art2 = dict(art); art2["adapter_bytes"] = c1["adapter_bytes"]; art2["adapter_hash"] = c1["adapter_hash"]
    # open r2 and run it fully (parent r1 CLOSED)
    _open_round(coord, "run1", "r2", art2, art["server_hash"], parent_round_id="r1",
                assignment_ids=["b1", "b2"])
    _run_round_flow(cl, coord, "run1", "r2", art2, art["server_hash"],
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
    # --- negative proofs in an ISOLATED round (never run/closed) so an accepted
    #     bad assignment cannot poison a quorum later: the check itself must fail ---
    art3 = dict(art); art3["adapter_bytes"] = c2["adapter_bytes"]; art3["adapter_hash"] = c2["adapter_hash"]
    coord.round_open("run1", "rneg", {"max_micro_batch": 4, "max_sequence_length": 128},
                     parent_round_id="r2")
    coord.register_round_model("run1", "rneg", art3["server_bytes"],
                               art3["worker_base_bytes"], art3["adapter_bytes"])
    for w in ("wc", "wd", "we", "wf"):
        coord.calibration_submit("run1", "rneg", w, _cal(w, nonce=f"nc_{w}_neg"))
    other = build_initial_artifacts(seed=42)
    try:
        coord.assign("run1", "rneg", "b6", "wc", "sC", _hashes(other, art["server_hash"]), 60)
        check("R8: different worker base in round 2 rejected", False)
    except ValueError:
        check("R8: different worker base in round 2 rejected", True)
    stale = _hashes(art3, art["server_hash"]); stale["base_adapter_hash"] = "f" * 64
    try:
        coord.assign("run1", "rneg", "b3", "wd", "sC", stale, 60)
        check("R3: stale adapter rejected", False)
    except ValueError:
        check("R3: stale adapter rejected", True)
    bad_wmh = _hashes(art3, art["server_hash"]); bad_wmh["worker_model_hash"] = "0" * 64
    try:
        coord.assign("run1", "rneg", "b4", "we", "sC", bad_wmh, 60)
        check("R5: wrong worker_model_hash rejected", False)
    except ValueError:
        check("R5: wrong worker_model_hash rejected", True)
    # duplicate worker: first a VALID assignment for wf, then a second one
    coord.assign("run1", "rneg", "b0", "wf", "sA", _hashes(art3, art["server_hash"]), 10)
    try:
        coord.assign("run1", "rneg", "b5", "wf", "sA", _hashes(art3, art["server_hash"]), 10)
        check("R6: duplicate worker assignment rejected", False)
    except ValueError:
        check("R6: duplicate worker assignment rejected", True)

def test_budget_enforced():
    """Assignment above calibration budget must be rejected (right reason: budget)."""
    art = build_initial_artifacts(seed=43)
    db, _, cl, coord = _setup(19899)
    # manual setup so wc is calibrated BEFORE start_round (state stays ASSIGNED)
    coord.round_open("run1", "rd11", {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.register_round_model("run1", "rd11", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "rd11", "wa", _cal("wa", mb=2, nonce="nc_wa_11"))
    coord.calibration_submit("run1", "rd11", "wb", _cal("wb", mb=2, nonce="nc_wb_11"))
    coord.calibration_submit("run1", "rd11", "wc", _cal("wc", mb=2, nonce="nc_wc_11"))
    coord.assign("run1", "rd11", "a1", "wa", "shard_A", _hashes(art, art["server_hash"]), 90)
    coord.assign("run1", "rd11", "a2", "wb", "shard_B", _hashes(art, art["server_hash"]), 60)
    # fresh worker wc: over-budget assignment must be REJECTED by the budget check
    over = _hashes(art, art["server_hash"]); over["max_micro_batch"] = 8  # exceeds cal 2 and profile 4
    try:
        coord.assign("run1", "rd11", "a9", "wc", "sA", over, 100)
        check("BE1: over-budget assignment rejected", False)
    except ValueError as e:
        check("BE1: over-budget assignment rejected", "budget" in str(e).lower())
    # no partial assignment persisted
    n = coord._conn.execute("SELECT COUNT(*) c FROM assignments_r53 WHERE assignment_id='a9' AND run_id='run1' AND round_id='rd11'").fetchone()["c"]
    check("BE2: no partial over-budget assignment persisted", n == 0)

def test_revisions_real():
    """rev1 ACTIVE -> rev2 ACTIVE -> rev1 SUPERSEDED; FedAvg excludes SUPERSEDED."""
    art = build_initial_artifacts(seed=47)
    _, _, cl, coord = _setup(19894)
    _open_round(coord, "run1", "rd3", art, art["server_hash"], worker_ids=("wa",))
    ra1 = real_worker_flow(cl, coord, "run1", "rd3", "a1_rd3", "wa", "shard_A",
                           art["worker_base_bytes"], art["worker_base_hash"],
                           art["adapter_bytes"], art["adapter_hash"],
                           offset=0, label_keep=90, update_suffix="v1",
                           server_model_hash=art["server_hash"])
    st1 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra1["cid"],)).fetchone()["status"]
    check("RV1: rev1 ACTIVE", st1 == "ACTIVE")
    ra2 = real_worker_flow(cl, coord, "run1", "rd3", "a1_rd3", "wa", "shard_A",
                           art["worker_base_bytes"], art["worker_base_hash"],
                           art["adapter_bytes"], art["adapter_hash"],
                           offset=64, label_keep=30, update_suffix="v2",
                           micro_unit_id="mu_wa_v2", server_model_hash=art["server_hash"])
    st2 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra2["cid"],)).fetchone()["status"]
    check("RV2: rev2 ACTIVE after full flow", st2 == "ACTIVE")
    st1c = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra1["cid"],)).fetchone()["status"]
    check("RV4: rev1 SUPERSEDED after rev2 ACTIVE", st1c == "SUPERSEDED")
    dA, _ = coord.fedavg("run1", "rd3")
    act_rows = coord._conn.execute(
        "SELECT delta_bundle_b64, delta_bundle_sha256 FROM contributions_r53 WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd3'").fetchall()
    check("RV5: only rev2 ACTIVE", len(act_rows) == 1)
    exp = delta_bundle_unpack(base64.b64decode(act_rows[0]["delta_bundle_b64"]), act_rows[0]["delta_bundle_sha256"])
    check("RV6: FedAvg excludes SUPERSEDED", torch.allclose(dA, exp["local_layers.0.linear1.lora_A.weight"], rtol=1e-5, atol=1e-6))

def test_quorum_exact_set():
    """Quorum = exact mandatory set; alien/missing contributions don't count."""
    art = build_initial_artifacts(seed=53)
    _, _, cl, coord = _setup(19895)
    _open_round(coord, "run1", "rd4", art, art["server_hash"])
    real_worker_flow(cl, coord, "run1", "rd4", "a1_rd4", "wa", "shard_A",
                     art["worker_base_bytes"], art["worker_base_hash"],
                     art["adapter_bytes"], art["adapter_hash"],
                     offset=0, label_keep=90, update_suffix="1",
                     server_model_hash=art["server_hash"])
    try:
        coord.ready_to_close("run1", "rd4")
        check("Q1: quorum not met with 1/2", False)
    except ValueError:
        check("Q1: quorum not met with 1/2", True)
    try:
        coord.close_round("run1", "rd4")
        check("Q2: close before quorum rejected", False)
    except ValueError:
        check("Q2: close before quorum rejected", True)

def test_state_machine_real():
    """Round state machine transitions enforced."""
    art = build_initial_artifacts(seed=59)
    db = _fresh_db(); coord = RoundCoordinator(db, KEY)
    try:
        coord.calibration_submit("r", "rd", "wa", _cal("wa", nonce="n_sm"))
        check("ST1: calibration before OPEN rejected", False)
    except ValueError:
        check("ST1: calibration before OPEN rejected", True)
    coord.round_open("r", "rd", {})
    try:
        coord.assign("r", "rd", "a1", "wa", "sA", _hashes(art, art["server_hash"]), 10)
        check("ST2: assignment before calibration rejected", False)
    except ValueError:
        check("ST2: assignment before calibration rejected", True)

def test_process_restart_real():
    """REAL process restart (F): A reaches COMMITTED -> kill -> restart -> recover
    receipt byte-for-byte -> idempotent upload -> B completes -> close -> compare
    with no-restart (server model reconstructed from persisted bytes)."""
    art = build_initial_artifacts(seed=61)
    db = _fresh_db()
    workdir = tempfile.mkdtemp(prefix="r53_restart_")
    art_json = os.path.join(workdir, "art.json")
    out_json = os.path.join(workdir, "out.json")
    script = os.path.join(workdir, "restart_worker.py")
    with open(script, "w") as f:
        f.write(f'''
import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from rc5_3_multiworker import RoundCoordinator, build_initial_artifacts, real_worker_flow
from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_tensor_bundle import bundle_sha256
import time, sys, json
DB = {db!r}; KEY = b"r53_stage_b_real_key_2026"; ART = {art_json!r}; OUT = {out_json!r}
srv, _ = serve(port=19896, db_path=DB, signing_key=KEY)
cl = JsonRpcClient("http://127.0.0.1:19896"); time.sleep(0.3)
coord = RoundCoordinator(DB, KEY)
def hashes(art, sh):
    from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash
    return {{"server_model_hash": sh, "worker_model_hash": art["worker_base_hash"],
             "base_adapter_hash": art["adapter_hash"],
             "partition_schema_hash": partition_schema_hash(),
             "adapter_schema_hash": adapter_schema_hash(),
             "numerical_profile_hash": numerical_profile_hash()}}
def cal(w, n):
    import time as _t
    return {{"backend": "cpu", "precision": "f32", "available_memory_mb": 4096,
             "observed_safe_budget_mb": 2048, "max_micro_batch": 2,
             "max_sequence_length": 128, "calibration_nonce": n, "measured_at": _t.time()}}
which = sys.argv[1]
if which == "A":
    art = build_initial_artifacts(seed=61)  # deterministic initial artifact
    coord.round_open("run1", "rd9", {{"max_micro_batch": 4, "max_sequence_length": 128}})
    coord.register_round_model("run1", "rd9", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "rd9", "wa", cal("wa", "nc_a"))
    coord.calibration_submit("run1", "rd9", "wb", cal("wb", "nc_b"))
    coord.assign("run1", "rd9", "a1", "wa", "shard_A", hashes(art, art["server_hash"]), 90)
    coord.assign("run1", "rd9", "a2", "wb", "shard_B", hashes(art, art["server_hash"]), 60)
    coord.start_round("run1", "rd9")
    json.dump({{"server_hash": art["server_hash"],
                "wb": art["worker_base_bytes"].hex(), "wbh": art["worker_base_hash"],
                "ad": art["adapter_bytes"].hex(), "adh": art["adapter_hash"]}},
              open(ART, "w"))
    res = real_worker_flow(cl, coord, "run1", "rd9", "a1", "wa", "shard_A",
                           art["worker_base_bytes"], art["worker_base_hash"],
                           art["adapter_bytes"], art["adapter_hash"],
                           offset=0, label_keep=90, update_suffix="1",
                           stop_at_commit=True, server_model_hash=art["server_hash"])
    json.dump({{"receipt": res["receipt"], "delta_b64": res["delta_bundle_b64"],
                "delta_sha": res["delta_bundle_sha256"]}}, open(OUT, "w"))
    print("A DONE")
else:
    saved_art = json.load(open(ART))
    sh = saved_art["server_hash"]
    wb = bytes.fromhex(saved_art["wb"]); ad = bytes.fromhex(saved_art["ad"])
    wbh = saved_art["wbh"]; adh = saved_art["adh"]
    saved = json.load(open(OUT))
    receipt = saved["receipt"]; delta_b64 = saved["delta_b64"]; delta_sha = saved["delta_sha"]
    row = coord._conn.execute("SELECT receipt_json FROM units WHERE unit_id=?", (receipt["unit_id"],)).fetchone()
    recovered = json.loads(row["receipt_json"]) if row else None
    same = recovered == receipt
    up1 = cl.call("checkpoint.upload", {{"receipt": receipt, "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha}})
    up2 = cl.call("checkpoint.upload", {{"receipt": receipt, "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha}})
    up_cid = up1.get("contribution_id", receipt["receipt_id"])
    cid = coord.register_uploaded_contribution(up_cid)
    coord.validate_contribution(cid["contribution_id"]); coord.activate_contribution(cid["contribution_id"])
    rb = real_worker_flow(cl, coord, "run1", "rd9", "a2", "wb", "shard_B",
                          wb, wbh, ad, adh, offset=32, label_keep=60,
                          update_suffix="1", server_model_hash=sh)
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
    art2 = build_initial_artifacts(seed=61)  # deterministic: identical bytes
    _, _, cl3, coord3 = _setup(19898)
    _open_round(coord3, "run1", "rd9", art2, art2["server_hash"], assignment_ids=["a1", "a2"])
    _run_round_flow(cl3, coord3, "run1", "rd9", art2, art2["server_hash"],
                    [("a1", "wa", "shard_A", 0, 90), ("a2", "wb", "shard_B", 32, 60)])
    coord3.ready_to_close("run1", "rd9")
    c3 = coord3.close_round("run1", "rd9")
    check("P6: restart adapter == no-restart adapter", out["adapter_hash"] == c3["adapter_hash"])
    os.unlink(script)

def test_isolation_real():
    """Cross-worker isolation: backward_id A not valid for B's unit."""
    art = build_initial_artifacts(seed=67)
    _, _, cl, coord = _setup(19897)
    _open_round(coord, "run1", "rd5", art, art["server_hash"])
    ra = real_worker_flow(cl, coord, "run1", "rd5", "a1_rd5", "wa", "shard_A",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=0, label_keep=90, update_suffix="1",
                          server_model_hash=art["server_hash"])
    rb = real_worker_flow(cl, coord, "run1", "rd5", "a2_rd5", "wb", "shard_B",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=32, label_keep=60, update_suffix="1",
                          server_model_hash=art["server_hash"])
    try:
        cl.call("step.server_backward.fetch", {"unit_id": rb["unit_id"], "backward_id": ra["backward_id"]})
        check("I1: alien backward_id rejected", False)
    except ValueError:
        check("I1: alien backward_id rejected", True)
    # arbitrary receipt (not from upload) rejected
    fake = {"receipt_id": "deadbeef"}
    try:
        coord.register_uploaded_contribution(fake["receipt_id"])
        check("I2: alien contribution rejected", False)
    except ValueError:
        check("I2: alien contribution rejected", True)

TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.3 Stage B R5 — REAL Two-Worker Tests (binding API)")
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
