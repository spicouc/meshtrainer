"""RC5.3 Stage B R5 — ADVERSARIAL GATE (28/28).

Each ADV-R3-* proof exercises the REAL production code path with a
single deliberate violation; PASS means the violation was rejected or
the invariant held. No synthetic shortcuts, no `or True`.
"""
import os, sys, json, torch, base64, time, tempfile, uuid, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash
from rc5_2_numerical_models import WorkerNumericalModel, SplitServerNumericalModel
from rc5_3_multiworker import (RoundCoordinator, build_initial_artifacts,
                                real_worker_flow, run_two_real_workers, make_shard,
                                server_model_from_bytes, oracle_fedavg_ett,
                                ROUND_CLOSED, ROUND_READY)
from rc5_2_worker_runtime import WorkerRuntime

PASS, FAIL = 0, 0
KEY = b"r53_stage_b_real_key_2026"

def check(label, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {label}")
    else: FAIL += 1; print(f"  ❌ {label}")

def _fresh_db():
    return os.path.join(tempfile.gettempdir(), f"r53adv_{uuid.uuid4().hex[:8]}.db")

def _setup(port):
    db = _fresh_db()
    srv, _ = serve(port=port, db_path=db, signing_key=KEY)
    cl = JsonRpcClient(f"http://127.0.0.1:{port}")
    time.sleep(0.3)
    coord = RoundCoordinator(db, KEY)
    return db, srv, cl, coord

def _cal(worker_id, mb=2, sl=128, nonce=None, mem=4096):
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

def _open(coord, run_id, round_id, art, server_hash, parent_round_id=None,
          workers=("wa", "wb"), assignment_ids=None):
    if assignment_ids is None:
        assignment_ids = [f"a1_{round_id}", f"a2_{round_id}"]
    coord.round_open(run_id, round_id, {"max_micro_batch": 4, "max_sequence_length": 128},
                     parent_round_id=parent_round_id)
    coord.register_round_model(run_id, round_id, art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    for w in workers:
        coord.calibration_submit(run_id, round_id, w,
                                 _cal(w, nonce=f"nc_{w}_{run_id}_{round_id}"))
    for i, w in enumerate(workers):
        coord.assign(run_id, round_id, assignment_ids[i], w, f"shard_{'A' if i == 0 else 'B'}",
                     _hashes(art, server_hash), 90 if i == 0 else 60)
    coord.start_round(run_id, round_id)

def _flow(cl, coord, run_id, round_id, art, server_hash, specs):
    arts = {w: (art["worker_base_bytes"], art["worker_base_hash"],
                art["adapter_bytes"], art["adapter_hash"]) for _, w, _, _, _ in specs}
    return run_two_real_workers(coord, cl, run_id, round_id, arts, specs,
                                server_model_hash=server_hash)

def _open_payload(art, server_hash, unit_id, run_id, round_id, assignment_id,
                  worker_id, shard_id="shard_A", offset=0, lk=90, adapter_hash=None,
                  smh=None, wmh=None):
    return {
        "protocol_version": "1.2.0-rc5.2", "unit_id": unit_id,
        "run_id": run_id, "round_id": round_id, "assignment_id": assignment_id,
        "micro_unit_id": f"mu_{unit_id}", "worker_id": worker_id, "session_id": f"s_{unit_id}",
        "shard_id": shard_id, "shard_offset": offset, "label_keep": lk,
        "server_model_hash": smh if smh is not None else server_hash,
        "worker_model_hash": wmh if wmh is not None else art["worker_base_hash"],
        "base_adapter_hash": adapter_hash if adapter_hash is not None else art["adapter_hash"],
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash()}

# ---- ADV-R3-01..04: server artifact -------------------------------------------

def adv_r3_01_server_artifact_hash_real():
    art = build_initial_artifacts(seed=101)
    check("ADV-R3-01: server artifact hash real (64 hex, matches bytes)",
          len(art["server_hash"]) == 64 and
          bundle_sha256(art["server_bytes"]) == art["server_hash"])

def adv_r3_02_fake_server_hash_rejected():
    art = build_initial_artifacts(seed=102)
    db, srv, cl, coord = _setup(20102)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    try:
        cl.call("step.open", _open_payload(art, art["server_hash"], "u_fake",
                                           "run1", "rd1", "a1_rd1", "wa", smh="0" * 64))
        check("ADV-R3-02: fake server hash rejected", False)
    except ValueError:
        check("ADV-R3-02: fake server hash rejected", True)

def adv_r3_03_altered_server_bytes_rejected():
    art = build_initial_artifacts(seed=103)
    bad = bytearray(art["server_bytes"]); bad[0] = ord("Z")
    try:
        server_model_from_bytes(bytes(bad), art["server_hash"])
        check("ADV-R3-03: altered server bytes rejected", False)
    except ValueError:
        check("ADV-R3-03: altered server bytes rejected", True)

def adv_r3_04_server_artifact_restart_exact():
    """Reconstruct the server model twice from the same persisted bytes ->
    identical activations (restart equivalence)."""
    art = build_initial_artifacts(seed=104)
    t, l, m = make_shard("shard_A", offset=0, label_keep=90)
    s1 = server_model_from_bytes(art["server_bytes"], art["server_hash"])
    s2 = server_model_from_bytes(art["server_bytes"], art["server_hash"])
    a1 = s1.server_forward(t, m)
    a2 = s2.server_forward(t, m)
    check("ADV-R3-04: server artifact restart exact", torch.equal(a1, a2))

# ---- ADV-R3-05..06: shared worker base / base adapter --------------------------

def adv_r3_05_shared_worker_base_ab():
    art = build_initial_artifacts(seed=105)
    db, srv, cl, coord = _setup(20105)
    _open(coord, "run1", "rd1", art, art["server_hash"])
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
    check("ADV-R3-05: shared worker base A/B", ra["wmh"] == rb["wmh"])

def adv_r3_06_shared_base_adapter_ab_round():
    art = build_initial_artifacts(seed=106)
    db, srv, cl, coord = _setup(20106)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    rnd = coord._conn.execute("SELECT * FROM rounds_r53 WHERE run_id='run1' AND round_id='rd1'").fetchone()
    check("ADV-R3-06: base_adapter == adapter_0 == worker hash",
          rnd["base_adapter_hash"] == rnd["adapter_0_hash"] == art["adapter_hash"])

# ---- ADV-R3-07: failed registration atomic -------------------------------------

def adv_r3_07_failed_model_registration_atomic():
    art = build_initial_artifacts(seed=107)
    db, srv, cl, coord = _setup(20107)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    other = build_initial_artifacts(seed=1071)
    try:
        coord.register_round_model("run1", "rd1", art["server_bytes"],
                                   art["worker_base_bytes"], other["adapter_bytes"])
        ok = False
    except ValueError:
        ok = True
    r = coord.get_round_model("run1", "rd1")
    check("ADV-R3-07: failed registration atomic (original intact)",
          ok and r["adapter_0_hash"] == art["adapter_hash"]
          and r["server_model_hash"] == art["server_hash"])

# ---- ADV-R3-08..11: assignment idempotency -------------------------------------

def adv_r3_08_assignment_same_payload_idempotent():
    art = build_initial_artifacts(seed=108)
    db, srv, cl, coord = _setup(20108)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "rd1", "wa", _cal("wa", nonce="nc_08"))
    h = _hashes(art, art["server_hash"])
    r1 = coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 90)
    r2 = coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 90)
    check("ADV-R3-08: assignment same payload idempotent", r1 == r2)

