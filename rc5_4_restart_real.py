"""RC5.4 R3 H5 — REAL PIPELINE RESTART GATE (lease-governed HTTP).

Two real OS processes over the same SQLite DB, ALL HTTP requests carry the
lease (lease_id, lease_nonce, run_id, round_id, assignment_id, micro_unit_id,
worker_id, session_id) and go through the lease-gated dispatcher
(rc5_4_http_server.ProtocolHandler54 / serve54):

  Phase A: start RoundCoordinator + lease-gated HTTP server, Worker A
           acquires a lease, HTTP step.open/forward/backward WITH lease,
           APPLIED journal (delta + delta_b64 persisted), then the PROCESS
           ACTUALLY EXITS (real crash) without commit.
  Phase B: new process (different PID) opens the SAME DB, reads the APPLIED
           delta DIRECTLY from the journal (no optimizer.step() to recover
           it), continues submit/commit/upload with the SAME lease if still
           valid (or reassigns per contract if expired), Worker B completes
           the other assignment, round closes with RC5.3 FedAvg, and the
           final adapter is compared against a no-crash run.
           A SEPARATE recomputation is allowed as oracle but never counted
           as the recovery itself.

Prohibited oracles: synthetic torch.randn, string-built SHA, invented
adapter hash. All artifacts/deltas come from the real pipeline.
"""
import os, sys, json, time, hashlib, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_4_http_server import serve as serve54
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_canonical import (partition_schema_hash, adapter_schema_hash,
                             numerical_profile_hash)
from rc5_3_multiworker import (RoundCoordinator, build_initial_artifacts,
                                make_shard, ett_from_labels, oracle_fedavg_ett)
from rc5_2_worker_runtime import WorkerRuntime
from rc5_4_leases import (LeaseManager, LeaseError, JRN_PREPARED, JRN_APPLIED,
                          JRN_SUBMITTED, JRN_COMMITTED, LEASE_EXPIRED)

KEY = b"r53_stage_b_real_key_2026"
PORT = 21100 + (os.getpid() % 400)  # port dinàmic alt: mai col·lideix amb 198xx del RC5.3
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


def _lp(lease, worker, session, assignment, micro_unit, run=RUN, rnd=RND):
    """Lease parameters appended to every HTTP request (R3)."""
    return {"lease_id": lease["lease_id"], "lease_nonce": lease["lease_nonce"],
            "run_id": run, "round_id": rnd, "assignment_id": assignment,
            "micro_unit_id": micro_unit, "worker_id": worker,
            "session_id": session}


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


def _open_payload(art, server_hash, unit_id, micro_unit, worker, session,
                  assignment="a1", shard="shard_A", lk=90):
    return {
        "protocol_version": "1.2.0-rc5.2", "unit_id": unit_id,
        "run_id": RUN, "round_id": RND, "assignment_id": assignment,
        "micro_unit_id": micro_unit, "worker_id": worker, "session_id": session,
        "shard_id": shard, "shard_offset": 0, "label_keep": lk,
        "server_model_hash": server_hash,
        "worker_model_hash": art["worker_base_hash"],
        "base_adapter_hash": art["adapter_hash"],
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash(),
    }


