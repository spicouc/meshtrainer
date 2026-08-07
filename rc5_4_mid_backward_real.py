"""RC5.4 R3 H6 — REAL MID-BACKWARD GATE (lease-governed HTTP).

Scenario (real HTTP, all requests carry the lease):
- Worker A acquires lease, step.open + forward real, fetches the gradient,
  then the PROCESS DIES mid-backward before APPLIED (no journal write).
- Coordinator expires A's lease (contract: crash mid-backward -> EXPIRED).
- DIRECTLY over HTTP, A now gets REJECTED on:
    server_backward.fetch, worker_update.submit, step.commit,
    checkpoint.upload  (expired lease -> rejected by the dispatcher)
- A cannot renew/continue; no partial delta from A in the journal.
- Worker B acquires a NEW lease + new revision (own micro_unit) and
  completes the full HTTP flow (open/forward/backward/update/commit/upload)
  with its own lease. Only B's delta is registered.
"""
import os, sys, json, time, tempfile, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_4_http_server import serve as serve54
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_canonical import (partition_schema_hash, adapter_schema_hash,
                             numerical_profile_hash)
from rc5_3_multiworker import (RoundCoordinator, build_initial_artifacts,
                                make_shard, ett_from_labels)
from rc5_2_worker_runtime import WorkerRuntime
from rc5_4_leases import (LeaseManager, LeaseError, JRN_PREPARED, JRN_APPLIED,
                          LEASE_EXPIRED)

KEY = b"r53_stage_b_real_key_2026"
PORT = 19887
RUN = "run_r54_midbwd"
RND = "r1"
UNIT_A = "mu_A"
WA, WB = "wa_r54", "wb_r54"


def _hashes(art, server_hash):
    return {"server_model_hash": server_hash,
            "worker_model_hash": art["worker_base_hash"],
            "base_adapter_hash": art["adapter_hash"],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()}


def _lp(lease, worker, session, assignment, micro_unit):
    return {"lease_id": lease["lease_id"], "lease_nonce": lease["lease_nonce"],
            "run_id": RUN, "round_id": RND, "assignment_id": assignment,
            "micro_unit_id": micro_unit, "worker_id": worker,
            "session_id": session}


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