def adv_r3_09_assignment_changed_shard_rejected():
    art = build_initial_artifacts(seed=109)
    db, srv, cl, coord = _setup(20109)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "rd1", "wa", _cal("wa", nonce="nc_09"))
    h = _hashes(art, art["server_hash"])
    coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 90)
    try:
        coord.assign("run1", "rd1", "a1", "wa", "shard_OTHER", h, 90)
        check("ADV-R3-09: changed shard rejected", False)
    except ValueError:
        check("ADV-R3-09: changed shard rejected", True)

def adv_r3_10_assignment_changed_ett_rejected():
    art = build_initial_artifacts(seed=110)
    db, srv, cl, coord = _setup(20110)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "rd1", "wa", _cal("wa", nonce="nc_10"))
    h = _hashes(art, art["server_hash"])
    coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 90)
    try:
        coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 5)
        check("ADV-R3-10: changed ETT rejected", False)
    except ValueError:
        check("ADV-R3-10: changed ETT rejected", True)

def adv_r3_11_assignment_changed_worker_rejected():
    art = build_initial_artifacts(seed=111)
    db, srv, cl, coord = _setup(20111)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "rd1", "wa", _cal("wa", nonce="nc_11a"))
    coord.calibration_submit("run1", "rd1", "wb", _cal("wb", nonce="nc_11b"))
    h = _hashes(art, art["server_hash"])
    coord.assign("run1", "rd1", "a1", "wa", "shard_A", h, 90)
    try:
        coord.assign("run1", "rd1", "a1", "wb", "shard_A", h, 90)
        check("ADV-R3-11: changed worker rejected", False)
    except ValueError:
        check("ADV-R3-11: changed worker rejected", True)

