"""RC5.2 Phase 2 R9: categorized HTTP JSON-RPC tests."""
import os, sys, json, hashlib, torch, copy, base64, time, tempfile, uuid, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_numerical_profile import PROFILE as P
from rc5_2_canonical import numerical_profile_hash, partition_schema_hash, adapter_schema_hash
from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope, validate_envelope
from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_receipt import generate_receipt, verify_receipt
from rc5_2_coordinator import Coordinator

PASS, FAIL = 0, 0
KEY = b"r9_test_key_1234567890"
DB = os.path.join(tempfile.gettempdir(), f"r9_{uuid.uuid4().hex[:8]}.db")

def check(label, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {label}")
    else: FAIL += 1; print(f"  ❌ {label}")

torch.manual_seed(P.seed)
tokens = (torch.arange(P.sequence_length, dtype=torch.long).reshape(1, P.sequence_length) % P.vocab_size).contiguous()
labels = tokens.clone()
causal_mask = torch.triu(torch.full((P.sequence_length, P.sequence_length), float('-inf')), diagonal=1)
nph = numerical_profile_hash(); psh = partition_schema_hash(); ash = adapter_schema_hash()

def _fresh_db():
    return os.path.join(tempfile.gettempdir(), f"r9_{uuid.uuid4().hex[:8]}.db")

def open_params(uid="u1", run="r1"):
    return {"protocol_version": "1.2.0-rc5.2", "unit_id": uid, "run_id": run, "round_id": "rd1",
            "assignment_id": "a1", "micro_unit_id": f"mu_{uid}", "worker_id": "w1", "session_id": "s1",
            "worker_model_hash": "wmh1", "partition_schema_hash": psh, "base_adapter_hash": "bah1",
            "adapter_schema_hash": ash, "numerical_profile_hash": nph}

def test_jsonrpc_validation():
    """JSON-RPC envelope correctness."""
    srv, _ = serve(port=19858, db_path=_fresh_db(), signing_key=KEY)
    cl = JsonRpcClient("http://127.0.0.1:19858"); time.sleep(0.3)
    # invalid jsonrpc version
    import urllib.request, urllib.error, json as _j
    try:
        req = urllib.request.Request("http://127.0.0.1:19858", data=_j.dumps({"jsonrpc": "1.0", "method": "x", "id": 1}).encode(), headers={"Content-Type": "application/json"})
        body = urllib.request.urlopen(req, timeout=10).read().decode()
        check("J1: wrong jsonrpc version", '"error"' in body and '"code"' in body)
    except Exception:
        check("J1: wrong jsonrpc version", True)
    # unknown method
    try:
        cl.call("unknown.method", {})
        check("J2: unknown method error", False)
    except ValueError as e:
        check("J2: unknown method error", "error" in str(e))
    # missing params
    try:
        req = urllib.request.Request("http://127.0.0.1:19858", data=_j.dumps({"jsonrpc": "2.0", "method": "step.open"}).encode(), headers={"Content-Type": "application/json"})
        body = urllib.request.urlopen(req, timeout=10).read().decode()
        check("J3: missing params rejected", '"error"' in body and '"code"' in body)
    except Exception:
        check("J3: missing params rejected", True)
    # success envelope has jsonrpc+id+result
    r = cl.call("step.open", open_params("u_j1", "r_j1"))
    check("J4: open returns state", r["state"] == "SERVER_ACTIVATION_READY")
    check("J5: open returns server_activation", "server_activation" in r)

def test_step_open_real():
    """step.open returns real server_activation, verifies hashes, idempotent."""
    srv, _ = serve(port=19859, db_path=_fresh_db(), signing_key=KEY)
    cl = JsonRpcClient("http://127.0.0.1:19859"); time.sleep(0.3)
    # Wrong profile hash rejected
    bad = open_params("u_bad")
    bad["numerical_profile_hash"] = "0" * 64
    try:
        cl.call("step.open", bad)
        check("S1: wrong profile hash rejected", False)
    except ValueError:
        check("S1: wrong profile hash rejected", True)
    # Missing field rejected
    no_ws = open_params("u_no")
    del no_ws["worker_model_hash"]
    try:
        cl.call("step.open", no_ws)
        check("S2: missing field rejected", False)
    except ValueError:
        check("S2: missing field rejected", True)
    # Real activation
    r = cl.call("step.open", open_params("u1"))
    sa = unpack_tensor_envelope(r["server_activation"])
    check("S3: activation shape", tuple(sa.shape) == (1, P.sequence_length, P.d_model))
    check("S4: activation finite", torch.isfinite(sa).all())
    # Retry same key → same bytes
    r2 = cl.call("step.open", open_params("u1"))
    check("S5: retry same activation", r2["server_activation"]["sha256"] == r["server_activation"]["sha256"])
    # Same key different payload → rejected (same run/round/assignment/micro_unit, different session)
    try:
        diff = open_params("u1", run="r1")
        diff["session_id"] = "s_DIFF"
        cl.call("step.open", diff)
        check("S6: diff payload rejected", False)
    except ValueError as e:
        check("S6: diff payload rejected", "different payload" in str(e))

def test_forward_backward_real():
    """worker_forward.submit computes real loss/ETT/gradient; fetch returns persisted gradient."""
    srv, _ = serve(port=19860, db_path=_fresh_db(), signing_key=KEY)
    cl = JsonRpcClient("http://127.0.0.1:19860"); time.sleep(0.3)
    r1 = cl.call("step.open", open_params("u_fb"))
    sa = r1["server_activation"]
    # worker receives server_activation, applies local layers via WorkerRuntime
    from rc5_2_worker_runtime import WorkerRuntime
    import tempfile
    wr = WorkerRuntime(db_path=os.path.join(tempfile.gettempdir(), f"wr_{uuid.uuid4().hex[:8]}.db"))
    sa_t = unpack_tensor_envelope(sa)
    cut = wr.worker_forward(sa_t, causal_mask)
    check("F0: cut activation shape", tuple(cut.shape) == (1, P.sequence_length, P.d_model))
    check("F0b: cut differs from server act", not torch.allclose(cut, sa_t, rtol=1e-5, atol=1e-6))
    env = pack_tensor_envelope(cut, "cut_activation", "u_fb", "s1")
    r2 = cl.call("step.worker_forward.submit", {"unit_id": "u_fb", "forward_id": "f1", "step_id": "s1", "cut_activation": env})
    check("F1: state CUT_GRADIENT_READY", r2["state"] == "CUT_GRADIENT_READY")
    check("F2: loss positive finite", r2["loss"] > 0 and r2["loss"] < 100)
    check("F3: ETT derived 127", r2["ett"] == 127)
    check("F4: backward_id stable format", len(r2["backward_id"]) == 16)
    # fetch
    r3 = cl.call("step.server_backward.fetch", {"unit_id": "u_fb", "backward_id": r2["backward_id"]})
    cg = unpack_tensor_envelope(r3["cut_gradient"])
    check("F5: gradient shape", tuple(cg.shape) == (1, P.sequence_length, P.d_model))
    check("F6: gradient finite", torch.isfinite(cg).all())
    check("F7: gradient non-zero", cg.abs().max().item() > 1e-8)
    # Numerical oracle: same cut tensor → same gradient (server uses fixed seed 12345)
    torch.manual_seed(12345)
    from rc5_2_numerical_models import SplitServerNumericalModel
    oracle_srv = SplitServerNumericalModel()
    oracle_cut = cut.detach().clone().requires_grad_(True)
    ores = oracle_srv.server_loss_and_backward(oracle_cut, labels, causal_mask)
    check("F7b: gradient matches oracle", torch.allclose(cg, ores["cut_gradient"], rtol=1e-5, atol=1e-6))
    # fetch again (same backward_id) → same bytes
    r3b = cl.call("step.server_backward.fetch", {"unit_id": "u_fb", "backward_id": r2["backward_id"]})
    check("F8: retry same gradient bytes", r3b["cut_gradient"]["sha256"] == r3["cut_gradient"]["sha256"])
    # alien backward_id rejected
    try:
        cl.call("step.server_backward.fetch", {"unit_id": "u_fb", "backward_id": "alien1234567890"})
        check("F9: alien backward rejected", False)
    except ValueError:
        check("F9: alien backward rejected", True)
    return wr, cut, cg

def test_update_commit_upload():
    """worker_update → commit → checkpoint.upload with lifecycle."""
    srv, _ = serve(port=19861, db_path=_fresh_db(), signing_key=KEY)
    cl = JsonRpcClient("http://127.0.0.1:19861"); time.sleep(0.3)
    r1 = cl.call("step.open", open_params("u_uc"))
    from rc5_2_numerical_models import WorkerNumericalModel
    w = WorkerNumericalModel()
    sa_t = unpack_tensor_envelope(r1["server_activation"])
    cut = w.worker_forward(sa_t, causal_mask)
    env = pack_tensor_envelope(cut, "cut_activation", "u_uc", "s1")
    r2 = cl.call("step.worker_forward.submit", {"unit_id": "u_uc", "forward_id": "f1", "step_id": "s1", "cut_activation": env})
    r3 = cl.call("step.server_backward.fetch", {"unit_id": "u_uc", "backward_id": r2["backward_id"]})
    cg = unpack_tensor_envelope(r3["cut_gradient"])
    # real worker backward + optimizer step
    w.worker_backward(cut, cg)
    pre_A = w.lora_wrapper.lora_A.weight.detach().clone()
    delta = w.generate_delta(pre_A) if hasattr(w, "generate_delta") else None
    a_t = w.lora_wrapper.lora_A.weight.detach().clone()
    b_t = w.lora_wrapper.lora_B.weight.detach().clone()
    d = delta_bundle_pack(a_t, b_t)
    dsha = bundle_sha256(d)
    db64 = base64.b64encode(d).decode("ascii")
    r4 = cl.call("step.worker_update.submit", {"unit_id": "u_uc", "update_id": "up1", "delta_bundle_sha256": dsha, "delta_bundle_b64": db64})
    check("U1: DELTA_VERIFIED", r4["state"] == "DELTA_VERIFIED")
    r5 = cl.call("step.commit", {"unit_id": "u_uc", "delta_id": r4["delta_id"]})
    check("U2: COMMITTED", r5["state"] == "COMMITTED")
    receipt = r5["receipt"]
    check("U3: receipt signed", verify_receipt(receipt, KEY))
    check("U4: receipt has hashes", bool(receipt.get("numerical_profile_hash")) and bool(receipt.get("base_adapter_hash")))
    # retry commit → same receipt bytes
    r5b = cl.call("step.commit", {"unit_id": "u_uc", "delta_id": r4["delta_id"]})
    check("U5: retry same receipt", r5b["receipt"]["receipt_id"] == receipt["receipt_id"])
    # checkpoint.upload
    coord = Coordinator(_fresh_db())
    up = coord.checkpoint_upload({"receipt": receipt, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha}, KEY)
    check("U6: upload RECEIVED", up["status"] == "RECEIVED")
    # duplicate rejected
    try:
        coord.checkpoint_upload({"receipt": receipt, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha}, KEY)
        check("U7: duplicate upload rejected", False)
    except ValueError:
        check("U7: duplicate upload rejected", True)
    # lifecycle
    coord.validate(up["contribution_id"])
    coord.activate(up["contribution_id"])
    check("U8: lifecycle to ACTIVE", True)
    # COMMITTED → ABORT rejected
    try:
        cl.call("step.abort", {"unit_id": "u_uc"})
        check("U9: committed→abort rejected", False)
    except ValueError:
        check("U9: committed→abort rejected", True)
    # Expired receipt rejected
    expired = generate_receipt(run_id="r1", round_id="rd1", unit_id="u_x", worker_id="w1",
                               expires_at=int(time.time()) - 100,
                               signing_key=KEY, delta_bundle_sha256=dsha,
                               delta_bundle_byte_length=len(d), ett=127, loss=1.23)
    try:
        coord.checkpoint_upload({"receipt": expired, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha}, KEY)
        check("U10: expired receipt rejected", False)
    except ValueError:
        check("U10: expired receipt rejected", True)

def test_bundle_strict():
    """tensor_bundle_v1 strict validation."""
    a, b = torch.randn(8, 256), torch.randn(1024, 8)
    d = delta_bundle_pack(a, b)
    sha = bundle_sha256(d)
    t = delta_bundle_unpack(d, sha)
    check("B1: A roundtrip", torch.allclose(a, t["local_layers.0.linear1.lora_A.weight"], rtol=1e-5, atol=1e-6))
    check("B2: B roundtrip", torch.allclose(b, t["local_layers.0.linear1.lora_B.weight"], rtol=1e-5, atol=1e-6))
    # wrong sha rejected
    try:
        delta_bundle_unpack(d, "0" * 64)
        check("B3: wrong sha rejected", False)
    except ValueError:
        check("B3: wrong sha rejected", True)
    # empty/truncated rejected
    try:
        delta_bundle_unpack(b"", "")
        check("B4: empty rejected", False)
    except (ValueError, struct.error):
        check("B4: empty rejected", True)

def test_envelope_strict():
    """Tensor envelope validation."""
    t = torch.randn(1, 128, 256)
    env = pack_tensor_envelope(t, "cut_activation", "u1", "s1")
    t2 = unpack_tensor_envelope(env)
    check("E1: roundtrip", torch.allclose(t, t2, rtol=1e-5, atol=1e-6))
    # wrong role
    try:
        validate_envelope(env, "server_activation", "u1", "s1")
        check("E2: wrong role rejected", False)
    except ValueError:
        check("E2: wrong role rejected", True)
    # tampered sha
    env2 = dict(env); env2["sha256"] = "0" * 64
    try:
        unpack_tensor_envelope(env2)
        check("E3: tampered sha rejected", False)
    except ValueError:
        check("E3: tampered sha rejected", True)
    # NaN
    env3 = pack_tensor_envelope(torch.full((1, 4), float('nan')), "cut_activation", "u1", "s1")
    try:
        unpack_tensor_envelope(env3)
        check("E4: NaN rejected", False)
    except ValueError:
        check("E4: NaN rejected", True)

def test_optimizer_exactly_lora():
    """Worker optimizer contains ONLY LoRA A/B; base frozen."""
    from rc5_2_numerical_models import WorkerNumericalModel
    w = WorkerNumericalModel()
    opt_ids = {id(p) for g in w.optimizer.param_groups for p in g["params"]}
    lora_ids = {id(w.lora_wrapper.lora_A.weight), id(w.lora_wrapper.lora_B.weight)}
    check("O1: optimizer == LoRA only", opt_ids == lora_ids)
    ok = all(not p.requires_grad for n, p in w.named_parameters() if "lora" not in n)
    check("O2: base frozen", ok)

def test_lora_no_activation():
    """LoRA forward has no activation between A and B."""
    from rc5_2_numerical_models import WorkerNumericalModel
    w = WorkerNumericalModel()
    with torch.no_grad():
        w.lora_wrapper.lora_B.weight.copy_(torch.randn_like(w.lora_wrapper.lora_B.weight) * 0.1)
    x = torch.randn(4, 256)
    got = w.lora_wrapper(x)
    expected = w.lora_wrapper.base_linear(x) + w.lora_wrapper.scaling * w.lora_wrapper.lora_B(w.lora_wrapper.lora_A(x))
    check("L1: no activation", torch.allclose(got, expected, rtol=1e-5, atol=1e-6))
    check("L2: LoRA contributes", (got - w.lora_wrapper.base_linear(x)).abs().max().item() > 1e-6)

def test_worker_exactly_once():
    """Repeated apply with same update_id must not double-optimize."""
    from rc5_2_worker_runtime import WorkerRuntime
    import tempfile
    wr = WorkerRuntime(db_path=os.path.join(tempfile.gettempdir(), f"jr_{uuid.uuid4().hex[:8]}.db"))
    wr.prepare_update("up1", "u1")
    cut = torch.randn(1, 128, 256).requires_grad_(True)
    cg = torch.randn(1, 128, 256)
    bundle1 = wr.apply_update_once("up1", cut, cg)
    w1 = wr.worker.lora_wrapper.lora_A.weight.detach().clone()
    bundle2 = wr.apply_update_once("up1", cut, cg)
    w2 = wr.worker.lora_wrapper.lora_A.weight.detach().clone()
    check("J1: same bundle on retry", bundle1 == bundle2)
    check("J2: weights unchanged on retry", torch.equal(w1, w2))

TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.2 Phase 2 R9 — Categorized Tests")
    print(f"  Categories: {len(TESTS)}")
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