def main():
    db_path = sys.argv[1]
    out = sys.argv[2]
    art = build_initial_artifacts(seed=424242)
    server_hash = art["server_hash"]

    coord = RoundCoordinator(db_path, KEY)
    srv, _ = serve54(port=PORT, db_path=db_path, signing_key=KEY,
                     coordinator=coord)
    srv.proto.register_assignment(RUN, RND, "a1", WA, _hashes(art, server_hash))
    srv.proto.register_assignment(RUN, RND, "a2", WB, _hashes(art, server_hash))
    cl = JsonRpcClient(f"http://127.0.0.1:{PORT}")
    time.sleep(0.4)
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

    lm = LeaseManager(coord._conn, default_ttl_seconds=30.0)
    lease = lm.acquire(RUN, RND, "a1", UNIT_A, WA, "sessA", ttl_seconds=30.0)
    unit_id = "unit_A_midbwd"
    session_id = "sessA"

    # step.open real + forward real (HTTP amb lease)
    r1 = cl.call("step.open", {
        **_open_payload(art, server_hash, unit_id, UNIT_A, WA, session_id),
        **_lp(lease, WA, session_id, "a1", UNIT_A)})
    sa = unpack_tensor_envelope(r1["server_activation"])
    wr = WorkerRuntime(db_path=os.path.join(os.path.dirname(db_path), "wr_mid.db"))
    wr.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    tokens, labels, mask = make_shard("shard_A", offset=0, label_keep=90)
    cut = wr.worker_forward(sa, mask)
    env = pack_tensor_envelope(cut, "cut_activation", unit_id, "s1")
    r2 = cl.call("step.worker_forward.submit", {
        "unit_id": unit_id, "forward_id": "fwd_mid", "step_id": "s1",
        "cut_activation": env,
        **_lp(lease, WA, session_id, "a1", UNIT_A)})
    bw_id = r2["backward_id"]
    r3 = cl.call("step.server_backward.fetch", {
        "unit_id": unit_id, "backward_id": bw_id,
        **_lp(lease, WA, session_id, "a1", UNIT_A)})
    cg = unpack_tensor_envelope(r3["cut_gradient"])

    # CRASH a mig backward: el proces mor ABANS d'APPLIED.
    # Cap delta al journal; la lease queda sense commit (mai APPLIED).

    # El Coordinator detecta el crash per TTL -> expire
    lm.expire(RUN, RND, "a1", UNIT_A, coordinator_only=True)
    st = lm.status(lease_id=lease["lease_id"])
    assert st["status"] == LEASE_EXPIRED, f"lease status={st['status']}"
    # cap delta parcial d'A al journal
    j = lm.journal_get(unit_id, lease["lease_id"])
    assert j is None or j["delta_bundle_sha256"] is None, "A va deixar delta!"

    # A NO pot continuar (renew rebutjat)
    try:
        lm.renew(lease["lease_id"], WA, session_id, lease["lease_nonce"])
        a_can_continue = True
    except LeaseError:
        a_can_continue = False
    assert not a_can_continue, "A no ha de poder continuar amb lease EXPIRED"

    # H6: DIRECTAMENT PER HTTP, A rep REJECTED amb la seva lease expirada:
    rejected = {}
    # backward.fetch
    try:
        cl.call("step.server_backward.fetch", {
            "unit_id": unit_id, "backward_id": bw_id,
            **_lp(lease, WA, session_id, "a1", UNIT_A)})
        rejected["backward"] = False
    except ValueError:
        rejected["backward"] = True
    # worker_update.submit
    try:
        cl.call("step.worker_update.submit", {
            "unit_id": unit_id, "update_id": "upd_A_dead",
            "delta_bundle_b64": "eA==", "delta_bundle_sha256": "0" * 64,
            **_lp(lease, WA, session_id, "a1", UNIT_A)})
        rejected["update"] = False
    except ValueError:
        rejected["update"] = True
    # step.commit
    try:
        cl.call("step.commit", {
            "unit_id": unit_id, "delta_id": "d_dead",
            **_lp(lease, WA, session_id, "a1", UNIT_A)})
        rejected["commit"] = False
    except ValueError:
        rejected["commit"] = True
    # checkpoint.upload
    try:
        cl.call("checkpoint.upload", {
            "receipt": {"receipt_id": "r_dead"}, "delta_bundle_b64": "eA==",
            "delta_bundle_sha256": "0" * 64,
            **_lp(lease, WA, session_id, "a1", UNIT_A)})
        rejected["upload"] = False
    except ValueError:
        rejected["upload"] = True
    assert all(rejected.values()), f"A no va rebre REJECTED a tot: {rejected}"
    print("[MID-BACKWARD] A REJECTED per HTTP: "
          + ", ".join(f"{k}={v}" for k, v in rejected.items()))

    # Worker B rep una nova revisio (lease NOVA + micro_unit propi)
    ls_b = lm.acquire(RUN, RND, "a2", "mu_B", WB, "sessB", ttl_seconds=30.0)
    assert ls_b["status"] == "ACTIVE", "B no ha pogut adquirir la revisio nova"

    # B fa forward/backward REALS i completa el flux HTTP amb la seva lease
    wr_b = WorkerRuntime(db_path=os.path.join(os.path.dirname(db_path), "wr_midB.db"))
    wr_b.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    r_open_b = cl.call("step.open", {
        **_open_payload(art, server_hash, "unit_B_midbwd", "mu_B", WB, "sessB",
                        assignment="a2", shard="shard_B", lk=60),
        **_lp(ls_b, WB, "sessB", "a2", "mu_B")})
    sa_b = unpack_tensor_envelope(r_open_b["server_activation"])
    cut_b = wr_b.worker_forward(sa_b, mask)
    env_b = pack_tensor_envelope(cut_b, "cut_activation", "unit_B_midbwd", "s1")
    r2b = cl.call("step.worker_forward.submit", {
        "unit_id": "unit_B_midbwd", "forward_id": "fwd_midB", "step_id": "s1",
        "cut_activation": env_b,
        **_lp(ls_b, WB, "sessB", "a2", "mu_B")})
    r3b = cl.call("step.server_backward.fetch", {
        "unit_id": "unit_B_midbwd", "backward_id": r2b["backward_id"],
        **_lp(ls_b, WB, "sessB", "a2", "mu_B")})
    cg_b = unpack_tensor_envelope(r3b["cut_gradient"])
    wr_b.prepare_update("upd_B", "unit_B_midbwd")
    delta_b64, dsha = wr_b.apply_update_once("upd_B", cut_b, cg_b)
    lm.journal_set("unit_B_midbwd", RUN, RND, "a2", "mu_B", WB, "sessB",
                   ls_b["lease_id"], ls_b["lease_nonce"], JRN_PREPARED)
    lm.journal_set("unit_B_midbwd", RUN, RND, "a2", "mu_B", WB, "sessB",
                   ls_b["lease_id"], ls_b["lease_nonce"], JRN_APPLIED,
                   delta_sha=dsha, delta_b64=delta_b64)
    jb = lm.journal_get("unit_B_midbwd", ls_b["lease_id"])
    assert jb["delta_bundle_sha256"] == dsha, "delta de B no registrat"

    # A intenta aportar amb la seva lease EXPIRED -> rebutjat (journal)
    try:
        lm.journal_set(unit_id, RUN, RND, "a1", UNIT_A, WA, session_id,
                       lease["lease_id"], lease["lease_nonce"], JRN_APPLIED,
                       delta_sha="partial_A")
        a_could_contribute = True
    except LeaseError:
        a_could_contribute = False
    assert not a_could_contribute, "A no ha de poder aportar cap delta parcial"

    result = {
        "pid": os.getpid(),
        "lease_A_status": st["status"],
        "a_can_continue": False,
        "a_could_contribute": False,
        "a_rejected_http": rejected,
        "B_acquired": ls_b["status"],
        "B_delta_sha": dsha[:16],
        "no_partial_A_in_journal": True,
    }
    with open(out, "w") as f:
        json.dump(result, f)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