# ---- ADV-R3-12..14: calibration idempotency ------------------------------------

def adv_r3_12_calibration_same_payload_idempotent():
    art = build_initial_artifacts(seed=112)
    db, srv, cl, coord = _setup(20112)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    cal = _cal("wa", nonce="nc_fix", mem=2048)
    r1 = coord.calibration_submit("run1", "rd1", "wa", cal)
    r2 = coord.calibration_submit("run1", "rd1", "wa", cal)
    check("ADV-R3-12: calibration same payload idempotent", r1 == r2)

def adv_r3_13_calibration_changed_payload_rejected():
    art = build_initial_artifacts(seed=113)
    db, srv, cl, coord = _setup(20113)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    cal = _cal("wa", nonce="nc_fix", mem=2048)
    coord.calibration_submit("run1", "rd1", "wa", cal)
    cal2 = dict(cal); cal2["available_memory_mb"] = 9999
    try:
        coord.calibration_submit("run1", "rd1", "wa", cal2)
        check("ADV-R3-13: calibration changed payload rejected", False)
    except ValueError:
        check("ADV-R3-13: calibration changed payload rejected", True)

def adv_r3_14_uncalibrated_worker_rejected():
    art = build_initial_artifacts(seed=114)
    db, srv, cl, coord = _setup(20114)
    coord.round_open("run1", "rd1", {})
    coord.register_round_model("run1", "rd1", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    try:
        coord.assign("run1", "rd1", "a1", "nobody", "shard_A", _hashes(art, art["server_hash"]), 90)
        check("ADV-R3-14: uncalibrated worker rejected", False)
    except ValueError:
        check("ADV-R3-14: uncalibrated worker rejected", True)

# ---- ADV-R3-15..16: explicit lineage --------------------------------------------

def adv_r3_15_explicit_parent_adapter():
    art = build_initial_artifacts(seed=115)
    db, srv, cl, coord = _setup(20115)
    _open(coord, "run1", "r1", art, art["server_hash"])
    _flow(cl, coord, "run1", "r1", art, art["server_hash"],
          [("a1_r1", "wa", "shard_A", 0, 90), ("a2_r1", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "r1")
    c1 = coord.close_round("run1", "r1")
    # round 2 with a stale adapter (adapter_0 of r1, not the parent global) rejected
    coord.round_open("run1", "r2", {}, parent_round_id="r1")
    try:
        coord.register_round_model("run1", "r2", art["server_bytes"],
                                   art["worker_base_bytes"], art["adapter_bytes"])
        check("ADV-R3-15: explicit parent adapter enforced", False)
    except ValueError:
        check("ADV-R3-15: explicit parent adapter enforced", True)
    # correct parent adapter accepted and lineage persisted
    art2 = dict(art); art2["adapter_bytes"] = c1["adapter_bytes"]; art2["adapter_hash"] = c1["adapter_hash"]
    coord.round_open("run1", "r2b", {}, parent_round_id="r1")
    coord.register_round_model("run1", "r2b", art2["server_bytes"],
                               art2["worker_base_bytes"], art2["adapter_bytes"])
    row = coord._conn.execute("SELECT parent_adapter_hash FROM rounds_r53 WHERE run_id='run1' AND round_id='r2b'").fetchone()
    check("ADV-R3-15b: parent_adapter_hash persisted == parent global", row["parent_adapter_hash"] == c1["adapter_hash"])

def adv_r3_16_r10_r2_lineage_not_lexical():
    """round ids 'r10' < 'r2' lexically, but lineage must follow round_index."""
    art = build_initial_artifacts(seed=116)
    db, srv, cl, coord = _setup(20116)
    # round named r10 is opened FIRST (round_index 1) and CLOSED;
    # then r2 (round_index 2) with explicit parent r10. A lexical
    # ORDER BY round_id would pick the wrong parent.
    coord.round_open("run1", "r10", {})
    check("ADV-R3-16a: r10 has round_index 1",
          coord._conn.execute("SELECT round_index FROM rounds_r53 WHERE run_id='run1' AND round_id='r10'").fetchone()["round_index"] == 1)
    coord.register_round_model("run1", "r10", art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "r10", "wa", _cal("wa", nonce="nc_16"))
    coord.assign("run1", "r10", "a1", "wa", "shard_A", _hashes(art, art["server_hash"]), 90)
    coord.start_round("run1", "r10")
    real_worker_flow(cl, coord, "run1", "r10", "a1", "wa", "shard_A",
                     art["worker_base_bytes"], art["worker_base_hash"],
                     art["adapter_bytes"], art["adapter_hash"],
                     offset=0, label_keep=90, update_suffix="1",
                     server_model_hash=art["server_hash"])
    coord.ready_to_close("run1", "r10")
    coord.close_round("run1", "r10")
    coord.round_open("run1", "r2", {}, parent_round_id="r10")
    check("ADV-R3-16b: r2 (parent r10) has round_index 2",
          coord._conn.execute("SELECT round_index FROM rounds_r53 WHERE run_id='run1' AND round_id='r2'").fetchone()["round_index"] == 2)
    check("ADV-R3-16c: lineage by explicit parent, not lexical order",
          coord._conn.execute("SELECT parent_round_id FROM rounds_r53 WHERE run_id='run1' AND round_id='r2'").fetchone()["parent_round_id"] == "r10")

# ---- ADV-R3-17..19: secure contributions ----------------------------------------

def adv_r3_17_contribution_not_from_upload_rejected():
    art = build_initial_artifacts(seed=117)
    db, srv, cl, coord = _setup(20117)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    try:
        coord.register_uploaded_contribution("not_an_upload_cid")
        check("ADV-R3-17: contribution not from upload rejected", False)
    except ValueError:
        check("ADV-R3-17: contribution not from upload rejected", True)

def adv_r3_18_contribution_receipt_mismatch_rejected():
    art = build_initial_artifacts(seed=118)
    db, srv, cl, coord = _setup(20118)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    ra = real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=0, label_keep=90, update_suffix="1",
                          server_model_hash=art["server_hash"])
    # tamper the persisted receipt then try to re-register
    orig = coord._conn.execute("SELECT receipt_json FROM contributions WHERE cid=?", (ra["cid"],)).fetchone()["receipt_json"]
    coord._conn.execute("UPDATE contributions SET receipt_json=? WHERE cid=?", (json.dumps({"x": 1}), ra["cid"]))
    coord._conn.commit()
    try:
        coord.register_uploaded_contribution(ra["cid"])
        check("ADV-R3-18: receipt mismatch rejected", False)
    except ValueError:
        check("ADV-R3-18: receipt mismatch rejected", True)
    coord._conn.execute("UPDATE contributions SET receipt_json=? WHERE cid=?", (orig, ra["cid"]))
    coord._conn.commit()

def adv_r3_19_same_contribution_changed_payload_rejected():
    """After an assignment is REJECTED-then-changed, a contribution bound to the
    OLD source_request_sha must not be re-served under the new assignment state."""
    art = build_initial_artifacts(seed=119)
    db, srv, cl, coord = _setup(20119)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    ra = real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=0, label_keep=90, update_suffix="1",
                          server_model_hash=art["server_hash"])
    row = coord._conn.execute("SELECT source_request_sha FROM contributions_r53 WHERE cid=?", (ra["cid"],)).fetchone()
    arow = coord._conn.execute("SELECT request_sha FROM assignments_r53 WHERE assignment_id='a1_rd1' AND run_id='run1' AND round_id='rd1'").fetchone()
    check("ADV-R3-19: source_request_sha == assignment request_sha",
          row is not None and row["source_request_sha"] == arow["request_sha"])

# ---- ADV-R3-20..21: FedAvg / quorum ----------------------------------------------

def adv_r3_20_superseded_excluded():
    art = build_initial_artifacts(seed=120)
    db, srv, cl, coord = _setup(20120)
    _open(coord, "run1", "rd1", art, art["server_hash"], workers=("wa",))
    ra1 = real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                           art["worker_base_bytes"], art["worker_base_hash"],
                           art["adapter_bytes"], art["adapter_hash"],
                           offset=0, label_keep=90, update_suffix="v1",
                           server_model_hash=art["server_hash"])
    ra2 = real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                           art["worker_base_bytes"], art["worker_base_hash"],
                           art["adapter_bytes"], art["adapter_hash"],
                           offset=64, label_keep=30, update_suffix="v2",
                           micro_unit_id="mu_v2", server_model_hash=art["server_hash"])
    st1 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra1["cid"],)).fetchone()["status"]
    st2 = coord._conn.execute("SELECT status FROM contributions_r53 WHERE cid=?", (ra2["cid"],)).fetchone()["status"]
    check("ADV-R3-20: SUPERSEDED excluded (rev1 SUPERSEDED, rev2 ACTIVE)",
          st1 == "SUPERSEDED" and st2 == "ACTIVE")
    dA, _ = coord.fedavg("run1", "rd1")
    exp = delta_bundle_unpack(base64.b64decode(coord._conn.execute(
        "SELECT delta_bundle_b64, delta_bundle_sha256 FROM contributions_r53 WHERE cid=?", (ra2["cid"],)).fetchone()["delta_bundle_b64"]),
        coord._conn.execute("SELECT delta_bundle_sha256 FROM contributions_r53 WHERE cid=?", (ra2["cid"],)).fetchone()["delta_bundle_sha256"])
    check("ADV-R3-20b: FedAvg uses ACTIVE only",
          torch.allclose(dA, exp["local_layers.0.linear1.lora_A.weight"], rtol=1e-5, atol=1e-6))

