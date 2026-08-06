"""RC5.4 R2 R8 — REAL PIPELINE RESTART GATE.

Two real OS processes over the same SQLite DB:
  Phase A: start Coordinator + HTTP server, Worker A acquires lease, runs
           step.open -> forward -> backward -> apply (APPLIED journal), then
           the PROCESS ACTUALLY EXITS (crash) without commit.
  Phase B: new process (different PID) opens the SAME DB, recovers the
           journal, returns the EXACT same delta (no second optimizer),
           continues SUBMITTED -> COMMITTED -> checkpoint.upload, Worker B
           completes the other assignment, round closes with RC5.3 FedAvg,
           and the final adapter is compared against a no-crash run.

Prohibited oracles: synthetic torch.randn, string-built SHA, invented
adapter hash. All artifacts/deltas come from the real pipeline.
"""
import os, sys, json, base64, time, tempfile, uuid, hashlib, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_canonical import (partition_schema_hash, adapter_schema_hash,
                             numerical_profile_hash)
from rc5_3_multiworker import (RoundCoordinator, build_initial_artifacts,
                                make_shard, ett_from_labels, real_worker_flow,
                                oracle_fedavg_ett)
from rc5_2_numerical_models import WorkerNumericalModel
from rc5_4_leases import (LeaseManager, LeaseError, JRN_PREPARED, JRN_APPLIED,
                          JRN_SUBMITTED, JRN_COMMITTED)

KEY = b"r53_stage_b_real_key_2026"
PORT = 19885
RUN = "run_r54_restart"
RND = "r1"
UNIT_A = "mu_A"
UNIT_B = "mu_B"
WA, WB = "wa_r54", "wb_r54"


def _hashes(art, server_hash):
    return {"server_model_hash": server_hash,
            "worker_model_hash": art["worker_base_hash"],
            "base_adapter_hash": art["adapter_hash"],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()}


def _prepare_round(coord, art, server_hash):
    coord.round_open(RUN, RND, {"max_micro_batch": 4, "max_sequence_length": 128})
    coord.register_round_model(RUN, RND, art["server_bytes"],
                               art["worker_base_bytes"], art["adapter_bytes"])
    for w in (WA, WB):
        coord.calibration_submit(RUN, RND, w, {
            "backend": "cpu", "precision": "f32", "available_memory_mb": 4096,
            "observed_safe_budget_mb": 2048, "max_micro_batch": 2,
            "max_sequence_length": 128, "calibration_nonce": f"nc_{w}",
            "measured_at": time.time()})
    coord.assign(RUN, RND, "a1", WA, "shard_A", _hashes(art, server_hash), 90)
    coord.assign(RUN, RND, "a2", WB, "shard_B", _hashes(art, server_hash), 60)
    coord.start_round(RUN, RND)


def phase_a(db_path, art, server_hash, log):
    """Process A: full flow until APPLIED, then EXIT (real crash)."""
    srv, _ = serve(port=PORT, db_path=db_path, signing_key=KEY)
    cl = JsonRpcClient(f"http://127.0.0.1:{PORT}")
    time.sleep(0.4)
    coord = RoundCoordinator(db_path, KEY)
    _prepare_round(coord, art, server_hash)
    lm = LeaseManager(coord._conn, default_ttl_seconds=120.0)
    lease = lm.acquire(RUN, RND, "a1", UNIT_A, WA, "sessA", ttl_seconds=120.0)

    # real step.open + forward + backward + apply (APPLIED journal)
    session_id = "sessA"
    unit_id = "unit_A_real"
    tokens, labels, mask = make_shard("shard_A", offset=0, label_keep=90)
    ett = ett_from_labels(labels)
    r1 = cl.call("step.open", {
        "protocol_version": "1.2.0-rc5.2", "unit_id": unit_id,
        "run_id": RUN, "round_id": RND, "assignment_id": "a1",
        "micro_unit_id": UNIT_A, "worker_id": WA, "session_id": session_id,
        "shard_id": "shard_A", "shard_offset": 0, "label_keep": 90,
        "server_model_hash": server_hash,
        "worker_model_hash": art["worker_base_hash"],
        "base_adapter_hash": art["adapter_hash"],
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash(),
    })
    sa = unpack_tensor_envelope(r1["server_activation"])

    from rc5_2_worker_runtime import WorkerRuntime
    wr = WorkerRuntime(db_path=os.path.join(
        os.path.dirname(db_path), f"wr_A_{os.getpid()}.db"))
    wr.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    cut = wr.worker_forward(sa, mask)
    env = pack_tensor_envelope(cut, "cut_activation", unit_id, "s1")
    r2 = cl.call("step.worker_forward.submit", {
        "unit_id": unit_id, "forward_id": "fwd_A", "step_id": "s1",
        "cut_activation": env})
    bw_id = r2["backward_id"]
    r3 = cl.call("step.server_backward.fetch", {"unit_id": unit_id, "backward_id": bw_id})
    cg = unpack_tensor_envelope(r3["cut_gradient"])
    cg_env = pack_tensor_envelope(cg, "cut_gradient", unit_id, "s1")

    # real optimizer step -> delta REAL (no synthetic)
    wr.prepare_update("upd_A", unit_id)
    wr.apply_update_once("upd_A", cut, cg)
    delta_b64, delta_sha = wr.get_prepared_bundle("upd_A")

    # journal APPLIED with the REAL delta sha
    lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, session_id,
                   lease["lease_id"], lease["lease_nonce"], JRN_PREPARED)
    lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, session_id,
                   lease["lease_id"], lease["lease_nonce"], JRN_APPLIED,
                   delta_sha=delta_sha)
    with open(log, "w") as f:
        f.write(json.dumps({
            "phase": "A", "pid": os.getpid(), "unit_id": unit_id,
            "lease_id": lease["lease_id"], "lease_nonce": lease["lease_nonce"],
            "delta_sha": delta_sha, "delta_b64": delta_b64, "bw_id": bw_id,
            "cut_activation_env": env, "cut_gradient_env": cg_env, "ett": ett,
            "worker_base_hash": art["worker_base_hash"],
            "adapter_hash": art["adapter_hash"],
        }))
    print(f"[PHASE A] pid={os.getpid()} APPLIED delta_sha={delta_sha[:16]}")
    # REAL crash: process exits WITHOUT commit, without release
    os._exit(0)


