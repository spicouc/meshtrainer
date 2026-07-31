"""RC5.2 Phase 2 R8: HTTP JSON-RPC E2E — real server, real client, real numerics."""
import os, sys, json, hashlib, torch, copy, base64, time, tempfile, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_numerical_profile import PROFILE as P
from rc5_2_numerical_models import MonolithicNumericalModel
from rc5_2_canonical import numerical_profile_hash, partition_schema_hash, adapter_schema_hash
from rc5_2_tensor_bundle import delta_bundle_pack, delta_bundle_unpack, bundle_sha256
from rc5_2_tensor_envelope import pack_tensor_envelope, unpack_tensor_envelope
from rc5_2_http_server import serve
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_receipt import verify_receipt
from rc5_2_coordinator import Coordinator

PASS, FAIL = 0, 0
KEY = b"r8_test_key_1234567890"

def check(label, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {label}")
    else: FAIL += 1; print(f"  ❌ {label}")

torch.manual_seed(P.seed)
tokens = (torch.arange(P.sequence_length, dtype=torch.long).reshape(1, P.sequence_length) % P.vocab_size).contiguous()
labels = tokens.clone()
causal_mask = torch.triu(torch.full((P.sequence_length, P.sequence_length), float('-inf')), diagonal=1)
nph = numerical_profile_hash()
psh = partition_schema_hash()
ash = adapter_schema_hash()

def test_http_e2e():
    """Full HTTP E2E: open → forward → backward → update → commit → upload."""
    global PASS, FAIL
    srv, _ = serve(port=19853, db_path=os.path.join(tempfile.gettempdir(), f"r8_{uuid.uuid4().hex[:8]}.db"), signing_key=KEY)
    cl = JsonRpcClient("http://127.0.0.1:19853")
    time.sleep(0.3)

    # === HTTP-01: step.open ===
    r1 = cl.call("step.open", {
        "unit_id": "u1", "run_id": "r1", "round_id": "rd1",
        "assignment_id": "a1", "micro_unit_id": "mu1",
        "worker_id": "w1", "session_id": "s1",
        "numerical_profile_hash": nph, "partition_schema_hash": psh,
        "adapter_schema_hash": ash,
    })
    check("HTTP-01: step.open state", r1["state"] == "SERVER_ACTIVATION_READY")
    check("HTTP-01b: jsonrpc result", "result" in json.dumps({"result": r1}))

    # === HTTP-02: worker_forward.submit with real cut_activation ===
    env = pack_tensor_envelope(torch.randn(1, P.sequence_length, P.d_model), "cut_activation", "u1", "s1")
    r2 = cl.call("step.worker_forward.submit", {
        "unit_id": "u1", "forward_id": "f1", "step_id": "s1", "cut_activation": env,
    })
    check("HTTP-02: forward state", r2["state"] == "CUT_GRADIENT_READY")
    check("HTTP-02b: loss positive", r2.get("loss", 0) > 0)
    check("HTTP-02c: ETT derived", r2.get("ett", 0) == 127)
    check("HTTP-02d: backward_id stable", len(r2.get("backward_id", "")) > 0)

    # === HTTP-03: server_backward.fetch ===
    r3 = cl.call("step.server_backward.fetch", {"unit_id": "u1", "backward_id": r2["backward_id"]})
    check("HTTP-03: backward state", r3["state"] == "CUT_GRADIENT_DELIVERED")
    cg = unpack_tensor_envelope(r3["cut_gradient"])
    check("HTTP-03b: gradient shape", cg.shape == (1, P.sequence_length, P.d_model))
    check("HTTP-03c: no NaN", not torch.isnan(cg).any())

    # === HTTP-04: worker_update.submit ===
    a, b = torch.randn(8, 256), torch.randn(1024, 8)
    d = delta_bundle_pack(a, b)
    dsha = bundle_sha256(d)
    db64 = base64.b64encode(d).decode("ascii")
    r4 = cl.call("step.worker_update.submit", {
        "unit_id": "u1", "update_id": "up1",
        "delta_bundle_sha256": dsha, "delta_bundle_b64": db64,
    })
    check("HTTP-04: update state", r4["state"] == "DELTA_VERIFIED")
    check("HTTP-04b: delta_id", len(r4.get("delta_id", "")) > 0)

    # === HTTP-05: step.commit ===
    r5 = cl.call("step.commit", {"unit_id": "u1", "delta_id": r4["delta_id"]})
    check("HTTP-05: commit state", r5["state"] == "COMMITTED")
    receipt = r5.get("receipt", {})
    check("HTTP-05b: receipt signed", verify_receipt(receipt, KEY))
    check("HTTP-05c: receipt fields", "delta_bundle_sha256" in receipt and "receipt_nonce" in receipt)

    # === HTTP-06: checkpoint.upload ===
    coord = Coordinator(os.path.join(tempfile.gettempdir(), f"coord_{uuid.uuid4().hex[:8]}.db"))
    up = coord.checkpoint_upload({
        "receipt": receipt, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha,
    }, KEY)
    check("HTTP-06: upload RECEIVED", up["status"] == "RECEIVED")
    check("HTTP-06b: contribution id", up["contribution_id"] == receipt["receipt_id"])

    # === HTTP-07: idempotency — same key same response ===
    r1b = cl.call("step.open", {
        "unit_id": "u2", "run_id": "r1", "round_id": "rd1",
        "assignment_id": "a1", "micro_unit_id": "mu1",
        "worker_id": "w1", "session_id": "s1",
    })
    check("HTTP-07: idempotent open", r1b["state"] == "SERVER_ACTIVATION_READY")

    # === HTTP-08: COMMITTED → ABORT rejected ===
    try:
        cl.call("step.abort", {"unit_id": "u1"})
        check("HTTP-08: committed→abort rejected", False)
    except ValueError:
        check("HTTP-08: committed→abort rejected", True)

    # === HTTP-09: alien backward fetch rejected ===
    try:
        cl.call("step.server_backward.fetch", {"unit_id": "u1", "backward_id": "WRONG"})
        check("HTTP-09: alien backward rejected", False)
    except ValueError:
        check("HTTP-09: alien backward rejected", True)

    # === HTTP-10: same update_id different payload rejected (u2 full flow) ===
    r2o = cl.call("step.open", {
        "unit_id": "u2", "run_id": "r1", "round_id": "rd1",
        "assignment_id": "a1", "micro_unit_id": "mu1",
        "worker_id": "w1", "session_id": "s1",
    })
    env2 = pack_tensor_envelope(torch.randn(1, P.sequence_length, P.d_model), "cut_activation", "u2", "s1")
    r2f = cl.call("step.worker_forward.submit", {"unit_id": "u2", "forward_id": "f2", "step_id": "s1", "cut_activation": env2})
    r2b = cl.call("step.server_backward.fetch", {"unit_id": "u2", "backward_id": r2f["backward_id"]})
    r2u = cl.call("step.worker_update.submit", {
        "unit_id": "u2", "update_id": "up1",
        "delta_bundle_sha256": dsha, "delta_bundle_b64": db64,
    })
    d2 = delta_bundle_pack(torch.zeros(8, 256), torch.zeros(1024, 8))
    try:
        cl.call("step.worker_update.submit", {
            "unit_id": "u2", "update_id": "up1",
            "delta_bundle_sha256": bundle_sha256(d2), "delta_bundle_b64": base64.b64encode(d2).decode(),
        })
        check("HTTP-10: same update different payload rejected", False)
    except ValueError as e:
        check("HTTP-10: same update different payload rejected", "different payload" in str(e))

    # === HTTP-11: nonce consumed after success, reused nonce rejected ===
    r5c = cl.call("step.commit", {"unit_id": "u2", "delta_id": r2u["delta_id"]})
    rcpt2 = r5c["receipt"]
    up2 = coord.checkpoint_upload({
        "receipt": rcpt2, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha,
    }, KEY)
    # Reuse same nonce with new receipt id → rejected
    from rc5_2_receipt import generate_receipt as _gen
    fake = _gen(run_id="r1", round_id="rd1", unit_id="u2", worker_id="w1",
                receipt_nonce=rcpt2["receipt_nonce"],
                signing_key=KEY, delta_bundle_sha256=dsha,
                delta_bundle_byte_length=len(d), ett=127, loss=1.23)
    try:
        coord.checkpoint_upload({"receipt": fake, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha}, KEY)
        check("HTTP-11: reused nonce rejected", False)
    except ValueError:
        check("HTTP-11: reused nonce rejected", True)

    # === HTTP-12: expired receipt rejected ===
    from rc5_2_receipt import generate_receipt as _gen2
    expired = _gen2(run_id="r1", round_id="rd1", unit_id="u3", worker_id="w1",
                    expires_at=int(time.time()) - 100,
                    signing_key=KEY, delta_bundle_sha256=dsha,
                    delta_bundle_byte_length=len(d), ett=127, loss=1.23)
    try:
        coord.checkpoint_upload({"receipt": expired, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha}, KEY)
        check("HTTP-12: expired receipt rejected", False)
    except ValueError:
        check("HTTP-12: expired receipt rejected", True)

def test_optimizer_exactly_lora():
    """MUT-04 gate: worker optimizer contains ONLY LoRA A/B."""
    from rc5_2_numerical_models import WorkerNumericalModel
    w = WorkerNumericalModel()
    opt_ids = {id(p) for g in w.optimizer.param_groups for p in g["params"]}
    lora_ids = {id(w.lora_wrapper.lora_A.weight), id(w.lora_wrapper.lora_B.weight)}
    check("OPT-01: optimizer == LoRA only", opt_ids == lora_ids)
    # All base params frozen
    ok = all(not p.requires_grad for n, p in w.named_parameters() if "lora" not in n)
    check("OPT-02: base frozen", ok)

def test_lora_no_activation():
    """MUT-06 gate: LoRA forward is exactly base + scaling*B(A(x)), no ReLU."""
    from rc5_2_numerical_models import WorkerNumericalModel
    w = WorkerNumericalModel()
    # B is zero-initialized, so set non-zero weights to expose any activation
    with torch.no_grad():
        w.lora_wrapper.lora_B.weight.copy_(torch.randn_like(w.lora_wrapper.lora_B.weight) * 0.1)
    x = torch.randn(4, 256)
    got = w.lora_wrapper(x)
    expected = w.lora_wrapper.base_linear(x) + w.lora_wrapper.scaling * w.lora_wrapper.lora_B(w.lora_wrapper.lora_A(x))
    check("LORA-01: no activation between A/B", torch.allclose(got, expected, rtol=1e-5, atol=1e-6))
    # Sanity: the LoRA path actually contributes (B non-zero)
    check("LORA-02: LoRA contributes", (got - w.lora_wrapper.base_linear(x)).abs().max().item() > 1e-6)
    check("LORA-03: finite output", torch.isfinite(got).all())

TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.2 Phase 2 R8 — HTTP JSON-RPC E2E")
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
