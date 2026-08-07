"""RC5.4 Stage A R3 — REAL HTTP lease adversarial tests.

All probes go through the REAL HTTP server (rc5_4_http_server.serve) with a
real RoundCoordinator. No _FakePipeline anywhere: every call is a real
JSON-RPC request over HTTP to the lease-gated dispatcher.

Probes:
  H-01 step.open sense lease rejected
  H-02 wrong lease nonce rejected
  H-03 wrong session rejected
  H-04 lease cross-run rejected
  H-05 lease cross-assignment rejected
  H-06 expired lease forward rejected
  H-07 expired lease backward rejected
  H-08 expired lease commit rejected
  H-09 expired lease upload rejected
  H-10 released lease rejected
  H-11 old worker rejected after reassign
  H-12 contribution registration sense lease rejected
"""
import os, sys, json, base64, time, tempfile, uuid, hashlib

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
                          LEASE_RELEASED)

KEY = b"r53_stage_b_real_key_2026"
PORT = 21000 + (os.getpid() % 400)  # port dinàmic alt: mai col·lideix amb 198xx del RC5.3
RUN = "run_r54_http"
RND = "r1"
WA, WB = "wa_r54", "wb_r54"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'OK ' if ok else 'FAIL'} {name} {detail}")


def _hashes(art, server_hash):
    return {"server_model_hash": server_hash,
            "worker_model_hash": art["worker_base_hash"],
            "base_adapter_hash": art["adapter_hash"],
            "partition_schema_hash": partition_schema_hash(),
            "adapter_schema_hash": adapter_schema_hash(),
            "numerical_profile_hash": numerical_profile_hash()}


def _open_payload(art, server_hash, unit_id, micro_unit, worker, session,
                  assignment="a1", shard="shard_A", offset=0, lk=90):
    return {
        "protocol_version": "1.2.0-rc5.2", "unit_id": unit_id,
        "run_id": RUN, "round_id": RND, "assignment_id": assignment,
        "micro_unit_id": micro_unit, "worker_id": worker, "session_id": session,
        "shard_id": shard, "shard_offset": offset, "label_keep": lk,
        "server_model_hash": server_hash,
        "worker_model_hash": art["worker_base_hash"],
        "base_adapter_hash": art["adapter_hash"],
        "partition_schema_hash": partition_schema_hash(),
        "adapter_schema_hash": adapter_schema_hash(),
        "numerical_profile_hash": numerical_profile_hash(),
    }


def _lease_params(lease, worker, session, assignment="a1", micro_unit="mu1",
                  run=RUN, rnd=RND):
    return {"lease_id": lease["lease_id"], "lease_nonce": lease["lease_nonce"],
            "run_id": run, "round_id": rnd, "assignment_id": assignment,
            "micro_unit_id": micro_unit, "worker_id": worker,
            "session_id": session}


def _rejected_missing_lease(fn):
    """Call fn() expecting a missing_lease_params rejection."""
    try:
        fn()
        return False
    except ValueError as e:
        return "missing_lease_params" in str(e)