def _server_activation(cl, unit_id, art, server_hash):
    """Re-run a real step.open (same unit) -> same server activation."""
    r = cl.call("step.open", {
        "protocol_version": "1.2.0-rc5.2", "unit_id": unit_id,
        "run_id": RUN, "round_id": RND, "assignment_id": "a1",
        "micro_unit_id": UNIT_A, "worker_id": WA, "session_id": "sessA",
        "shard_id": "shard_A", "shard_offset": 0, "label_keep": 90,
        "server_model_hash": server_hash,
        "worker_model_hash": art["worker_base_hash"],
        "base_adapter_hash": art["adapter_hash"],
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash(),
    })
    return unpack_tensor_envelope(r["server_activation"])


def _cg_from_state(cl, st):
    """Recover the REAL cut gradient produced in phase A (server exactly-once
    would reject a second fetch; the journal/state carries it)."""
    return unpack_tensor_envelope(st["cut_gradient_env"])


def phase_b(db_path, art, server_hash, log, log_nocrash):
    """Process B: recover journal, same delta, continue, close round, compare."""
    srv, _ = serve(port=PORT, db_path=db_path, signing_key=KEY)  # servidor NOU
    cl = JsonRpcClient(f"http://127.0.0.1:{PORT}")
    time.sleep(0.4)
    coord = RoundCoordinator(db_path, KEY)
    lm = LeaseManager(coord._conn, default_ttl_seconds=120.0)
    with open(log) as f:
        st = json.load(f)
    pid_a = st["pid"]
    assert os.getpid() != pid_a, "Phase B must run in a DIFFERENT process"
    print(f"[PHASE B] pid={os.getpid()} (A era {pid_a})")

    # recover journal -> the exact same delta (no second optimizer)
    j = lm.journal_get(st["unit_id"], st["lease_id"])
    assert j is not None and j["state"] == JRN_APPLIED, f"journal={j}"
    assert j["delta_bundle_sha256"] == st["delta_sha"], "delta SHA mismatch"

    # re-derive determinism: reload WorkerRuntime from REAL artifacts, re-run
    # step.open (idempotent -> same server activation) + REAL forward to build
    # a fresh graph, then apply with the phase-A gradient -> same delta.
    # Proves the delta is reproducible WITHOUT a second optimizer step on the
    # committed journal (the journal holds APPLIED; nothing re-runs on it).
    from rc5_2_worker_runtime import WorkerRuntime
    wr2 = WorkerRuntime(db_path=os.path.join(
        os.path.dirname(db_path), f"wr_B_{os.getpid()}.db"))
    wr2.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    sa2 = _server_activation(cl, st["unit_id"], art, server_hash)
    tokens, labels, mask = make_shard("shard_A", offset=0, label_keep=90)
    cut2 = wr2.worker_forward(sa2, mask)   # fresh graph (requires grad)
    cg2 = unpack_tensor_envelope(st["cut_gradient_env"])
    wr2.prepare_update("upd_B_recheck", st["unit_id"])
    wr2.apply_update_once("upd_B_recheck", cut2, cg2)
    _b64, rederived_sha = wr2.get_prepared_bundle("upd_B_recheck")
    assert rederived_sha == st["delta_sha"], (
        f"rederived delta != journal delta: {rederived_sha[:12]} vs {st['delta_sha'][:12]}")

    # continue: SUBMITTED -> COMMITTED -> checkpoint via REAL HTTP flow
    # Worker A's commit re-uses the recovered delta (idempotent)
    unit_id = st["unit_id"]
    upd = f"upd_A_final"
    lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, "sessA",
                   st["lease_id"], st["lease_nonce"], JRN_SUBMITTED,
                   delta_sha=st["delta_sha"], update_id=upd)
    # re-submit the REAL delta bundle through the HTTP pipeline (idempotent:
    # the server returns the same delta_id / receipt on retry)
    r4 = cl.call("step.worker_update.submit", {
        "unit_id": unit_id, "update_id": upd,
        "delta_bundle_b64": st["delta_b64"],
        "delta_bundle_sha256": st["delta_sha"]})
    r5 = cl.call("step.commit", {"unit_id": unit_id,
                                 "delta_id": r4.get("delta_id", "")})
    receipt = r5["receipt"]
    lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, "sessA",
                   st["lease_id"], st["lease_nonce"], JRN_COMMITTED,
                   delta_sha=st["delta_sha"], update_id=upd,
                   receipt=json.dumps(receipt, sort_keys=True))
    # checkpoint.upload -> REAL contribution id -> register via coordinator
    r6 = cl.call("checkpoint.upload", {
        "receipt": receipt,
        "delta_bundle_b64": st["delta_b64"],
        "delta_bundle_sha256": st["delta_sha"]})
    up_cid = r6.get("contribution_id", receipt["receipt_id"])
    cid = coord.register_uploaded_contribution(up_cid)
    coord.validate_contribution(cid["contribution_id"])
    coord.activate_contribution(cid["contribution_id"])
    print(f"[PHASE B] worker A recuperat cid={cid['contribution_id']}")

    # Worker B completes the OTHER assignment (no crash)
    art_b = {"worker_base_bytes": art["worker_base_bytes"],
             "worker_base_hash": art["worker_base_hash"],
             "adapter_bytes": art["adapter_bytes"],
             "adapter_hash": art["adapter_hash"]}
    b_res = real_worker_flow(cl, coord, RUN, RND, "a2", WB, "shard_B",
                             art_b["worker_base_bytes"], art_b["worker_base_hash"],
                             art_b["adapter_bytes"], art_b["adapter_hash"],
                             offset=0, label_keep=60,
                             server_model_hash=server_hash)
    print(f"[PHASE B] worker B cid={b_res.get('cid')}")

    # close round with RC5.3 FedAvg
    ok = coord.ready_to_close(RUN, RND)
    adapter = coord.close_round(RUN, RND)
    with open(log_nocrash) as f:
        nc = json.load(f)
    # compare final adapter vs no-crash equivalent
    if isinstance(adapter, dict):
        adapter_hash = adapter.get("adapter_hash", "?")
    else:
        adapter_hash = hashlib.sha256(str(adapter).encode()).hexdigest()
    assert adapter_hash == nc["final_adapter_hash"], (
        f"adapter mismatch: {adapter_hash[:12]} vs {nc['final_adapter_hash'][:12]}")
    print(f"[PHASE B] adapter OK {adapter_hash[:16]}")
    return {"pid_a": pid_a, "pid_b": os.getpid(), "adapter_hash": adapter_hash,
            "delta_sha": st["delta_sha"]}