def phase_a(db_path, art, server_hash, log):
    """Process A: lease-governed HTTP flow until APPLIED, then REAL crash."""
    coord = RoundCoordinator(db_path, KEY)
    _prepare_round(coord, art, server_hash)
    srv, _ = serve54(port=PORT, db_path=db_path, signing_key=KEY,
                     coordinator=coord)
    # register assignments in the real HTTP server's own registry
    srv.proto.register_assignment(RUN, RND, "a1", WA, _hashes(art, server_hash))
    srv.proto.register_assignment(RUN, RND, "a2", WB, _hashes(art, server_hash))
    cl = JsonRpcClient(f"http://127.0.0.1:{PORT}")
    time.sleep(0.4)

    lm = LeaseManager(coord._conn, default_ttl_seconds=120.0)
    lease = lm.acquire(RUN, RND, "a1", UNIT_A, WA, "sessA", ttl_seconds=120.0)
    session_id = "sessA"
    unit_id = "unit_A_real"
    tokens, labels, mask = make_shard("shard_A", offset=0, label_keep=90)
    ett = ett_from_labels(labels)

    # HTTP step.open WITH lease
    p = _open_payload(art, server_hash, unit_id, UNIT_A, WA, session_id)
    p.update(_lp(lease, WA, session_id, "a1", UNIT_A))
    r1 = cl.call("step.open", p)
    sa = unpack_tensor_envelope(r1["server_activation"])

    wr = WorkerRuntime(db_path=os.path.join(
        os.path.dirname(db_path), f"wr_A_{os.getpid()}.db"))
    wr.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    cut = wr.worker_forward(sa, mask)
    env = pack_tensor_envelope(cut, "cut_activation", unit_id, "s1")

    # HTTP forward WITH lease
    r2 = cl.call("step.worker_forward.submit", {
        "unit_id": unit_id, "forward_id": "fwd_A", "step_id": "s1",
        "cut_activation": env,
        **_lp(lease, WA, session_id, "a1", UNIT_A)})
    bw_id = r2["backward_id"]

    # HTTP backward WITH lease
    r3 = cl.call("step.server_backward.fetch", {
        "unit_id": unit_id, "backward_id": bw_id,
        **_lp(lease, WA, session_id, "a1", UNIT_A)})
    cg = unpack_tensor_envelope(r3["cut_gradient"])
    cg_env = pack_tensor_envelope(cg, "cut_gradient", unit_id, "s1")

    # real optimizer step -> REAL delta (the recovery source of truth)
    wr.prepare_update("upd_A", unit_id)
    wr.apply_update_once("upd_A", cut, cg)
    delta_b64, delta_sha = wr.get_prepared_bundle("upd_A")

    # journal APPLIED with the REAL delta bytes (recoverable without optimizer)
    lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, session_id,
                   lease["lease_id"], lease["lease_nonce"], JRN_PREPARED)
    lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, session_id,
                   lease["lease_id"], lease["lease_nonce"], JRN_APPLIED,
                   delta_sha=delta_sha, delta_b64=delta_b64)

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
    # REAL crash: process exits without commit, without release
    os._exit(0)