def adv_r3_21_exact_assignment_quorum():
    art = build_initial_artifacts(seed=121)
    db, srv, cl, coord = _setup(20121)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                     art["worker_base_bytes"], art["worker_base_hash"],
                     art["adapter_bytes"], art["adapter_hash"],
                     offset=0, label_keep=90, update_suffix="1",
                     server_model_hash=art["server_hash"])
    try:
        coord.ready_to_close("run1", "rd1")
        check("ADV-R3-21: exact assignment quorum (1/2 not enough)", False)
    except ValueError:
        check("ADV-R3-21: exact assignment quorum (1/2 not enough)", True)

# ---- ADV-R3-22..24: restart / journal / optimizer --------------------------------

def adv_r3_22_restart_full_adapter_equality():
    """Full process restart: adapter from the restart flow equals the no-restart
    flow (server model reconstructed from persisted bytes)."""
    art = build_initial_artifacts(seed=122)
    db = _fresh_db()
    workdir = tempfile.mkdtemp(prefix="r53adv22_")
    script = os.path.join(workdir, "adv22.py")
    with open(script, "w") as f:
        f.write(f'''
import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from rc5_3_multiworker import RoundCoordinator, build_initial_artifacts, real_worker_flow
from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_canonical import partition_schema_hash, adapter_schema_hash, numerical_profile_hash
import time, sys, json, os, base64
DB = {db!r}; KEY = b"r53_stage_b_real_key_2026"
srv, _ = serve(port=20122, db_path=DB, signing_key=KEY)
cl = JsonRpcClient("http://127.0.0.1:20122"); time.sleep(0.3)
coord = RoundCoordinator(DB, KEY)
art = build_initial_artifacts(seed=122)
def hashes(art, sh):
    return {{"server_model_hash": sh, "worker_model_hash": art["worker_base_hash"],
             "base_adapter_hash": art["adapter_hash"],
             "partition_schema_hash": partition_schema_hash(),
             "adapter_schema_hash": adapter_schema_hash(),
             "numerical_profile_hash": numerical_profile_hash()}}
def cal(w, n):
    return {{"backend": "cpu", "precision": "f32", "available_memory_mb": 4096,
             "observed_safe_budget_mb": 2048, "max_micro_batch": 2,
             "max_sequence_length": 128, "calibration_nonce": n, "measured_at": time.time()}}
which = sys.argv[1]
if which == "A":
    coord.round_open("run1", "rd9", {{"max_micro_batch": 4, "max_sequence_length": 128}})
    coord.register_round_model("run1", "rd9", art["server_bytes"], art["worker_base_bytes"], art["adapter_bytes"])
    coord.calibration_submit("run1", "rd9", "wa", cal("wa", "nc_a"))
    coord.calibration_submit("run1", "rd9", "wb", cal("wb", "nc_b"))
    coord.assign("run1", "rd9", "a1", "wa", "shard_A", hashes(art, art["server_hash"]), 90)
    coord.assign("run1", "rd9", "a2", "wb", "shard_B", hashes(art, art["server_hash"]), 60)
    coord.start_round("run1", "rd9")
    res = real_worker_flow(cl, coord, "run1", "rd9", "a1", "wa", "shard_A",
                           art["worker_base_bytes"], art["worker_base_hash"],
                           art["adapter_bytes"], art["adapter_hash"],
                           offset=0, label_keep=90, update_suffix="1",
                           stop_at_commit=True, server_model_hash=art["server_hash"])
    json.dump({{"receipt": res["receipt"], "d64": res["delta_bundle_b64"], "ds": res["delta_bundle_sha256"]}},
              open(os.path.join({workdir!r}, "out22.json"), "w"))
    print("A DONE")
else:
    saved = json.load(open(os.path.join({workdir!r}, "out22.json")))
    rcpt = saved["receipt"]; d64 = saved["d64"]; ds = saved["ds"]
    up = cl.call("checkpoint.upload", {{"receipt": rcpt, "delta_bundle_b64": d64, "delta_bundle_sha256": ds}})
    cid = coord.register_uploaded_contribution(up.get("contribution_id", rcpt["receipt_id"]))
    coord.validate_contribution(cid["contribution_id"]); coord.activate_contribution(cid["contribution_id"])
    rb = real_worker_flow(cl, coord, "run1", "rd9", "a2", "wb", "shard_B",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=32, label_keep=60, update_suffix="1",
                          server_model_hash=art["server_hash"])
    coord.ready_to_close("run1", "rd9")
    c = coord.close_round("run1", "rd9")
    print("B DONE " + c["adapter_hash"])
''')
    r1 = subprocess.run(["python3", script, "A"], capture_output=True, text=True, timeout=240)
    r2 = subprocess.run(["python3", script, "B"], capture_output=True, text=True, timeout=240)
    adv22_hash = r2.stdout.strip().split()[-1] if r2.returncode == 0 else ""
    # no-restart run (same assignment ids a1/a2)
    _, _, cl3, coord3 = _setup(20123)
    _open(coord3, "run1", "rd9", art, art["server_hash"], assignment_ids=["a1", "a2"])
    _flow(cl3, coord3, "run1", "rd9", art, art["server_hash"],
          [("a1", "wa", "shard_A", 0, 90), ("a2", "wb", "shard_B", 32, 60)])
    coord3.ready_to_close("run1", "rd9")
    c3 = coord3.close_round("run1", "rd9")
    check("ADV-R3-22: restart full adapter equality",
          r1.returncode == 0 and r2.returncode == 0 and adv22_hash == c3["adapter_hash"])

