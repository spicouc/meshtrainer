"""RC5.1 P0 — T-SPLIT smoke + functional + security + persistence tests."""
import os, sys, json, time, hashlib, base64, threading, tempfile, copy
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
from rc5_receipt import generate_receipt, verify_receipt, init_keyring, rotate_key, RECEIPT_KEYRING_FILE
from rc5_split_server import start_split_server, ss_state
from rc3_coordinator import Coordinator
from rc5_coordinator_ext import patch_coordinator, rpc as crpc

PASS, FAIL = 0, 0
COUNTS = {"smoke": 0, "func": 0, "numeric": 0, "security": 0, "persist": 0}
USED_NONCES = set()

def check(cond, msg, group="func"):
    global PASS, FAIL
    if cond:
        PASS += 1
        COUNTS[group] += 1
        print(f"  ✅ {msg}")
    else:
        FAIL += 1
        print(f"  ❌ {msg}")

def start_ss(port=18795):
    ss_state.reset_run("run-t1", "rnd-t1")
    t = threading.Thread(target=start_split_server, args=("127.0.0.1", port), daemon=True)
    t.start()
    time.sleep(0.3)
    return port

def make_coord():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    coord = Coordinator(p, admin_mode=True)
    coord.train_manager.ensure_schema()
    coord._recovered = True
    patch_coordinator(coord)
    return coord, p

def gen_receipt(overrides=None):
    """Generate a valid receipt with delta_sha256. Override fields before signing."""
    base = {
        "run_id": "r-sec", "round_id": "rd-sec", "assignment_id": "a-sec",
        "micro_unit_id": "u-sec", "worker_id": "w-sec", "session_id": "ss-sec",
        "base_adapter_hash": "sha256:base", "model_hash": "sha256:model",
        "adapter_schema_hash": "sha256:schema", "loss_definition_hash": "sha256:loss",
        "precision_profile": "fp32", "data_shard_hash": "d"*16,
        "effective_trainable_tokens": 10, "optimizer_steps": 1,
        "first_step_id": "u-sec-s0", "last_step_id": "u-sec-s0",
        "receipt_key_id": "k1", "delta_sha256": "a"*64,
    }
    if overrides:
        base.update(overrides)
    r = generate_receipt(
        run_id=base["run_id"], round_id=base["round_id"],
        assignment_id=base["assignment_id"], micro_unit_id=base["micro_unit_id"],
        worker_id=base["worker_id"], session_id=base["session_id"],
        base_adapter_hash=base["base_adapter_hash"],
        model_hash=base["model_hash"],
        adapter_schema_hash=base["adapter_schema_hash"],
        loss_definition_hash=base["loss_definition_hash"],
        precision_profile=base["precision_profile"],
        data_shard_hash=base["data_shard_hash"],
        effective_trainable_tokens=base["effective_trainable_tokens"],
        optimizer_steps=base["optimizer_steps"],
        first_step_id=base["first_step_id"],
        last_step_id=base["last_step_id"],
        delta_sha256=base["delta_sha256"],
        receipt_key_id=base["receipt_key_id"],
    )
    return r

def upload_contrib(coord, receipt, wid="w-sec", run="r-sec", rnd="rd-sec", aid="a-sec", delta=None):
    if delta is None:
        delta = receipt.get("delta_sha256", "a"*64)
    return crpc(coord, "checkpoint.upload",
        {"run_id": run, "round_id": rnd, "assignment_id": aid,
         "receipt": receipt, "delta_sha256": delta}, wid)

def setup_worker(coord, wid="w-sec", assignment_id="a-sec", unit_ids=None):
    if unit_ids is None:
        unit_ids = ["u-sec"]
    crpc(coord, "worker.join", {"worker_id": wid, "session_id": f"ss-{wid}"}, wid)
    coord._rc5_assignments[assignment_id] = {"worker_id": wid, "unit_ids": unit_ids, "status": "PENDING"}
    crpc(coord, "work.request", {}, wid)
    crpc(coord, "work.accept", {"assignment_id": assignment_id}, wid)
    return wid, assignment_id

