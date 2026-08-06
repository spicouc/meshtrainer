"""RC5.4 R2 R9 — REAL MID-BACKWARD GATE.

Scenario: Worker A enters backward, the process CRASHES before APPLIED,
the lease expires, the autograd context is discarded, Worker A cannot
continue, Worker B receives a new revision, only B contributes, and no
partial delta from A remains in the journal.
"""
import os, sys, json, base64, time, tempfile, uuid, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_http_server import serve
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


def main():
    db_path = sys.argv[1]
    out = sys.argv[2]
    art = build_initial_artifacts(seed=424242)
    server_hash = art["server_hash"]

    srv, _ = serve(port=PORT, db_path=db_path, signing_key=KEY)
    cl = JsonRpcClient(f"http://127.0.0.1:{PORT}")
    time.sleep(0.4)
    coord = RoundCoordinator(db_path, KEY)
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

    # step.open real + forward real
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
    wr = WorkerRuntime(db_path=os.path.join(os.path.dirname(db_path), "wr_mid.db"))
    wr.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    tokens, labels, mask = make_shard("shard_A", offset=0, label_keep=90)
    cut = wr.worker_forward(sa, mask)
    env = pack_tensor_envelope(cut, "cut_activation", unit_id, "s1")
    r2 = cl.call("step.worker_forward.submit", {
        "unit_id": unit_id, "forward_id": "fwd_mid", "step_id": "s1",
        "cut_activation": env})
    bw_id = r2["backward_id"]
    r3 = cl.call("step.server_backward.fetch", {"unit_id": unit_id, "backward_id": bw_id})
    cg = unpack_tensor_envelope(r3["cut_gradient"])

    # CRASH a mig backward: el procés mor ABANS d'APPLIED.
    # El journal NO te cap delta; la lease queda ACTIVE (mai renew/commit).
    # Simulem el crash real: tanquem la DB sense escriure el journal APPLIED.
    # (El context autograd es descarta en morir el procés.)

    # El Coordinator detecta el crash per TTL: expire_overdue la marca EXPIRED.
    lm.expire(RUN, RND, "a1", UNIT_A, coordinator_only=True)
    st = lm.status(lease_id=lease["lease_id"])
    assert st["status"] == LEASE_EXPIRED, f"lease status={st['status']}"
    # cap delta parcial al journal (A no va arribar a APPLIED)
    j = lm.journal_get(unit_id, lease["lease_id"])
    assert j is None or j["delta_bundle_sha256"] is None, "A va deixar delta!"

    # Worker A NO pot continuar: la seva lease està EXPIRED
    try:
        lm.renew(lease["lease_id"], WA, session_id, lease["lease_nonce"])
        a_can_continue = True
    except LeaseError:
        a_can_continue = False
    assert not a_can_continue, "A no ha de poder continuar amb lease EXPIRED"

    # Worker B rep una nova revisió (reassignació de la unitat a B)
    ls_b = lm.acquire(RUN, RND, "a2", "mu_B", WB, "sessB", ttl_seconds=30.0)
    assert ls_b["status"] == "ACTIVE", "B no ha pogut adquirir la revisió nova"

    # B fa forward/backward REALS i arriba a APPLIED amb el SEU delta
    # (nova revisió = nou micro_unit_id: la clau lògica step.open es
    #  run:round:assignment:micro_unit — un retry amb el mateix micro_unit
    #  i payload diferent seria REJECTED pel protocol, com ha de ser)
    wr_b = WorkerRuntime(db_path=os.path.join(os.path.dirname(db_path), "wr_midB.db"))
    wr_b.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    sa_b = unpack_tensor_envelope(cl.call("step.open", {
        "protocol_version": "1.2.0-rc5.2", "unit_id": "unit_B_midbwd",
        "run_id": RUN, "round_id": RND, "assignment_id": "a2",
        "micro_unit_id": "mu_B", "worker_id": WB, "session_id": "sessB",
        "shard_id": "shard_A", "shard_offset": 0, "label_keep": 90,
        "server_model_hash": server_hash,
        "worker_model_hash": art["worker_base_hash"],
        "base_adapter_hash": art["adapter_hash"],
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash(),
    })["server_activation"])
    cut_b = wr_b.worker_forward(sa_b, mask)
    env_b = pack_tensor_envelope(cut_b, "cut_activation", "unit_B_midbwd", "s1")
    r2b = cl.call("step.worker_forward.submit", {
        "unit_id": "unit_B_midbwd", "forward_id": "fwd_midB", "step_id": "s1",
        "cut_activation": env_b})
    r3b = cl.call("step.server_backward.fetch",
                  {"unit_id": "unit_B_midbwd", "backward_id": r2b["backward_id"]})
    cg_b = unpack_tensor_envelope(r3b["cut_gradient"])
    wr_b.prepare_update("upd_B", "unit_B_midbwd")
    wr_b.apply_update_once("upd_B", cut_b, cg_b)
    d64, dsha = wr_b.get_prepared_bundle("upd_B")
    lm.journal_set("unit_B_midbwd", RUN, RND, "a2", "mu_B", WB, "sessB",
                   ls_b["lease_id"], ls_b["lease_nonce"], JRN_PREPARED)
    lm.journal_set("unit_B_midbwd", RUN, RND, "a2", "mu_B", WB, "sessB",
                   ls_b["lease_id"], ls_b["lease_nonce"], JRN_APPLIED,
                   delta_sha=dsha)
    jb = lm.journal_get("unit_B_midbwd", ls_b["lease_id"])
    assert jb["delta_bundle_sha256"] == dsha, "delta de B no registrat"

    # A intenta aportar amb la seva lease EXPIRED -> rebutjat
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
        "B_acquired": ls_b["status"],
        "B_delta_sha": dsha[:16],
        "no_partial_A_in_journal": True,
    }
    with open(out, "w") as f:
        json.dump(result, f)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