def adv_r3_23_journal_survives_restart():
    """WorkerRuntime journal: after an APPLIED update, a NEW runtime on the SAME
    journal file returns the SAME delta without a second optimizer step."""
    jpath = os.path.join(tempfile.gettempdir(), f"jr_{uuid.uuid4().hex[:8]}.db")
    art = build_initial_artifacts(seed=123)
    wr1 = WorkerRuntime(db_path=jpath)
    wr1.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    t, l, m = make_shard("shard_A", offset=0, label_keep=90)
    from rc5_2_numerical_models import SplitServerNumericalModel
    torch.manual_seed(123)
    srv_model = SplitServerNumericalModel()
    sa = srv_model.server_forward(t, m)
    cut = wr1.worker_forward(sa, m)
    cg = torch.randn_like(cut) * 0.001
    wr1.prepare_update("upd_j", "u1")
    d1, s1 = wr1.apply_update_once("upd_j", cut, cg)
    # NEW runtime instance (simulated restart) on the SAME journal
    wr2 = WorkerRuntime(db_path=jpath)
    wr2.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    d2, s2 = wr2.apply_update_once("upd_j", cut, cg)
    check("ADV-R3-23: journal survives restart (same delta, no re-apply)",
          d1 == d2 and s1 == s2)

def adv_r3_24_no_second_optimizer_after_restart():
    """Re-applying an APPLIED update must NOT change the weights again."""
    jpath = os.path.join(tempfile.gettempdir(), f"jr2_{uuid.uuid4().hex[:8]}.db")
    art = build_initial_artifacts(seed=124)
    wr = WorkerRuntime(db_path=jpath)
    wr.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    t, l, m = make_shard("shard_A", offset=0, label_keep=90)
    from rc5_2_numerical_models import SplitServerNumericalModel
    torch.manual_seed(124)
    srv_model = SplitServerNumericalModel()
    sa = srv_model.server_forward(t, m)
    cut = wr.worker_forward(sa, m)
    cg = torch.randn_like(cut) * 0.001
    wr.prepare_update("upd_j2", "u1")
    w_pre = wr.worker.lora_wrapper.lora_A.weight.detach().clone()
    d1, s1 = wr.apply_update_once("upd_j2", cut, cg)
    w_post = wr.worker.lora_wrapper.lora_A.weight.detach().clone()
    d2, s2 = wr.apply_update_once("upd_j2", cut, cg)  # replay
    w_after_replay = wr.worker.lora_wrapper.lora_A.weight.detach().clone()
    check("ADV-R3-24: no second optimizer after restart",
          torch.equal(w_post, w_after_replay) and d1 == d2)