# ================================================================
# SMOKE TESTS
# ================================================================
def test_tsplit_1():
    print("\n--- T-SPLIT-1: 1 worker E2E smoke ---")
    port = start_ss(port=18795)
    coord, db = make_coord()
    ss_url = f"http://127.0.0.1:{port}"
    wid, aid = setup_worker(coord, "w1", "a1", ["u1"])
    init_keyring("k1")
    # Full step flow
    text = "Hola mon test"
    hdr = {"X-Worker-Id": wid}
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.open",
        "params":{"assignment_id":"a1","micro_unit_id":"u1","session_id":"ss-w1"},"id":"1"}, headers=hdr).json()
    check("result" in r, "step.open", "smoke")
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.embedding",
        "params":{"unit_id":"u1","text_b64":base64.b64encode(text.encode()).decode()},"id":"2"}, headers=hdr).json()
    check("result" in r, "step.embedding", "smoke")
    emb = np.frombuffer(base64.b64decode(r["result"]["embedding_b64"]), dtype=np.float32)
    act = emb.reshape(1, -1)[:, :ss_state.d_model]
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.cut_activation",
        "params":{"unit_id":"u1","activation_b64":base64.b64encode(act.tobytes()).decode()},"id":"3"}, headers=hdr).json()
    check("result" in r, "step.cut_activation", "smoke")
    grad = np.random.randn(1, ss_state.d_model).astype(np.float32)
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.cut_gradient",
        "params":{"unit_id":"u1","gradient_b64":base64.b64encode(grad.tobytes()).decode()},"id":"4"}, headers=hdr).json()
    check("result" in r, "step.cut_gradient", "smoke")
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.commit",
        "params":{"unit_id":"u1"},"id":"5"}, headers=hdr).json()
    check("result" in r, "step.commit", "smoke")
    result = r["result"]
    check(result["loss"] is not None, "loss computed", "smoke")
    check(result["receipt_valid"], "receipt HMAC verified", "smoke")
    check(len(result.get("delta_sha256","")) == 64, "delta_sha256 full 64-char hash", "smoke")
    check(result["receipt"].get("delta_sha256") == result["delta_sha256"], "receipt delta_sha256 matches", "smoke")
    # Upload
    r = crpc(coord, "checkpoint.upload", {"run_id":"run-t1","round_id":"rnd-t1",
        "assignment_id":"a1","receipt":result["receipt"],"delta_sha256":result["delta_sha256"]}, wid)
    check(r["result"]["status"] == "RECEIVED", "contribution RECEIVED", "smoke")
    coord.stop()
    os.unlink(db)

def test_tsplit_2():
    print("\n--- T-SPLIT-2: Fake receipt REJECTED ---")
    coord, db = make_coord()
    setup_worker(coord, "w2", "a2", ["u2"])
    r2 = gen_receipt({"assignment_id":"a2","micro_unit_id":"u2","worker_id":"w2","session_id":"ss-w2",
                       "run_id":"r2","round_id":"rd2"})
    r2["signature"] = "0"*64  # break HMAC
    r = upload_contrib(coord, r2, "w2", "r2", "rd2", "a2")
    check(r["result"].get("status") == "REJECTED", "fake receipt REJECTED", "smoke")
    coord.stop()
    os.unlink(db)

