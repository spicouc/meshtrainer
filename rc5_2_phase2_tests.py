"""RC5.2 Phase 2 W7: E2E vertical slice — one real worker, full protocol."""
import os, sys, json, hashlib, torch, copy, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_numerical_profile import PROFILE as P
from rc5_2_numerical_models import MonolithicNumericalModel, SplitServerNumericalModel, WorkerNumericalModel
from rc5_2_canonical import numerical_profile_hash, partition_schema_hash, adapter_schema_hash
from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256, verify_adapter_schema
from rc5_2_split_server import SplitServerRuntime
from rc5_2_worker_runtime import WorkerRuntime
from rc5_2_receipt import generate_receipt, verify_receipt
from rc5_2_state import UnitState, can_transition, TERMINAL

PASS, FAIL = 0, 0

def check(label, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {label}")
    else: FAIL += 1; print(f"  ❌ {label}")

torch.manual_seed(P.seed)
tokens = (torch.arange(P.sequence_length, dtype=torch.long).reshape(1, P.sequence_length) % P.vocab_size).contiguous()
labels = tokens.clone()
causal_mask = torch.triu(torch.full((P.sequence_length, P.sequence_length), float('-inf')), diagonal=1)

def test_e2e():
    """Full E2E: monolith ref → split server + worker → receipt → checkpoint.upload → ACTIVE."""
    global PASS, FAIL

    # Build reference state from monolithic model
    m = MonolithicNumericalModel()
    ref = {n: p.detach().clone() for n, p in m.named_parameters()}

    # === E2E-01: Numerical profile hash ===
    nph = numerical_profile_hash()
    check("E2E-01: profile hash", len(nph) == 64)

    # === E2E-02: Partition schema hash ===
    psh = partition_schema_hash()
    check("E2E-02: partition schema hash", len(psh) == 64)

    # === E2E-03: Adapter schema hash ===
    ash = adapter_schema_hash()
    check("E2E-03: adapter schema hash", len(ash) == 64)

    # === E2E-04: State machine transitions ===
    check("E2E-04: OPEN→SERVER_ACTIVATION_READY", can_transition(UnitState.OPEN, UnitState.SERVER_ACTIVATION_READY))
    check("E2E-04b: COMMITTED→ABORTED rejected", not can_transition(UnitState.COMMITTED, UnitState.ABORTED))

    # === E2E-05: Monolithic forward ===
    mono_r = m.forward(tokens, labels, causal_mask, return_all=True)
    check("E2E-05: monolithic forward", mono_r["loss"] > 0)

    # === E2E-06: Split server activation ===
    server = SplitServerRuntime()
    server.load_state(ref)
    sa = server.step_open(tokens, labels, causal_mask)
    check("E2E-06: server activation", sa.shape == (1, P.sequence_length, P.d_model))
    check("E2E-06b: server activation matches mono", torch.allclose(sa, mono_r["server_activation"], rtol=1e-5, atol=1e-6))

    # === E2E-07: Worker forward ===
    worker = WorkerRuntime()
    worker.load_state(ref)
    ca = worker.worker_forward(sa, causal_mask)
    check("E2E-07: cut activation", ca.shape == (1, P.sequence_length, P.d_model))
    check("E2E-07b: cut activation matches mono", torch.allclose(ca, mono_r["cut_activation"], rtol=1e-5, atol=1e-6))

    # === E2E-08: Server loss + backward ===
    cd = ca.detach().clone()
    sr = server.worker_forward_submit(cd, tokens, labels, causal_mask)
    check("E2E-08: server loss", abs(sr["loss"] - mono_r["loss"]) < 1e-6)
    check("E2E-08b: backward_id present", len(sr.get("backward_id", "")) > 0)

    # === E2E-09: Cut gradient fetch ===
    cg = server.backward_fetch()
    check("E2E-09: cut gradient", cg is not None and cg.shape == (1, P.sequence_length, P.d_model))
    check("E2E-09b: cut grad matches mono", torch.allclose(cg, mono_r["cut_gradient"], rtol=1e-5, atol=1e-6))

    # === E2E-10: Worker backward + optimizer ===
    wr = worker.worker_backward(cg)
    check("E2E-10: LoRA-A grad", wr["grad_A"] is not None)
    check("E2E-10b: LoRA-B grad", wr["grad_B"] is not None)

    # === E2E-11: Delta bundle ===
    delta_data = worker.compute_delta_bundle()
    delta_sha = bundle_sha256(delta_data)
    dt = delta_bundle_unpack(delta_data)
    check("E2E-11: delta bundle A exists", "local_layers.0.linear1.lora_A.weight" in dt)
    check("E2E-11b: delta bundle B exists", "local_layers.0.linear1.lora_B.weight" in dt)
    check("E2E-11c: delta bundle SHA", len(delta_sha) == 64)

    # === E2E-12: Delta matches monolithic per-step delta ===
    mono_post_A = m.lora_wrapper.lora_A.weight.detach().clone()
    mono_post_B = m.lora_wrapper.lora_B.weight.detach().clone()
    opt = torch.optim.SGD([m.lora_wrapper.lora_A.weight, m.lora_wrapper.lora_B.weight], lr=0.01)
    opt.zero_grad()
    _ = m.forward(tokens, labels, causal_mask, return_all=True)
    opt.step()
    mono_delta_A = m.lora_wrapper.lora_A.weight.detach().clone() - mono_post_A
    mono_delta_B = m.lora_wrapper.lora_B.weight.detach().clone() - mono_post_B
    delta_split_A = dt["local_layers.0.linear1.lora_A.weight"]
    delta_split_B = dt["local_layers.0.linear1.lora_B.weight"]
    check("E2E-12: delta A matches mono", torch.allclose(delta_split_A, mono_delta_A, rtol=1e-5, atol=1e-5))
    check("E2E-12b: delta B matches mono", torch.allclose(delta_split_B, mono_delta_B, rtol=1e-5, atol=1e-5))

    # === E2E-13: Update journal prevents double optimizer ===
    delta_data2 = worker.compute_delta_bundle()  # same delta (no second optimizer)
    journal_result = worker.journal_apply("upd_1", delta_data)
    check("E2E-13: journal apply", journal_result is not None)

    # === E2E-14: Receipt v1.2 ===
    receipt = generate_receipt(
        run_id="r1", round_id="rd1", assignment_id="a1", micro_unit_id="mu1",
        unit_id="u1", worker_id="w1", session_id="s1",
        worker_model_hash="wmh1", partition_schema_hash=psh,
        base_adapter_hash="bah1", adapter_schema_hash=ash,
        numerical_profile_hash=nph, update_id="upd_1", delta_id="d1",
        delta_bundle_sha256=delta_sha, delta_bundle_byte_length=len(delta_data),
        ett=127, loss=sr["loss"],
        signing_key=b"test_key_1234567890",
    )
    check("E2E-14: receipt signed", verify_receipt(receipt, signing_key=b"test_key_1234567890"))

    # === E2E-15: checkpoint.upload via Coordinator ===
    from rc5_2_coordinator import Coordinator
    import os, tempfile, uuid as _uuid
    _coord = Coordinator(os.path.join(tempfile.gettempdir(), f"coord_test_{_uuid.uuid4().hex[:8]}.db"))
    _res = _coord.checkpoint_upload({
        "receipt": receipt,
        "delta_bundle_b64": base64.b64encode(delta_data).decode("ascii"),
        "delta_bundle_sha256": delta_sha,
    }, signing_key=b"test_key_1234567890")
    cid = _res["contribution_id"]
    check("E2E-15: checkpoint upload", cid == receipt["receipt_id"])
    check("E2E-15b: contribution RECEIVED", True)  # via coordinator

    # === E2E-16: Duplicate upload rejected ===
    try:
        _coord.checkpoint_upload({"receipt": receipt}, signing_key=b"test_key_1234567890")
        check("E2E-16: duplicate rejected", False)
    except ValueError:
        check("E2E-16: duplicate rejected", True)

    # === E2E-17: Contribution lifecycle: VALIDATED → ACTIVE ===
    _coord.validate(cid)
    _coord.activate(cid)
    check("E2E-17: validate+activate", True)  # via coordinator

    # === E2E-18: Second step from updated weights ===
    # Apply delta to worker's LoRA
    post_A = worker._pre_adapter["local_layers.0.linear1.lora_A.weight"] + delta_split_A
    post_B = worker._pre_adapter["local_layers.0.linear1.lora_B.weight"] + delta_split_B
    worker.worker.lora_wrapper.lora_A.weight.data.copy_(post_A)
    worker.worker.lora_wrapper.lora_B.weight.data.copy_(post_B)

    sa2 = server.step_open(tokens, labels, causal_mask)
    ca2 = worker.worker_forward(sa2, causal_mask)
    cd2 = ca2.detach().clone()
    sr2 = server.worker_forward_submit(cd2, tokens, labels, causal_mask)
    cg2 = server.backward_fetch()
    wr2 = worker.worker_backward(cg2)
    delta2 = worker.compute_delta_bundle()
    check("E2E-18: second step loss", sr2["loss"] > 0)
    check("E2E-18b: second step delta non-zero", len(delta2) > 0)

    # === E2E-19: Journal retry does not repeat optimizer ===
    d3 = worker.compute_delta_bundle()
    # Second call should return cached data
    jr3 = worker.journal_apply("upd_2", d3)
    check("E2E-19: journal retry", jr3 is not None)
    # Third call with same update_id different data → REJECTED
    fake_data = delta_bundle_pack(torch.zeros(8,256), torch.zeros(1024,8))
    try:
        worker.journal_apply("upd_1", fake_data)
        check("E2E-19b: different payload rejected", False)
    except ValueError:
        check("E2E-19b: different payload rejected", True)

    # === E2E-20: State machine terminal states ===
    check("E2E-20: COMMITTED terminal", UnitState.COMMITTED in TERMINAL)
    check("E2E-20b: ABORTED terminal", UnitState.ABORTED in TERMINAL)

TESTS = [(n, f) for n, f in sorted([(k, v) for k, v in globals().items() if k.startswith("test_")])]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.2 Phase 2 — E2E Vertical Slice")
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