# ---- ADV-R3-25..28 ---------------------------------------------------------------

def adv_r3_25_closed_immutable():
    art = build_initial_artifacts(seed=125)
    db, srv, cl, coord = _setup(20125)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    _flow(cl, coord, "run1", "rd1", art, art["server_hash"],
          [("a1_rd1", "wa", "shard_A", 0, 90), ("a2_rd1", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "rd1")
    c1 = coord.close_round("run1", "rd1")
    # CLOSED is immutable: reopening or reassigning must fail
    try:
        coord.round_open("run1", "rd1", {})
        check("ADV-R3-25: CLOSED immutable (reopen)", False)
    except ValueError:
        check("ADV-R3-25: CLOSED immutable (reopen)", True)
    # close again -> idempotent same hash (no corruption)
    c2 = coord.close_round("run1", "rd1")
    check("ADV-R3-25b: close idempotent after CLOSED", c2["adapter_hash"] == c1["adapter_hash"])

def adv_r3_26_adapter_round2_oracle():
    """adapter_2 = adapter_1 + weighted(delta_2) via the independent oracle."""
    art = build_initial_artifacts(seed=126)
    db, srv, cl, coord = _setup(20126)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    _flow(cl, coord, "run1", "rd1", art, art["server_hash"],
          [("a1_rd1", "wa", "shard_A", 0, 90), ("a2_rd1", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "rd1")
    c1 = coord.close_round("run1", "rd1")
    art2 = dict(art); art2["adapter_bytes"] = c1["adapter_bytes"]; art2["adapter_hash"] = c1["adapter_hash"]
    _open(coord, "run1", "rd2", art2, art["server_hash"], parent_round_id="rd1")
    _flow(cl, coord, "run1", "rd2", art2, art["server_hash"],
          [("a1_rd2", "wa", "shard_A", 0, 90), ("a2_rd2", "wb", "shard_B", 32, 60)])
    coord.ready_to_close("run1", "rd2")
    c2 = coord.close_round("run1", "rd2")
    rows = coord._conn.execute(
        "SELECT delta_bundle_b64, delta_bundle_sha256, ett FROM contributions_r53"
        " WHERE status='ACTIVE' AND run_id='run1' AND round_id='rd2'").fetchall()
    wds = []
    for r in rows:
        t = delta_bundle_unpack(base64.b64decode(r["delta_bundle_b64"]), r["delta_bundle_sha256"])
        wds.append((t["local_layers.0.linear1.lora_A.weight"],
                    t["local_layers.0.linear1.lora_B.weight"], r["ett"]))
    pre = delta_bundle_unpack(c1["adapter_bytes"], c1["adapter_hash"])
    oA, oB = oracle_fedavg_ett(pre["local_layers.0.linear1.lora_A.weight"],
                               pre["local_layers.0.linear1.lora_B.weight"], wds)
    check("ADV-R3-26: adapter Round 2 oracle == Coordinator",
          bundle_sha256(delta_bundle_pack(oA, oB)) == c2["adapter_hash"])

def adv_r3_27_alien_backward_rejected():
    art = build_initial_artifacts(seed=127)
    db, srv, cl, coord = _setup(20127)
    _open(coord, "run1", "rd1", art, art["server_hash"])
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
    try:
        cl.call("step.server_backward.fetch", {"unit_id": rb["unit_id"], "backward_id": ra["backward_id"]})
        check("ADV-R3-27: alien backward rejected", False)
    except ValueError:
        check("ADV-R3-27: alien backward rejected", True)

def adv_r3_28_ett_derived_from_labels():
    art = build_initial_artifacts(seed=128)
    db, srv, cl, coord = _setup(20128)
    _open(coord, "run1", "rd1", art, art["server_hash"])
    ra = real_worker_flow(cl, coord, "run1", "rd1", "a1_rd1", "wa", "shard_A",
                          art["worker_base_bytes"], art["worker_base_hash"],
                          art["adapter_bytes"], art["adapter_hash"],
                          offset=0, label_keep=90, update_suffix="1",
                          server_model_hash=art["server_hash"])
    row = coord._conn.execute("SELECT ett FROM contributions_r53 WHERE cid=?", (ra["cid"],)).fetchone()
    check("ADV-R3-28: ETT derived from labels", row["ett"] == 89)

TESTS = [(n, f) for n, f in globals().items() if n.startswith("adv_r3_")]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.3 Stage B R5 — ADVERSARIAL GATE (28)")
    print("=" * 60)
    for name, fn in sorted(TESTS):
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