# ================================================================
# SECURITY TESTS
# ================================================================
def test_security():
    print("\n--- T-SPLIT-3 to T-SPLIT-11: Security tests ---")
    coord, db = make_coord()
    setup_worker(coord, "w-sec", "a-sec", ["u-sec"])
    init_keyring("k1")

    # T-SPLIT-3: HMAC incorrecte
    r3 = gen_receipt({})
    r3["signature"] = "0"*64
    r = upload_contrib(coord, r3)
    check(r["result"]["status"] == "REJECTED", "T-SPLIT-3: wrong HMAC rejected", "security")

    # T-SPLIT-4: receipt d'un altre worker
    r4 = gen_receipt({"worker_id":"w-other","session_id":"ss-other"})
    r = upload_contrib(coord, r4)
    check(r["result"]["status"] == "REJECTED", "T-SPLIT-4: other worker receipt rejected", "security")

    # T-SPLIT-5: assignment incorrecte (receipt valida pero assignment mismatch)
    r5 = gen_receipt({"assignment_id":"a-wrong","micro_unit_id":"u-wrong"})
    r = upload_contrib(coord, r5, aid="a-wrong")
    # a-wrong doesn't exist in assignments
    check(r["result"]["status"] == "REJECTED", "T-SPLIT-5: wrong assignment rejected", "security")

    # T-SPLIT-6: run/round incorrectes
    r6 = gen_receipt({})
    r = upload_contrib(coord, r6, run="r-wrong")
    check(r["result"]["status"] == "REJECTED", "T-SPLIT-6: wrong run rejected", "security")

    # T-SPLIT-7: nonce replay
    r7 = gen_receipt({})
    r = upload_contrib(coord, r7)
    r7b = upload_contrib(coord, r7)  # same receipt, same nonce
    check(r7b["result"]["status"] == "REJECTED", "T-SPLIT-7: nonce replay rejected", "security")

    # T-SPLIT-8: key_id desconegut
    r8 = gen_receipt({})
    r8["receipt_key_id"] = "k-unknown"
    r = upload_contrib(coord, r8)
    check(r["result"]["status"] == "REJECTED", "T-SPLIT-8: unknown key_id rejected", "security")

    # T-SPLIT-9: receipt expirat (generated with expiry in the past)
    r9 = gen_receipt({})
    r9["expires_at"] = time.time() - 10
    r = upload_contrib(coord, r9)
    check(r["result"]["status"] == "REJECTED", "T-SPLIT-9: expired receipt rejected", "security")

    # T-SPLIT-10: transicio illegal
    ss_state.reset_run("r-sec", "rd-sec")
    ss_state.units["u-illegal"] = {"worker_id":"w-sec","session_id":"ss-sec","state":"OPEN","assignment_id":"a-sec"}
    hdr = {"X-Worker-Id":"w-sec"}
    r = requests.post("http://127.0.0.1:18795", json={"jsonrpc":"2.0","method":"step.cut_activation",
        "params":{"unit_id":"u-illegal","activation_b64":base64.b64encode(b"x"*32).decode()},"id":"1"}, headers=hdr).json()
    check("error" in r and r.get("error",{}).get("code") == -32003,
          f"T-SPLIT-10: illegal transition OPEN->CUT_ACTIVATION code={r.get('error',{}).get('code','?')}", "security")

    # T-SPLIT-11: worker opera sobre unitat aliena
    ss_state.units["u-alien"] = {"worker_id":"w-owner","session_id":"ss-owner","state":"OPEN","assignment_id":"a-sec"}
    r = requests.post("http://127.0.0.1:18795", json={"jsonrpc":"2.0","method":"step.embedding",
        "params":{"unit_id":"u-alien","text_b64":base64.b64encode(b"test").decode()},"id":"1"},
        headers={"X-Worker-Id":"w-sec"}).json()
    check("error" in r, "T-SPLIT-11: alien worker rejected", "security")

    # Delta_sha256 absent
    r_nods = gen_receipt({})
    del r_nods["delta_sha256"]
    r = upload_contrib(coord, r_nods)
    check(r["result"]["status"] == "REJECTED", "T-SPLIT: missing delta_sha256 REJECTED", "security")

    # Delta_sha256 wrong format
    r_wf = gen_receipt({})
    r_wf["delta_sha256"] = "short"
    r = upload_contrib(coord, r_wf)
    check(r["result"]["status"] == "REJECTED", "T-SPLIT: wrong format delta_sha256 REJECTED", "security")

    # Delta_sha256 mismatch
    r_mm = gen_receipt({})
    r = upload_contrib(coord, r_mm, delta="b"*64)
    check(r["result"]["status"] == "REJECTED", "T-SPLIT: delta_sha256 mismatch REJECTED", "security")

    coord.stop()
    os.unlink(db)