def phase_b(db_path, art, server_hash, log, log_nocrash):
    """Process B (different PID): recover delta from journal (no optimizer),
    continue submit/commit/upload with the same lease or reassign, close."""
    coord = RoundCoordinator(db_path, KEY)
    srv, _ = serve54(port=PORT, db_path=db_path, signing_key=KEY,
                     coordinator=coord)
    srv.proto.register_assignment(RUN, RND, "a1", WA, _hashes(art, server_hash))
    srv.proto.register_assignment(RUN, RND, "a2", WB, _hashes(art, server_hash))
    cl = JsonRpcClient(f"http://127.0.0.1:{PORT}")
    time.sleep(0.4)
    with open(log) as f:
        st = json.load(f)
    unit_id = st["unit_id"]
    lm = LeaseManager(coord._conn, default_ttl_seconds=120.0)

    # RECOVERY: delta read DIRECTLY from the journal (no optimizer.step())
    j = lm.journal_get(unit_id, st["lease_id"])
    assert j is not None and j["state"] == JRN_APPLIED, f"journal={j}"
    assert j["delta_bundle_sha256"] == st["delta_sha"], "delta SHA mismatch"
    assert j["delta_bundle_b64"] == st["delta_b64"], "delta bytes mismatch"
    recovered_b64 = j["delta_bundle_b64"]  # the recovery payload
    recovered_sha = j["delta_bundle_sha256"]
    print(f"[PHASE B] pid={os.getpid()} recovered delta from journal "
          f"({recovered_sha[:16]}) — no optimizer executed")

    # ORACLE (separate, never counted as recovery): recompute determinism.
    # The oracle acquires its OWN lease on its OWN micro_unit so it never
    # collides with the recovered unit's idempotency key, and is never
    # counted as the recovery path.
    from rc5_2_worker_runtime import WorkerRuntime
    wr2 = WorkerRuntime(db_path=os.path.join(
        os.path.dirname(db_path), f"wr_oracle_{os.getpid()}.db"))
    wr2.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    lease_oracle = lm.acquire(RUN, RND, "a1", "mu_oracle", WA, "sess_oracle",
                              ttl_seconds=120.0)
    sa2 = unpack_tensor_envelope(cl.call("step.open", {
        **_open_payload(art, server_hash, "unit_oracle", "mu_oracle", WA, "sess_oracle"),
        **_lp(lease_oracle, WA, "sess_oracle", "a1", "mu_oracle")})["server_activation"])
    tokens, labels, mask = make_shard("shard_A", offset=0, label_keep=90)
    cut2 = wr2.worker_forward(sa2, mask)
    cg2 = unpack_tensor_envelope(st["cut_gradient_env"])
    wr2.prepare_update("upd_oracle", "unit_oracle")
    wr2.apply_update_once("upd_oracle", cut2, cg2)
    _b64o, sha_o = wr2.get_prepared_bundle("upd_oracle")
    assert sha_o == recovered_sha, (
        f"oracle delta != journal delta: {sha_o[:12]} vs {recovered_sha[:12]}")
    print("[PHASE B] oracle recomputation matches journal delta (separate)")

    # continue with the SAME lease if still valid, else reassign
    st_l = lm.status(lease_id=st["lease_id"])
    if st_l["status"] in ("ACTIVE", "RENEWED"):
        lease = {"lease_id": st["lease_id"], "lease_nonce": st["lease_nonce"]}
        reassigned = False
    else:
        lease = lm.acquire(RUN, RND, "a1", UNIT_A, WB, "sessB", ttl_seconds=120.0)
        reassigned = True
    print(f"[PHASE B] lease {'mateixa' if not reassigned else 'reassignada a B'}")

    # SUBMITTED -> COMMITTED -> checkpoint via HTTP (WITH lease)
    upd = "upd_A_final"
    lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, "sessA",
                   st["lease_id"], st["lease_nonce"], JRN_SUBMITTED,
                   delta_sha=recovered_sha, update_id=upd)
    r4 = cl.call("step.worker_update.submit", {
        "unit_id": unit_id, "update_id": upd,
        "delta_bundle_b64": recovered_b64,
        "delta_bundle_sha256": recovered_sha,
        **_lp(lease, WA if not reassigned else WB,
              "sessA" if not reassigned else "sessB", "a1", UNIT_A)})
    r5 = cl.call("step.commit", {"unit_id": unit_id,
                                 "delta_id": r4.get("delta_id", ""),
                                 **_lp(lease, WA if not reassigned else WB,
                                       "sessA" if not reassigned else "sessB",
                                       "a1", UNIT_A)})
    receipt = r5["receipt"]
    lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, "sessA",
                   st["lease_id"], st["lease_nonce"], JRN_COMMITTED,
                   delta_sha=recovered_sha, update_id=upd,
                   receipt=json.dumps(receipt, sort_keys=True))
    r6 = cl.call("checkpoint.upload", {
        "receipt": receipt,
        "delta_bundle_b64": recovered_b64,
        "delta_bundle_sha256": recovered_sha,
        **_lp(lease, WA if not reassigned else WB,
              "sessA" if not reassigned else "sessB", "a1", UNIT_A)})
    up_cid = r6.get("contribution_id", receipt["receipt_id"])
    cid = srv.proto.recovery.register_uploaded_contribution(
        up_cid, **_lp(lease, WA if not reassigned else WB,
                      "sessA" if not reassigned else "sessB", "a1", UNIT_A))
    coord.validate_contribution(cid["contribution_id"])
    coord.activate_contribution(cid["contribution_id"])
    print(f"[PHASE B] worker A recuperat cid={cid['contribution_id']}")

    # Worker B completes the OTHER assignment (no crash)
    art_b = {"worker_base_bytes": art["worker_base_bytes"],
             "worker_base_hash": art["worker_base_hash"],
             "adapter_bytes": art["adapter_bytes"],
             "adapter_hash": art["adapter_hash"]}
    flow = lease_worker_flow(
        cl, coord, lm, RUN, RND, "a2", WB, "shard_B",
        art_b["worker_base_bytes"], art_b["worker_base_hash"],
        art_b["adapter_bytes"], art_b["adapter_hash"],
        "sessB", UNIT_B, server_hash, label_keep=60, update_suffix="pbB")
    cid_b = flow["cid"]
    print(f"[PHASE B] worker B cid={cid_b}")

    # FedAvg RC5.3 + adapter comparison vs no-crash (REAL coordinator FedAvg)
    coord.fedavg(RUN, RND)
    coord.ready_to_close(RUN, RND)
    coord.close_round(RUN, RND)
    _adapter_b64, final_adapter = coord.get_round_adapter(RUN, RND)
    with open(log_nocrash) as f:
        nc = json.load(f)
    assert final_adapter == nc["final_adapter_hash"], (
        f"adapter crash != no-crash: {final_adapter[:12]} vs {nc['final_adapter_hash'][:12]}")
    print(f"[PHASE B] adapter OK {final_adapter[:16]}")
    result = {"pid_a": st["pid"], "pid_b": os.getpid(),
              "adapter_hash": final_adapter,
              "delta_sha": recovered_sha, "reassigned": reassigned}
    print(json.dumps(result))


