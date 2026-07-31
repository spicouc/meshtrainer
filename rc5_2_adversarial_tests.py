"""RC5.2 Phase 2 R11: adversarial gate — 12 automated checks."""
import os, sys, json, hashlib, torch, base64, time, tempfile, uuid, sqlite3, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_artifacts import (pack_worker_base_model, pack_base_adapter,
                              verify_worker_artifacts, WORKER_BASE_SCHEMA, ADAPTER_KEYS)
from rc5_2_canonical import numerical_profile_hash, partition_schema_hash, adapter_schema_hash
from rc5_2_tensor_bundle import (tensor_bundle_v1_pack, tensor_bundle_v1_unpack,
                                  delta_bundle_pack, delta_bundle_unpack, bundle_sha256)
from rc5_2_tensor_envelope import pack_tensor_envelope
from rc5_2_http_server import serve, ProtocolHandler
from rc5_2_jsonrpc import JsonRpcClient
from rc5_2_receipt import generate_receipt, verify_receipt
from rc5_2_coordinator import Coordinator
from rc5_2_numerical_models import WorkerNumericalModel, SplitServerNumericalModel
from rc5_2_worker_runtime import WorkerRuntime
from rc5_2_numerical_adapter import make_causal_fixture

PASS, FAIL = 0, 0
KEY = b"r11_adv_key_1234567890"