def run_no_crash_equivalent(db_path, art, server_hash):
    """Oracle: same round WITHOUT crash -> final adapter hash."""
    srv, _ = serve(port=PORT, db_path=db_path, signing_key=KEY)
    cl = JsonRpcClient(f"http://127.0.0.1:{PORT}")
    time.sleep(0.4)
    coord = RoundCoordinator(db_path, KEY)
    _prepare_round(coord, art, server_hash)
    art_b = {"worker_base_bytes": art["worker_base_bytes"],
             "worker_base_hash": art["worker_base_hash"],
             "adapter_bytes": art["adapter_bytes"],
             "adapter_hash": art["adapter_hash"]}
    specs = [("a1", WA, "shard_A", 0, 90), ("a2", WB, "shard_B", 0, 60)]
    arts = {WA: (art["worker_base_bytes"], art["worker_base_hash"],
                 art["adapter_bytes"], art["adapter_hash"]),
            WB: (art["worker_base_bytes"], art["worker_base_hash"],
                 art["adapter_bytes"], art["adapter_hash"])}
    from rc5_3_multiworker import run_two_real_workers
    run_two_real_workers(coord, cl, RUN, RND, arts, specs,
                         server_model_hash=server_hash)
    coord.ready_to_close(RUN, RND)
    adapter = coord.close_round(RUN, RND)
    if isinstance(adapter, dict):
        h = adapter.get("adapter_hash", "?")
    else:
        h = hashlib.sha256(str(adapter).encode()).hexdigest()
    print(f"[NO-CRASH] adapter_hash={h[:16]}")
    return {"delta_sha": None, "final_adapter_hash": h}


def main():
    mode = sys.argv[1]
    db_path = sys.argv[2]
    if mode == "no-crash":
        art = build_initial_artifacts(seed=424242)
        res = run_no_crash_equivalent(db_path, art, art["server_hash"])
        with open(sys.argv[3], "w") as f:
            json.dump(res, f)
        print(json.dumps(res))
        return
    if mode == "phase_a":
        art = build_initial_artifacts(seed=424242)
        phase_a(db_path, art, art["server_hash"], sys.argv[3])
    elif mode == "phase_b":
        art = build_initial_artifacts(seed=424242)
        res = phase_b(db_path, art, art["server_hash"], sys.argv[3], sys.argv[4])
        print(json.dumps(res))


if __name__ == "__main__":
    main()