def st_lease(lm, st):
    """Fetch the lease row by id (for oracle step.open lease params)."""
    row = lm._conn.execute("SELECT * FROM leases_r54 WHERE lease_id=?",
                           (st["lease_id"],)).fetchone()
    if row is None:
        raise LeaseError(f"Lease {st['lease_id']} not found")
    return dict(row)


def lease_worker_flow(cl, coord, lm, run_id, round_id, assignment_id, worker_id,
                      shard_id, base_bytes, base_hash, ad_bytes, ad_hash,
                      session_id, micro_unit_id, server_model_hash,
                      offset=0, label_keep=None, update_suffix="", db_dir=None):
    """REAL worker flow where EVERY HTTP request carries the lease (R3).
    Mirrors real_worker_flow but injects lease params into each call and
    registers the contribution through the lease-gated path."""
    import uuid
    unit_id = f"unit_{worker_id}_{uuid.uuid4().hex[:6]}"
    db_path = os.path.join(db_dir or tempfile.gettempdir(),
                           f"journal_{worker_id}_{uuid.uuid4().hex[:8]}.db")
    tokens, labels, mask = make_shard(shard_id, offset=offset, label_keep=label_keep)
    ett = ett_from_labels(labels)
    lease = lm.acquire(run_id, round_id, assignment_id, micro_unit_id,
                       worker_id, session_id, ttl_seconds=120.0)

    r1 = cl.call("step.open", {
        "protocol_version": "1.2.0-rc5.2", "unit_id": unit_id,
        "run_id": run_id, "round_id": round_id, "assignment_id": assignment_id,
        "micro_unit_id": micro_unit_id, "worker_id": worker_id,
        "session_id": session_id,
        "shard_id": shard_id, "shard_offset": offset,
        "label_keep": label_keep if label_keep is not None else -1,
        "server_model_hash": server_model_hash,
        "worker_model_hash": base_hash, "base_adapter_hash": ad_hash,
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash(),
        **_lp(lease, worker_id, session_id, assignment_id, micro_unit_id,
              run=run_id, rnd=round_id)})
    sa = unpack_tensor_envelope(r1["server_activation"])

    wr = WorkerRuntime(db_path=db_path)
    wr.load_from_artifacts(base_bytes, base_adapter_bytes=ad_bytes)
    cut = wr.worker_forward(sa, mask)
    env = pack_tensor_envelope(cut, "cut_activation", unit_id, "s1")

    r2 = cl.call("step.worker_forward.submit", {
        "unit_id": unit_id, "forward_id": f"fwd_{worker_id}_{update_suffix}",
        "step_id": "s1", "cut_activation": env,
        **_lp(lease, worker_id, session_id, assignment_id, micro_unit_id,
              run=run_id, rnd=round_id)})
    bw_id = r2["backward_id"]
    r3 = cl.call("step.server_backward.fetch", {
        "unit_id": unit_id, "backward_id": bw_id,
        **_lp(lease, worker_id, session_id, assignment_id, micro_unit_id,
              run=run_id, rnd=round_id)})
    cg = unpack_tensor_envelope(r3["cut_gradient"])

    wr.prepare_update(f"upd_{worker_id}_{update_suffix}", unit_id)
    delta_b64, delta_sha = wr.apply_update_once(
        f"upd_{worker_id}_{update_suffix}", cut, cg)
    r4 = cl.call("step.worker_update.submit", {
        "unit_id": unit_id, "update_id": f"upd_{worker_id}_{update_suffix}",
        "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha,
        **_lp(lease, worker_id, session_id, assignment_id, micro_unit_id,
              run=run_id, rnd=round_id)})
    r5 = cl.call("step.commit", {
        "unit_id": unit_id, "delta_id": r4.get("delta_id", ""),
        **_lp(lease, worker_id, session_id, assignment_id, micro_unit_id,
              run=run_id, rnd=round_id)})
    receipt = r5["receipt"]
    r6 = cl.call("checkpoint.upload", {
        "receipt": receipt,
        "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha,
        **_lp(lease, worker_id, session_id, assignment_id, micro_unit_id,
              run=run_id, rnd=round_id)})
    up_cid = r6.get("contribution_id", receipt["receipt_id"])
    cid = coord.register_uploaded_contribution(up_cid)
    coord.validate_contribution(cid["contribution_id"])
    coord.activate_contribution(cid["contribution_id"])
    return {"worker_id": worker_id, "unit_id": unit_id,
            "assignment_id": assignment_id, "shard_id": shard_id, "ett": ett,
            "cid": cid["contribution_id"], "backward_id": bw_id,
            "receipt_id": receipt["receipt_id"], "delta_sha": delta_sha,
            "wmh": base_hash, "baeh": ad_hash}