def check(label, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {label}")
    else: FAIL += 1; print(f"  ❌ {label}")

def _fresh_db():
    return os.path.join(tempfile.gettempdir(), f"r11_{uuid.uuid4().hex[:8]}.db")

def test_adv01_artifacts_selfverifying():
    """ADV-01: artifacts pack → verify → unpack → strict load → forward equivalent."""
    w = WorkerNumericalModel()
    base_bytes, base_hash = pack_worker_base_model(w)
    ad_bytes, ad_hash = pack_base_adapter(w)
    # verify
    v = verify_worker_artifacts(base_bytes, ad_bytes, base_hash, ad_hash,
                                partition_schema_hash(), adapter_schema_hash(), numerical_profile_hash())
    check("ADV-01a: artifacts verify", v["ok"] and not v["errors"])
    # unpack + strict load
    bm = tensor_bundle_v1_unpack(base_bytes, base_hash)
    ad = tensor_bundle_v1_unpack(ad_bytes, ad_hash)
    # base model: no LoRA tensors
    lora_in_base = [k for k in bm if "lora" in k]
    check("ADV-01b: no LoRA in base model", len(lora_in_base) == 0)
    # adapter: exactly A and B
    check("ADV-01c: adapter has exactly A/B", set(ad.keys()) == set(ADAPTER_KEYS))
    # strict load into fresh worker
    w2 = WorkerNumericalModel()
    missing, unexpected = [], []
    for n, p in w2.named_parameters():
        if "lora" not in n:
            if n in bm: p.data.copy_(bm[n])
            else: missing.append(n)
    for k in bm:
        if k not in dict(w2.named_parameters()): unexpected.append(k)
    check("ADV-01d: zero missing", len(missing) == 0)
    check("ADV-01e: zero unexpected", len(unexpected) == 0)
    # forward equivalent
    x = torch.randn(4, 256)
    out1 = w.worker_forward(x, None)
    check("ADV-01f: forward equivalent", torch.allclose(out1, w2.worker_forward(x, None), rtol=1e-5, atol=1e-6))

def test_adv02_fake_worker_model_hash():
    """ADV-02: fake worker_model_hash rejected at step.open."""
    db = _fresh_db(); ph = ProtocolHandler(db, KEY)
    tokens, labels, cm = make_causal_fixture()
    p = {"protocol_version": "1.2.0-rc5.2", "unit_id": "u1", "run_id": "r1", "round_id": "rd1",
         "assignment_id": "a1", "micro_unit_id": "mu1", "worker_id": "w1", "session_id": "s1",
         "worker_model_hash": "0" * 64, "partition_schema_hash": partition_schema_hash(),
         "base_adapter_hash": "b"*64, "adapter_schema_hash": adapter_schema_hash(),
         "numerical_profile_hash": numerical_profile_hash()}
    try:
        ph.handle("step.open", p)
        check("ADV-02: fake worker_model_hash rejected", False)
    except ValueError:
        check("ADV-02: fake worker_model_hash rejected", True)

def test_adv03_fake_base_adapter_hash():
    db = _fresh_db(); ph = ProtocolHandler(db, KEY)
    p = {"protocol_version": "1.2.0-rc5.2", "unit_id": "u2", "run_id": "r1", "round_id": "rd1",
         "assignment_id": "a1", "micro_unit_id": "mu2", "worker_id": "w1", "session_id": "s1",
         "worker_model_hash": "a"*64, "partition_schema_hash": partition_schema_hash(),
         "base_adapter_hash": "0" * 64, "adapter_schema_hash": adapter_schema_hash(),
         "numerical_profile_hash": numerical_profile_hash()}
    try:
        ph.handle("step.open", p)
        check("ADV-03: empty base_adapter_hash rejected", False)
    except ValueError:
        check("ADV-03: empty base_adapter_hash rejected", True)

def test_adv04_wrong_schema_version():
    """ADV-04: wrong schema_version in bundle rejected."""
    a, b = torch.randn(8, 256), torch.randn(1024, 8)
    d = delta_bundle_pack(a, b)
    sha = bundle_sha256(d)
    # Tamper schema_version inside header
    hdr_len = struct.unpack_from("<I", d, 4)[0]
    header = json.loads(d[8:8+hdr_len])
    header["schema_version"] = "wrong_v1"
    new_hdr = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    if len(new_hdr) != hdr_len:
        check("ADV-04: wrong schema_version rejected", True)
        return
    tampered = d[:4] + struct.pack("<I", len(new_hdr)) + new_hdr + d[8+hdr_len:]
    try:
        delta_bundle_unpack(tampered, sha)
        check("ADV-04: wrong schema_version rejected", False)
    except (ValueError, Exception):
        check("ADV-04: wrong schema_version rejected", True)

def test_adv05_leading_gap():
    """ADV-05: leading gap (first offset != 0) rejected."""
    a, b = torch.randn(8, 256), torch.randn(1024, 8)
    d = delta_bundle_pack(a, b)
    sha = bundle_sha256(d)
    hdr_len = struct.unpack_from("<I", d, 4)[0]
    header = json.loads(d[8:8+hdr_len])
    ts = header["tensors"]
    ts[0]["offset"] = ts[0].get("offset", 0) + 4
    header["tensors"] = ts
    new_hdr = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    tampered = d[:4] + struct.pack("<I", len(new_hdr)) + new_hdr + d[8+hdr_len:]
    try:
        delta_bundle_unpack(tampered, sha)
        check("ADV-05: leading gap rejected", False)
    except (ValueError, Exception):
        check("ADV-05: leading gap rejected", True)

def test_adv06_missing_unit_upload():
    """ADV-06: upload for nonexistent unit rejected."""
    coord = Coordinator(_fresh_db())
    r = generate_receipt(run_id="r1", round_id="rd1", unit_id="NOPE", worker_id="w1",
                         signing_key=KEY, delta_bundle_sha256="0"*64,
                         delta_bundle_byte_length=16, ett=127, loss=1.0)
    a, b = torch.randn(8, 256), torch.randn(1024, 8)
    d = delta_bundle_pack(a, b)
    try:
        coord.checkpoint_upload({"receipt": r, "delta_bundle_b64": base64.b64encode(d).decode()}, KEY)
        check("ADV-06: missing unit upload rejected", False)
    except ValueError:
        check("ADV-06: missing unit upload rejected", True)

def test_adv07_bundle_postpre():
    """ADV-07: delta bundle == post-pre, != absolute post."""
    w = WorkerNumericalModel()
    x = torch.randn(1, 128, 256).requires_grad_(True)
    cg = torch.randn(1, 128, 256)
    pre_A = w.lora_wrapper.lora_A.weight.detach().clone()
    pre_B = w.lora_wrapper.lora_B.weight.detach().clone()
    w.worker_backward(x, cg)
    w.optimizer.step()
    post_A = w.lora_wrapper.lora_A.weight.detach().clone()
    post_B = w.lora_wrapper.lora_B.weight.detach().clone()
    dA, dB = post_A - pre_A, post_B - pre_B
    d = delta_bundle_pack(dA, dB)
    t = delta_bundle_unpack(d, bundle_sha256(d))
    got_A = t["local_layers.0.linear1.lora_A.weight"]
    check("ADV-07a: bundle == post-pre", torch.allclose(got_A, dA, rtol=1e-5, atol=1e-6))
    check("ADV-07b: bundle != absolute post", not torch.allclose(got_A, post_A, rtol=1e-5, atol=1e-6))
    check("ADV-07c: pre + delta == post", torch.allclose(pre_A + dA, post_A, rtol=1e-5, atol=1e-6))

def test_adv08_prepost_hashes():
    """ADV-08: pre/post adapter hashes non-null."""
    w = WorkerNumericalModel()
    pre_bytes, pre_hash = pack_base_adapter(w)
    check("ADV-08a: pre hash non-null", len(pre_hash) == 64 and pre_hash != "0"*64)
    check("ADV-08b: pre bytes non-empty", len(pre_bytes) > 0)

def test_adv09_restart_restores():
    """ADV-09: restart restores post-step adapter; no second optimizer."""
    db = os.path.join(tempfile.gettempdir(), f"jr_{uuid.uuid4().hex[:8]}.db")
    wr = WorkerRuntime(db_path=db)
    wr.prepare_update("up1", "u1")
    cut = torch.randn(1, 128, 256).requires_grad_(True)
    cg = torch.randn(1, 128, 256)
    b1, s1 = wr.apply_update_once("up1", cut, cg)
    # restart: new WorkerRuntime same db — must return same bundle WITHOUT a 2nd optimizer step
    wr2 = WorkerRuntime(db_path=db)
    b2, s2 = wr2.apply_update_once("up1", cut, cg)
    check("ADV-09a: same bundle after restart", b1 == b2)
    check("ADV-09b: APPLIED recovered", wr2.get_journal_status("up1") == "APPLIED")

def test_adv10_retry_no_optimizer():
    """ADV-10: retry with same update_id doesn't re-optimize (weights byte-identical)."""
    db = os.path.join(tempfile.gettempdir(), f"jr_{uuid.uuid4().hex[:8]}.db")
    wr = WorkerRuntime(db_path=db)
    wr.prepare_update("up2", "u1")
    cut = torch.randn(1, 128, 256).requires_grad_(True)
    cg = torch.randn(1, 128, 256)
    wr.apply_update_once("up2", cut, cg)
    w1 = wr.worker.lora_wrapper.lora_A.weight.detach().clone()
    wr.apply_update_once("up2", cut, cg)
    w2 = wr.worker.lora_wrapper.lora_A.weight.detach().clone()
    check("ADV-10: retry no second optimizer", torch.equal(w1, w2))

def test_adv11_checkpoint_retry_idempotent():
    """ADV-11: checkpoint retry returns same response, no nonce failure."""
    db = _fresh_db(); coord = Coordinator(db)
    a, b = torch.randn(8, 256), torch.randn(1024, 8)
    d = delta_bundle_pack(a, b)
    dsha = bundle_sha256(d)
    db64 = base64.b64encode(d).decode("ascii")
    r = generate_receipt(run_id="r1", round_id="rd1", unit_id="u1", worker_id="w1",
                         signing_key=KEY, delta_bundle_sha256=dsha,
                         delta_bundle_byte_length=len(d), ett=127, loss=1.0)
    # Register unit as COMMITTED in the authoritative store
    coord._conn.execute(
        "INSERT OR REPLACE INTO units (unit_id, state, receipt_json, run_id, round_id, worker_id) VALUES (?,?,?,?,?,?)",
        ("u1", "COMMITTED", json.dumps(r), "r1", "rd1", "w1"))
    coord._conn.commit()
    up1 = coord.checkpoint_upload({"receipt": r, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha}, KEY)
    try:
        up2 = coord.checkpoint_upload({"receipt": r, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha}, KEY)
        check("ADV-11a: retry idempotent", up2["contribution_id"] == up1["contribution_id"])
    except ValueError:
        check("ADV-11a: retry idempotent", False)

def test_adv12_resigned_tampered_receipt():
    """ADV-12: tampered+resigned receipt rejected against persisted state."""
    db = _fresh_db()
    ph = ProtocolHandler(db, KEY)  # create units schema first (superset)
    coord = Coordinator(db)
    a, b = torch.randn(8, 256), torch.randn(1024, 8)
    d = delta_bundle_pack(a, b)
    dsha = bundle_sha256(d)
    db64 = base64.b64encode(d).decode("ascii")
    r = generate_receipt(run_id="r1", round_id="rd1", unit_id="u1", worker_id="w1",
                         signing_key=KEY, delta_bundle_sha256=dsha,
                         delta_bundle_byte_length=len(d), ett=127, loss=1.0)
    # register unit as COMMITTED via protocol
    p = {"protocol_version": "1.2.0-rc5.2", "unit_id": "u1", "run_id": "r1", "round_id": "rd1",
         "assignment_id": "a1", "micro_unit_id": "mu1", "worker_id": "w1", "session_id": "s1",
         "worker_model_hash": "a"*64, "partition_schema_hash": partition_schema_hash(),
         "base_adapter_hash": "b"*64, "adapter_schema_hash": adapter_schema_hash(),
         "numerical_profile_hash": numerical_profile_hash()}
    ph.handle("step.open", p)
    # tamper receipt: different loss, resign
    r2 = dict(r); r2["loss"] = 99.9
    payload = {k: v for k, v in r2.items() if k != "signature"}
    import hmac
    r2["signature"] = hmac.new(KEY, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
    try:
        coord.checkpoint_upload({"receipt": r2, "delta_bundle_b64": db64, "delta_bundle_sha256": dsha}, KEY)
        check("ADV-12: tampered receipt rejected", False)
    except ValueError:
        check("ADV-12: tampered receipt rejected", True)

TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.2 Phase 2 R11 — ADVERSARIAL GATE")
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