def _rejected_any(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


def main():
    db = os.path.join(tempfile.gettempdir(), f"http54_{os.getpid()}.db")
    if os.path.exists(db):
        os.remove(db)
    art = build_initial_artifacts(seed=424242)
    server_hash = art["server_hash"]

    # RoundCoordinator PRIMER; el servidor comparteix la seva connexió
    coord = RoundCoordinator(db, KEY)
    srv, _ = serve54(port=PORT, db_path=db, signing_key=KEY, coordinator=coord)
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

    # registra les mateixes assignacions al servidor HTTP real (el ProtocolHandler
    # RC5.2 té un registre propi, independent del RoundCoordinator)
    srv.proto.register_assignment(RUN, RND, "a1", WA, _hashes(art, server_hash))
    srv.proto.register_assignment(RUN, RND, "a2", WB, _hashes(art, server_hash))

    lm = LeaseManager(coord._conn, default_ttl_seconds=30.0)

    # H-01: step.open sense lease -> rejected (missing_lease_params)
    check("H-01 step.open sense lease rejected",
          _rejected_missing_lease(lambda: cl.call(
              "step.open", _open_payload(art, server_hash, "u_h1", "mu_h1", WA, "s1"))))

    # ---- setup: lease real i unitat de bindings (aïllada del forward real) ----
    lease = lm.acquire(RUN, RND, "a1", "mu_bind", WA, "sess_main", ttl_seconds=10.0)
    p = _open_payload(art, server_hash, "u_bind", "mu_bind", WA, "sess_main")
    p.update(_lease_params(lease, WA, "sess_main", micro_unit="mu_bind"))
    r = cl.call("step.open", p)
    sa = unpack_tensor_envelope(r["server_activation"])
    wr = WorkerRuntime(db_path=os.path.join(tempfile.gettempdir(), f"wr_h_{os.getpid()}.db"))
    wr.load_from_artifacts(art["worker_base_bytes"], art["adapter_bytes"])
    tokens, labels, mask = make_shard("shard_A", offset=0, label_keep=90)
    cut = wr.worker_forward(sa, mask)
    env = pack_tensor_envelope(cut, "cut_activation", "u_bind", "s1")
    fwd = {"unit_id": "u_bind", "forward_id": "fwd_h", "step_id": "s1",
           "cut_activation": env}

    # H-02: wrong lease nonce rejected
    p2 = dict(fwd)
    p2.update(_lease_params(lease, WA, "sess_main", micro_unit="mu_bind"))
    p2["lease_nonce"] = "WRONG"
    try:
        cl.call("step.worker_forward.submit", p2)
        check("H-02 wrong lease nonce rejected", False)
    except ValueError:
        check("H-02 wrong lease nonce rejected", True)

    # H-03: wrong session rejected
    p3 = dict(fwd)
    p3.update(_lease_params(lease, WA, "sess_WRONG", micro_unit="mu_bind"))
    try:
        cl.call("step.worker_forward.submit", p3)
        check("H-03 wrong session rejected", False)
    except ValueError:
        check("H-03 wrong session rejected", True)

    # H-04: lease cross-run rejected
    p4 = dict(fwd)
    p4.update(_lease_params(lease, WA, "sess_main", micro_unit="mu_bind", run="run_OTHER"))
    try:
        cl.call("step.worker_forward.submit", p4)
        check("H-04 lease cross-run rejected", False)
    except ValueError:
        check("H-04 lease cross-run rejected", True)

    # H-05: lease cross-assignment rejected
    p5 = dict(fwd)
    p5.update(_lease_params(lease, WA, "sess_main", micro_unit="mu_bind", assignment="a9"))
    try:
        cl.call("step.worker_forward.submit", p5)
        check("H-05 lease cross-assignment rejected", False)
    except ValueError:
        check("H-05 lease cross-assignment rejected", True)

    # H-16: cross-unit rejected (lease de mu_bind usada sobre una altra unitat)
    p16 = dict(fwd)
    p16.update(_lease_params(lease, WA, "sess_main", micro_unit="mu_ALTRES"))
    check("H-16 lease cross-unit rejected", _rejected_any(
        lambda: cl.call("step.worker_forward.submit", p16)))

    # ---- unitat principal amb lease PRÒPIA per al forward real ----
    lease_main = lm.acquire(RUN, RND, "a1", "mu_main", WA, "sess_main", ttl_seconds=10.0)
    p_main = _open_payload(art, server_hash, "u_main", "mu_main", WA, "sess_main")
    p_main.update(_lease_params(lease_main, WA, "sess_main", micro_unit="mu_main"))
    r_main = cl.call("step.open", p_main)
    sa_main = unpack_tensor_envelope(r_main["server_activation"])
    cut_main = wr.worker_forward(sa_main, mask)
    env_main = pack_tensor_envelope(cut_main, "cut_activation", "u_main", "s1")
    fwd_main = {"unit_id": "u_main", "forward_id": "fwd_h", "step_id": "s1",
                "cut_activation": env_main}

    # forward real OK (lease valida) -> backward_id
    r2 = cl.call("step.worker_forward.submit",
                 {**fwd_main, **_lease_params(lease_main, WA, "sess_main", micro_unit="mu_main")})
    bw_id = r2["backward_id"]
    bwd = {"unit_id": "u_main", "backward_id": bw_id}

    # H-06: expired lease forward rejected (forward_id NOU per evitar la
    # idempotència del servidor, que retornaria la resposta cachejada)
    lm.expire(RUN, RND, "a1", "mu_main", coordinator_only=True)
    fwd6 = {"unit_id": "u_main", "forward_id": "fwd_h6", "step_id": "s1",
            "cut_activation": env_main}
    try:
        cl.call("step.worker_forward.submit",
                {**fwd6, **_lease_params(lease_main, WA, "sess_main", micro_unit="mu_main")})
        check("H-06 expired lease forward rejected", False)
    except ValueError:
        check("H-06 expired lease forward rejected", True)

    # H-07: expired lease backward rejected
    try:
        cl.call("step.server_backward.fetch",
                {**bwd, **_lease_params(lease_main, WA, "sess_main", micro_unit="mu_main")})
        check("H-07 expired lease backward rejected", False)
    except ValueError:
        check("H-07 expired lease backward rejected", True)

    # H-08: expired lease commit rejected
    try:
        cl.call("step.commit",
                {"unit_id": "u_main", "delta_id": "d_x",
                 **_lease_params(lease_main, WA, "sess_main", micro_unit="mu_main")})
        check("H-08 expired lease commit rejected", False)
    except ValueError:
        check("H-08 expired lease commit rejected", True)

    # H-09: expired lease upload rejected
    try:
        cl.call("checkpoint.upload",
                {"receipt": {"receipt_id": "r_x"}, "delta_bundle_b64": "eA==",
                 "delta_bundle_sha256": "0" * 64,
                 **_lease_params(lease_main, WA, "sess_main", micro_unit="mu_main")})
        check("H-09 expired lease upload rejected", False)
    except ValueError:
        check("H-09 expired lease upload rejected", True)

    # H-10: released lease rejected (forward)
    lease10 = lm.acquire(RUN, RND, "a1", "mu_rel", WA, "sess_rel", ttl_seconds=30.0)
    lm.release(lease10["lease_id"], WA, "sess_rel", lease10["lease_nonce"])
    p10 = _open_payload(art, server_hash, "u_rel", "mu_rel", WA, "sess_rel")
    p10.update(_lease_params(lease10, WA, "sess_rel", micro_unit="mu_rel"))
    try:
        cl.call("step.open", p10)
        check("H-10 released lease rejected", False)
    except ValueError:
        check("H-10 released lease rejected", True)

    # H-11: old worker rejected after reassign (A intenta forward amb la lease
    # de B sobre la PRÒPIA unitat d'A: l'única discrepància és el worker_id —
    # detecta MUT-R4-34)
    leaseA = lm.acquire(RUN, RND, "a1", "mu_old", WA, "sess_old", ttl_seconds=10.0)
    # A obre la seva unitat a a1 (forward real)
    p11a = _open_payload(art, server_hash, "u_old", "mu_old", WA, "sess_old",
                         assignment="a1", shard="shard_A", lk=90)
    p11a.update(_lease_params(leaseA, WA, "sess_old", assignment="a1", micro_unit="mu_old"))
    r11 = cl.call("step.open", p11a)
    sa11 = unpack_tensor_envelope(r11["server_activation"])
    cut11 = wr.worker_forward(sa11, mask)
    env11 = pack_tensor_envelope(cut11, "cut_activation", "u_old", "s1")
    # reassignació: la lease d'A expira, B n'adquireix una de nova (mateixa unitat)
    lm.expire(RUN, RND, "a1", "mu_old", coordinator_only=True)
    leaseB = lm.acquire(RUN, RND, "a1", "mu_old", WB, "sessB_old", ttl_seconds=30.0)
    # A intenta forward amb la lease de B (ACTIVE) però worker_id=WA
    fwd11 = {"unit_id": "u_old", "forward_id": "fwd_oldB", "step_id": "s1",
             "cut_activation": env11}
    p11 = dict(fwd11)
    p11.update(_lease_params(leaseB, WA, "sessB_old", assignment="a1", micro_unit="mu_old"))
    try:
        cl.call("step.worker_forward.submit", p11)
        check("H-11 old worker rejected after reassign", False)
    except ValueError:
        check("H-11 old worker rejected after reassign", True)

    # H-12: contribution registration sense lease rejected (signatura nova:
    # keyword-only; sense args -> TypeError)
    try:
        srv.proto.recovery.register_uploaded_contribution("cid_fake")
        check("H-12 contribution registration sense lease rejected", False)
    except Exception:
        check("H-12 contribution registration sense lease rejected", True)
    # H-12b: registration amb lease inexistent rejected amb UNKNOWN_LEASE
    # (distingeix del "coordinator_not_attached" que passaria si el gate es
    #  bypasseja — detecta MUT-R4-29)
    try:
        srv.proto.recovery.register_uploaded_contribution(
            "cid_fake2", lease_id="nolease", lease_nonce=1, run_id=RUN,
            round_id=RND, assignment_id="a1", micro_unit_id="mu1",
            worker_id=WA, session_id="sess_main")
        check("H-12b registration amb lease inexistent rejected", False)
    except Exception as e:
        msg = str(e)
        check("H-12b registration amb lease inexistent rejected",
              "unknown_lease" in msg)

    # H-13: backward.fetch sense lease -> rejected (dispatcher)
    check("H-13 backward.fetch sense lease rejected",
          _rejected_missing_lease(lambda: cl.call(
              "step.server_backward.fetch",
              {"unit_id": "u_main", "backward_id": bw_id})))

    # H-14: step.commit sense lease -> rejected (dispatcher)
    check("H-14 step.commit sense lease rejected",
          _rejected_missing_lease(lambda: cl.call(
              "step.commit", {"unit_id": "u_main", "delta_id": "d_x"})))

    # H-15: checkpoint.upload sense lease -> rejected (dispatcher)
    check("H-15 checkpoint.upload sense lease rejected",
          _rejected_missing_lease(lambda: cl.call(
              "checkpoint.upload",
              {"receipt": {"receipt_id": "r_x"}, "delta_bundle_b64": "eA==",
               "delta_bundle_sha256": "0" * 64})))

    conn = coord._conn
    conn.close()
    if os.path.exists(db):
        os.remove(db)
    npass = sum(1 for _, ok, _ in RESULTS if ok)
    nfail = len(RESULTS) - npass
    print(f"\n=== HTTP LEASE ADVERSARIAL: {npass}/{len(RESULTS)} PASS, {nfail} FAIL ===")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