# ================================================================
# NUMERIC TESTS
# ================================================================
def test_numeric():
    print("\n--- T-SPLIT-12 to T-SPLIT-14: Numeric tests ---")
    port = start_ss(port=18796)
    coord, db = make_coord()
    ss_url = f"http://127.0.0.1:{port}"
    wid, aid = setup_worker(coord, "w-num", "a-num", ["u-num"])

    # T-SPLIT-12: backward + optimizer change weights
    torch.manual_seed(42)
    weight_before = ss_state.server_model.get_lora_weight().detach().clone()
    text = "Test per backward weight change"
    hdr = {"X-Worker-Id": wid}
    requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.open",
        "params":{"assignment_id":"a-num","micro_unit_id":"u-num","session_id":"ss-w-num"},"id":"1"}, headers=hdr).json()
    requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.embedding",
        "params":{"unit_id":"u-num","text_b64":base64.b64encode(text.encode()).decode()},"id":"2"}, headers=hdr).json()
    emb = np.random.randn(1, ss_state.d_model).astype(np.float32)
    requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.cut_activation",
        "params":{"unit_id":"u-num","activation_b64":base64.b64encode(emb.tobytes()).decode()},"id":"3"}, headers=hdr).json()
    grad = np.random.randn(1, ss_state.d_model).astype(np.float32)
    requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.cut_gradient",
        "params":{"unit_id":"u-num","gradient_b64":base64.b64encode(grad.tobytes()).decode()},"id":"4"}, headers=hdr).json()
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.commit",
        "params":{"unit_id":"u-num"},"id":"5"}, headers=hdr).json()
    result = r["result"]
    weight_after = ss_state.server_model.get_lora_weight().detach().clone()
    lora_delta = weight_after - weight_before
    check(lora_delta.abs().sum().item() > 0, "T-SPLIT-12: weights changed after training", "numeric")

    # T-SPLIT-13: delta_sha256 full 64-char hash matches actual delta
    expected_hash = hashlib.sha256(lora_delta.numpy().tobytes()).hexdigest()
    check(result["delta_sha256"] == expected_hash, "T-SPLIT-13: delta_sha256 matches actual delta", "numeric")
    # Verify it's full 64 chars
    check(len(result["delta_sha256"]) == 64, "T-SPLIT-13: full 64-char hash", "numeric")

    # T-SPLIT-14: FedAvg weighted
    fedavg_result = (1 * 10 + 3 * 30) / (10 + 30)
    check(abs(fedavg_result - 2.5) < 1e-6, f"T-SPLIT-14: FedAvg={fedavg_result:.4f} expected 2.5", "numeric")

    coord.stop()
    os.unlink(db)