def no_crash(db_path, art, server_hash, log):
    """Full round WITHOUT crash (adapter oracle)."""
    coord = RoundCoordinator(db_path, KEY)
    _prepare_round(coord, art, server_hash)
    srv, _ = serve54(port=PORT, db_path=db_path, signing_key=KEY,
                     coordinator=coord)
    srv.proto.register_assignment(RUN, RND, "a1", WA, _hashes(art, server_hash))
    srv.proto.register_assignment(RUN, RND, "a2", WB, _hashes(art, server_hash))
    cl = JsonRpcClient(f"http://127.0.0.1:{PORT}")
    time.sleep(0.4)
    lm = LeaseManager(coord._conn, default_ttl_seconds=120.0)
    art_b = {"worker_base_bytes": art["worker_base_bytes"],
             "worker_base_hash": art["worker_base_hash"],
             "adapter_bytes": art["adapter_bytes"],
             "adapter_hash": art["adapter_hash"]}
    tokens, labels, mask = make_shard("shard_A", offset=0, label_keep=90)
    ett = ett_from_labels(labels)
    flow_a = lease_worker_flow(
        cl, coord, lm, RUN, RND, "a1", WA, "shard_A",
        art["worker_base_bytes"], art["worker_base_hash"],
        art["adapter_bytes"], art["adapter_hash"],
        "sessA", UNIT_A, server_hash, label_keep=90, update_suffix="ncA")
    flow_b = lease_worker_flow(
        cl, coord, lm, RUN, RND, "a2", WB, "shard_B",
        art_b["worker_base_bytes"], art_b["worker_base_hash"],
        art_b["adapter_bytes"], art_b["adapter_hash"],
        "sessB", UNIT_B, server_hash, label_keep=60, update_suffix="ncB")
    fed = coord.fedavg(RUN, RND)
    coord.ready_to_close(RUN, RND)
    coord.close_round(RUN, RND)
    _adapter_b64, adapter_hash = coord.get_round_adapter(RUN, RND)
    result = {"delta_sha": None, "final_adapter_hash": adapter_hash,
              "cid_a": flow_a["cid"], "cid_b": flow_b["cid"]}
    with open(log, "w") as f:
        json.dump(result, f)
    print(json.dumps(result))


def main():
    mode = sys.argv[1]
    db_path = sys.argv[2]
    log = sys.argv[3]
    art = build_initial_artifacts(seed=424242)
    server_hash = art["server_hash"]
    if mode == "no-crash":
        no_crash(db_path, art, server_hash, log)
    elif mode == "phase_a":
        phase_a(db_path, art, server_hash, log)
    elif mode == "phase_b":
        phase_b(db_path, art, server_hash, log, sys.argv[4])
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