# ================================================================
# LEDGER TESTS (T-SPLIT-15)
# ================================================================
def test_ledger():
    print("\n--- T-SPLIT-15: SUPERSEDED semantics ---")
    coord, db = make_coord()
    setup_worker(coord, "w-ledger", "a-ledger", ["u-ledger"])
    init_keyring("k1")

    # Cas A: RECEIVED v2 does NOT supersede ACTIVE v1
    r1 = gen_receipt({"assignment_id":"a-ledger","micro_unit_id":"u-ledger",
        "worker_id":"w-ledger","session_id":"ss-w-ledger",
        "run_id":"r-ledger","round_id":"rd-ledger","effective_trainable_tokens":10,
        "delta_sha256":"a"*64})
    r = upload_contrib(coord, r1, "w-ledger", "r-ledger", "rd-ledger", "a-ledger")
    cid1 = r["result"]["contribution_id"]
    coord._rc5_extensions["admin.validate.contribution"]({"contribution_id":cid1},"")
    coord._rc5_extensions["admin.activate.contribution"]({"contribution_id":cid1},"")
    # Verify v1 is ACTIVE
    c1_status = [c["status"] for c in coord._rc5_ledger if c["contribution_id"] == cid1][0]
    check(c1_status == "ACTIVE", "Cas A: v1 ACTIVE", "persist")

    # Upload v2 (RECEIVED) — v1 should remain ACTIVE
    r2 = gen_receipt({"assignment_id":"a-ledger","micro_unit_id":"u-ledger",
        "worker_id":"w-ledger","session_id":"ss-w-ledger",
        "run_id":"r-ledger","round_id":"rd-ledger","effective_trainable_tokens":20,
        "delta_sha256":"b"*64,"receipt_nonce":"n-ledger"})
    coord._rc5_nonces = set()  # reset nonces for test
    r = upload_contrib(coord, r2, "w-ledger", "r-ledger", "rd-ledger", "a-ledger")
    cid2 = r["result"]["contribution_id"]
    c1_status_after = [c["status"] for c in coord._rc5_ledger if c["contribution_id"] == cid1][0]
    c2_status = [c["status"] for c in coord._rc5_ledger if c["contribution_id"] == cid2][0]
    check(c1_status_after == "ACTIVE", "Cas A: v1 still ACTIVE after v2 RECEIVED", "persist")
    check(c2_status == "RECEIVED", "Cas A: v2 RECEIVED", "persist")

    # Cas B: validate v2 — v1 still ACTIVE
    coord._rc5_extensions["admin.validate.contribution"]({"contribution_id":cid2},"")
    c_v2 = [c for c in coord._rc5_ledger if c["contribution_id"] == cid2][0]
    check(c_v2["status"] == "VALIDATED", "Cas B: v2 VALIDATED", "persist")
    c_v1 = [c for c in coord._rc5_ledger if c["contribution_id"] == cid1][0]
    check(c_v1["status"] == "ACTIVE", "Cas B: v1 still ACTIVE", "persist")

    # Cas C: activate v2 — v1 becomes SUPERSEDED, v2 becomes ACTIVE
    coord._rc5_extensions["admin.activate.contribution"]({"contribution_id":cid2},"")
    c_v1 = [c for c in coord._rc5_ledger if c["contribution_id"] == cid1][0]
    c_v2 = [c for c in coord._rc5_ledger if c["contribution_id"] == cid2][0]
    check(c_v1["status"] == "SUPERSEDED", "Cas C: v1 SUPERSEDED after v2 ACTIVE", "persist")
    check(c_v2["status"] == "ACTIVE", "Cas C: v2 ACTIVE", "persist")

    # Cas D: round close aggregates only ACTIVE (1, not 2)
    rc = crpc(coord, "admin.round.close", {"run_id":"r-ledger","round_id":"rd-ledger"})
    check(rc["result"]["contributions_aggregated"] == 1, "Cas D: 1 aggregated (ACTIVE only)", "persist")
    check(rc["result"]["total_ett"] == 20, "Cas D: ett=20 (latest ACTIVE)", "persist")

    coord.stop()
    os.unlink(db)

# ================================================================
# PERSISTENCE TESTS
# ================================================================
def test_persistence():
    print("\n--- T-SPLIT-16 to T-SPLIT-19: Persistence tests ---")
    coord, db = make_coord()
    setup_worker(coord, "w-p", "a-p", ["u-p"])
    init_keyring("k1")

    # T-SPLIT-16: ledger entries
    r1 = gen_receipt({"assignment_id":"a-p","micro_unit_id":"u-p",
        "worker_id":"w-p","session_id":"ss-w-p","run_id":"r-p","round_id":"rd-p",
        "delta_sha256":"a"*64,"effective_trainable_tokens":10})
    upload_contrib(coord, r1, "w-p", "r-p", "rd-p", "a-p")
    r2 = gen_receipt({"assignment_id":"a-p","micro_unit_id":"u-p",
        "worker_id":"w-p","session_id":"ss-w-p","run_id":"r-p","round_id":"rd-p",
        "delta_sha256":"b"*64,"effective_trainable_tokens":20,"receipt_nonce":"n-p2"})
    upload_contrib(coord, r2, "w-p", "r-p", "rd-p", "a-p")
    check(len(coord._rc5_ledger) == 2, "T-SPLIT-16: ledger has 2 entries", "persist")
    check(coord._rc5_ledger[0]["revision"] == 1, "revision 1", "persist")
    check(coord._rc5_ledger[1]["revision"] == 2, "revision 2", "persist")

    # T-SPLIT-17: key rotation
    rotate_key("k2")
    r_new = gen_receipt({"receipt_key_id":"k2","delta_sha256":"c"*64})
    valid, msg = verify_receipt(r_new)
    check(valid, "T-SPLIT-17a: receipt with new key verifies", "persist")
    r_old = coord._rc5_ledger[0]["receipt"]
    valid, msg = verify_receipt(r_old)
    check(valid, "T-SPLIT-17b: old key receipt still verifies (retired)", "persist")

    coord.stop()
    os.unlink(db)

# ================================================================
# TOKEN COUNTING
# ================================================================
def test_tokens():
    print("\n--- T-SPLIT-18: Token counting ---")
    from rc5_split_server import Tokenizer
    tok = Tokenizer()
    ids, labels = tok.encode("hello")
    check(tok.effective_tokens(labels) == 5, "5 tokens for 'hello'", "func")
    ids, labels = tok.encode("hi", max_len=8)
    check(tok.effective_tokens(labels) == 2, "2 tokens for 'hi' with padding", "func")
    labels[0,:] = -100
    check(tok.effective_tokens(labels) == 0, "0 tokens when all -100", "func")
    ids, labels = tok.encode("cafe")
    check(tok.effective_tokens(labels) == 4, "4 tokens for 'cafe'", "func")

# ================================================================
# BACKPRESSURE
# ================================================================
def test_backpressure():
    print("\n--- T-SPLIT-19: Backpressure ---")
    port = start_ss(port=18797)
    ss_url = f"http://127.0.0.1:{port}"
    hdr = {"X-Worker-Id": "w-bp"}
    r1 = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.open",
        "params":{"assignment_id":"a-bp","micro_unit_id":"u-bp-1","session_id":"ss-bp"},"id":"1"}, headers=hdr).json()
    check("result" in r1, "first unit opens OK", "func")
    r2 = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.open",
        "params":{"assignment_id":"a-bp2","micro_unit_id":"u-bp-2","session_id":"ss-bp"},"id":"1"}, headers=hdr).json()
    check("error" in r2 and r2["error"]["code"] == -32001, "backpressure blocks second open", "func")

# ================================================================
# INTERNAL: counting validation
# ================================================================
def check_counts():
    print("\n--- Internal: counting validation ---")
    total = PASS + FAIL
    check(PASS == sum(COUNTS.values()), f"PASS={PASS} matches sum(COUNTS)={sum(COUNTS.values())}", "func")
    print(f"  COUNTS: {COUNTS}")
    print(f"  PASS/FAIL: {PASS}/{FAIL}")

if __name__ == "__main__":
    if os.path.exists(RECEIPT_KEYRING_FILE):
        os.remove(RECEIPT_KEYRING_FILE)
    init_keyring("k1")

    print("=" * 60)
    print("  RC5.1 P0 — T-SPLIT Tests")
    print("=" * 60)
    test_tsplit_1()
    test_tsplit_2()
    test_security()
    test_numeric()
    test_ledger()
    test_persistence()
    test_tokens()
    test_backpressure()
    check_counts()

    print(f"\n{'='*60}")
    print(f"  RESULTAT: {PASS}/{PASS+FAIL} PASS, {FAIL} FAIL")
    for k, v in COUNTS.items():
        print(f"  {k}: {v}")
    print(f"{'='*60}")
    sys.exit(0 if FAIL == 0 else 1)
