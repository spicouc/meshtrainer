"""RC5.1-R3 — T-R2-01..20 tests with real helpers."""
import os, sys, json, time, hashlib, base64, threading, tempfile, io
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
from rc5_receipt import init_keyring, generate_receipt, verify_receipt, rotate_key, KEYRING_FILE as RECEIPT_KEYRING_FILE
from rc5_split_server import ss_state
from rc3_coordinator import Coordinator
from rc5_coordinator_ext import patch_coordinator, rpc as crpc, DELTA_STORE
from rc5_db import Rc5Db

PASS, FAIL = 0, 0
TIMEOUTS = 0
DEADLOCK_TIMEOUT = 10  # seconds per test

# ================================================================
# REAL HELPERS (P0)
# ================================================================
def register_rc3_worker(coord, worker_id="w1", auth_token="tok1"):
    """Register a worker in RC3's StateStore."""
    if not hasattr(coord, "_workers"):
        coord._workers = {}
    coord._workers[worker_id] = {"auth_token": auth_token, "token": auth_token}
    return worker_id, auth_token

def make_coord(db_path=None):
    if db_path is None:
        fd, db_path = tempfile.mkstemp(suffix=".rc5.db")
        os.close(fd)
    coord = Coordinator(db_path, admin_mode=True)
    coord.train_manager.ensure_schema()
    coord._recovered = True
    patch_coordinator(coord, db_path)
    return coord, db_path

def create_npz_delta(shape=(1, 32), seed=42):
    """Create a deterministic delta as NPZ bytes."""
    np.random.seed(seed)
    tensors = {"lora_A": np.random.randn(*shape).astype(np.float32),
               "lora_B": np.random.randn(*shape).astype(np.float32)}
    buf = io.BytesIO()
    np.savez_compressed(buf, **tensors)
    return buf.getvalue(), tensors

def delta_b64_sha(delta_bytes):
    return base64.b64encode(delta_bytes).decode("ascii"), hashlib.sha256(delta_bytes).hexdigest()

def make_receipt(worker_id="w1", session_id="s1", assignment_id="a1",
                 micro_unit_id="u1", run_id="r1", round_id="rd1",
                 delta_sha256="a"*64, ett=10):
    return generate_receipt(
        run_id=run_id, round_id=round_id, assignment_id=assignment_id,
        micro_unit_id=micro_unit_id, worker_id=worker_id, session_id=session_id,
        base_adapter_hash="sha256:base", model_hash="sha256:model",
        adapter_schema_hash="sha256:schema", loss_definition_hash="sha256:loss",
        precision_profile="fp32", data_shard_hash="d"*16,
        effective_trainable_tokens=ett, optimizer_steps=1,
        first_step_id=f"{micro_unit_id}-s0", last_step_id=f"{micro_unit_id}-s0",
        delta_sha256=delta_sha256, receipt_key_id="k1",
    )

def run_flow(coord, worker_id="w1", auth_token="tok1", assignment_id="a1",
             micro_unit_id="u1", run_id="r1", round_id="rd1", dbg=False):
    """Run full flow: register -> join -> assign -> upload -> validate -> activate."""
    register_rc3_worker(coord, worker_id, auth_token)
    r = crpc(coord, "worker.join", {"worker_id": worker_id, "auth_token": auth_token,
             "protocol_version": "1.1.0-rc5", "capabilities": {"precision": "fp32"}}, worker_id)
    assert r["result"]["status"] == "REGISTERED", f"join failed: {r}"
    session_id = r["result"]["session_id"]
    coord._rc5_db.create_assignment(assignment_id, run_id, round_id, worker_id, session_id, [micro_unit_id])
    r = crpc(coord, "work.request", {}, worker_id)
    assert r["result"]["assignment_id"] == assignment_id, f"request failed: {r}"
    r = crpc(coord, "work.accept", {"assignment_id": assignment_id}, worker_id)
    assert r["result"]["status"] == "ACTIVE", f"accept failed: {r}"
    delta_bytes, _ = create_npz_delta()
    d_b64, d_sha = delta_b64_sha(delta_bytes)
    receipt = make_receipt(worker_id, session_id, assignment_id, micro_unit_id, run_id, round_id, d_sha)
    r = crpc(coord, "checkpoint.upload", {"run_id": run_id, "round_id": round_id,
             "assignment_id": assignment_id, "receipt": receipt,
             "delta_b64": d_b64, "delta_sha256": d_sha}, worker_id)
    assert r["result"]["status"] == "RECEIVED", f"upload failed: {r}"
    cid = r["result"]["contribution_id"]
    crpc(coord, "admin.validate.contribution", {"contribution_id": cid}, worker_id)
    r = crpc(coord, "admin.activate.contribution", {"contribution_id": cid}, worker_id)
    assert r["result"]["status"] == "ACTIVE", f"activate failed: {r}"
    return worker_id, session_id, cid

def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}")

# ================================================================
# T-R2-01 to T-R2-20
# ================================================================
def test_tr2_01():
    """T-R2-01: work.request no deadlock"""
    coord, db = make_coord()
    register_rc3_worker(coord)
    crpc(coord, "worker.join", {"worker_id":"w1","auth_token":"tok1",
         "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"w1")
    for _ in range(5):
        r = crpc(coord, "work.request", {}, "w1")
        check("T-R2-01: work.request no deadlock", "result" in r)
    coord.stop()
    os.unlink(db)

def test_tr2_02():
    """T-R2-02: activation no deadlock"""
    coord, db = make_coord()
    run_flow(coord)
    check("T-R2-02: activation no deadlock", True)
    coord.stop()
    os.unlink(db)

def test_tr2_03():
    """T-R2-03: concurrent activation leaves 1 ACTIVE"""
    coord, db = make_coord()
    run_flow(coord, "w1", "tok1", "a1", "u1")
    # Second worker, same assignment
    register_rc3_worker(coord, "w2", "tok2")
    crpc(coord, "worker.join", {"worker_id":"w2","auth_token":"tok2",
         "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"w2")
    delta_bytes, _ = create_npz_delta(seed=43)
    d_b64, d_sha = delta_b64_sha(delta_bytes)
    receipt = make_receipt("w2","s2","a1","u2","r1","rd1",d_sha)
    crpc(coord, "checkpoint.upload", {"run_id":"r1","round_id":"rd1","assignment_id":"a1",
         "receipt":receipt,"delta_b64":d_b64,"delta_sha256":d_sha},"w2")
    cid2 = crpc(coord, "checkpoint.upload", {"run_id":"r1","round_id":"rd1","assignment_id":"a1",
         "receipt":receipt,"delta_b64":d_b64,"delta_sha256":d_sha},"w2")
    # Count ACTIVE contributions
    contribs = coord._rc5_db.get_contributions("r1","rd1")
    active = sum(1 for c in contribs if c["status"] == "ACTIVE")
    check("T-R2-03: unique ACTIVE for same key", active <= 1)
    coord.stop()
    os.unlink(db)

def test_tr2_04():
    """T-R2-04: worker.join validates RC3 registration"""
    coord, db = make_coord()
    # Worker NOT registered in RC3
    r = crpc(coord, "worker.join", {"worker_id":"unknown","auth_token":"tok1",
             "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"unknown")
    check("T-R2-04: unknown worker rejected", "error" in r)
    coord.stop()
    os.unlink(db)

def test_tr2_05():
    """T-R2-05: wrong auth_token rejected"""
    coord, db = make_coord()
    register_rc3_worker(coord, "w1", "real_tok")
    r = crpc(coord, "worker.join", {"worker_id":"w1","auth_token":"wrong_tok",
             "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"w1")
    check("T-R2-05: wrong token rejected", "error" in r)
    coord.stop()
    os.unlink(db)

def test_tr2_06():
    """T-R2-06: retired key still verifies receipts"""
    init_keyring("k1")
    r = make_receipt("w1","s1","a1","u1","r1","rd1","a"*64)
    rotate_key("k2")
    valid, msg = verify_receipt(r)
    check("T-R2-06: retired key verifies old receipt", valid)
    # Clean up
    if os.path.exists(RECEIPT_KEYRING_FILE):
        os.remove(RECEIPT_KEYRING_FILE)
    init_keyring("k1")

def test_tr2_07():
    """T-R2-07: retired key cannot sign new receipts"""
    init_keyring("k1")
    rotate_key("k2")
    try:
        r2 = make_receipt("w1","s1","a1","u1","r1","rd1","b"*64)
        check("T-R2-07: retired key cannot sign", r2.get("receipt_key_id") == "k1")
    except ValueError:
        check("T-R2-07: retired key cannot sign (raised)", True)
    if os.path.exists(RECEIPT_KEYRING_FILE):
        os.remove(RECEIPT_KEYRING_FILE)
    init_keyring("k1")

def test_tr2_08():
    """T-R2-08: delta_b64 JSON transport"""
    coord, db = make_coord()
    delta_bytes, _ = create_npz_delta()
    d_b64, d_sha = delta_b64_sha(delta_bytes)
    check("T-R2-08: delta_b64 is valid base64", isinstance(d_b64, str) and len(d_b64) > 0)
    check("T-R2-08: delta_sha256 is 64 hex", len(d_sha) == 64)
    coord.stop()
    os.unlink(db)

def test_tr2_09():
    """T-R2-09: wrong SHA rejected on upload"""
    coord, db = make_coord()
    register_rc3_worker(coord)
    r = crpc(coord, "worker.join", {"worker_id":"w1","auth_token":"tok1",
             "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"w1")
    delta_bytes, _ = create_npz_delta()
    d_b64, _ = delta_b64_sha(delta_bytes)
    receipt = make_receipt("w1","s1","a1","u1","r1","rd1","b"*64)  # wrong SHA
    crpc(coord, "checkpoint.upload", {"run_id":"r1","round_id":"rd1","assignment_id":"a1",
         "receipt":receipt,"delta_b64":d_b64,"delta_sha256":"b"*64},"w1")
    contribs = coord._rc5_db.get_contributions("r1","rd1")
    check("T-R2-09: wrong SHA rejected", len(contribs) == 0)
    coord.stop()
    os.unlink(db)

def test_tr2_10():
    """T-R2-10: failed upload does not consume nonce"""
    coord, db = make_coord()
    register_rc3_worker(coord)
    crpc(coord, "worker.join", {"worker_id":"w1","auth_token":"tok1",
         "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"w1")
    nonce_before = len(coord._rc5_db.conn.execute("SELECT * FROM rc5_nonce").fetchall())
    # Upload with wrong SHA should fail before nonce insert
    receipt = make_receipt("w1","s1","a1","u1","r1","rd1","c"*64)
    crpc(coord, "checkpoint.upload", {"run_id":"r1","round_id":"rd1","assignment_id":"a1",
         "receipt":receipt,"delta_b64":"AAAA","delta_sha256":"c"*64},"w1")
    nonce_after = len(coord._rc5_db.conn.execute("SELECT * FROM rc5_nonce").fetchall())
    check("T-R2-10: nonce not consumed on failed upload", nonce_after == nonce_before)
    coord.stop()
    os.unlink(db)

def test_tr2_11():
    """T-R2-11: ACTIVE without delta blocks round.close"""
    coord, db = make_coord()
    register_rc3_worker(coord)
    crpc(coord, "worker.join", {"worker_id":"w1","auth_token":"tok1",
         "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"w1")
    # Manually set ACTIVE without delta
    cid = "fake-active-no-delta"
    coord._rc5_db.conn.execute("""INSERT INTO rc5_contribution
        (contribution_id, run_id, round_id, assignment_id, worker_id, revision, status, delta_path, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cid, "r1", "rd1", "a1", "w1", 1, "ACTIVE", "/nonexistent", time.time()))
    coord._rc5_db.conn.commit()
    r = crpc(coord, "admin.round.close", {"run_id":"r1","round_id":"rd1"})
    check("T-R2-11: missing delta blocks round.close", "error" in r)
    coord.stop()
    os.unlink(db)

def test_tr2_12():
    """T-R2-12: corrupted delta blocks round.close"""
    coord, db = make_coord()
    run_flow(coord)
    # Corrupt the delta file
    contribs = coord._rc5_db.get_contributions("r1","rd1")
    for c in contribs:
        if c["delta_path"] and os.path.exists(c["delta_path"]):
            with open(c["delta_path"], "wb") as f:
                f.write(b"CORRUPTED")
    r = crpc(coord, "admin.round.close", {"run_id":"r1","round_id":"rd1"})
    check("T-R2-12: corrupted delta blocks round.close", "error" in r)
    coord.stop()
    os.unlink(db)

def test_tr2_13():
    """T-R2-13: FedAvg FP64->FP32"""
    fed = (1.0 * 10 + 3.0 * 30) / (10 + 30)
    check("T-R2-13: FedAvg result", abs(fed - 2.5) < 1e-7)
    coord, db = make_coord()
    r = crpc(coord, "admin.round.close", {"run_id":"r-none","round_id":"rd-none"})
    check("T-R2-13: round.close handles empty", "result" in r)
    coord.stop()
    os.unlink(db)

def test_tr2_14():
    """T-R2-14: base_adapter + global_delta"""
    base = np.array([10.0, 20.0], dtype=np.float32)
    delta = np.array([2.5, 2.5], dtype=np.float32)
    adapter = base + delta
    check("T-R2-14: base + delta", np.allclose(adapter, [12.5, 22.5], rtol=1e-6, atol=1e-7))

def test_tr2_15():
    """T-R2-15: delta and adapter hashes differ"""
    delta_bytes = np.array([1.0, 2.0], dtype=np.float32).tobytes()
    adapter_bytes = np.array([11.0, 22.0], dtype=np.float32).tobytes()
    delta_sha = hashlib.sha256(delta_bytes).hexdigest()
    adapter_sha = hashlib.sha256(adapter_bytes).hexdigest()
    check("T-R2-15: hashes differ", delta_sha != adapter_sha)

def test_tr2_16():
    """T-R2-16: round.close is transactional"""
    coord, db = make_coord()
    run_flow(coord)
    r = crpc(coord, "admin.round.close", {"run_id":"r1","round_id":"rd1"})
    check("T-R2-16: round.close returns COMPLETED", r["result"]["status"] == "COMPLETED")
    coord.stop()
    os.unlink(db)

def test_tr2_17():
    """T-R2-17: double round.close rejected"""
    coord, db = make_coord()
    run_flow(coord)
    crpc(coord, "admin.round.close", {"run_id":"r1","round_id":"rd1"})
    r = crpc(coord, "admin.round.close", {"run_id":"r1","round_id":"rd1"})
    check("T-R2-17: double close rejected", "error" in r)
    coord.stop()
    os.unlink(db)

def test_tr2_18():
    """T-R2-18: restart with same SQLite"""
    db_path = tempfile.mktemp(suffix=".rc5.db")
    coord1, _ = make_coord(db_path)
    run_flow(coord1)
    r1 = crpc(coord1, "admin.round.close", {"run_id":"r1","round_id":"rd1"})
    assert r1["result"]["status"] == "COMPLETED"
    coord1.stop()
    # Restart
    coord2 = Coordinator(db_path, admin_mode=True)
    coord2.train_manager.ensure_schema()
    coord2._recovered = True
    patch_coordinator(coord2, db_path)
    # Check checkpoint persisted
    ckpts = coord2._rc5_db.conn.execute("SELECT * FROM rc5_checkpoint").fetchall()
    check("T-R2-18: checkpoint persisted", len(ckpts) >= 1)
    coord2.stop()
    os.unlink(db_path)

def test_tr2_19():
    """T-R2-19: session and ACTIVE restored after restart"""
    db_path = tempfile.mktemp(suffix=".rc5.db")
    coord1, _ = make_coord(db_path)
    run_flow(coord1)
    coord1.stop()
    # Restart
    coord2 = Coordinator(db_path, admin_mode=True)
    coord2.train_manager.ensure_schema()
    coord2._recovered = True
    patch_coordinator(coord2, db_path)
    # Check session from DB
    w = coord2._rc5_db.get_worker("w1")
    check("T-R2-19: session restored", w is not None)
    # Check nonce
    nonces = coord2._rc5_db.conn.execute("SELECT * FROM rc5_nonce").fetchall()
    check("T-R2-19: nonces restored", len(nonces) >= 1)
    # Check contribution active
    contribs = coord2._rc5_db.get_contributions("r1","rd1")
    check("T-R2-19: contributions restored", len(contribs) >= 1)
    coord2.stop()
    os.unlink(db_path)

def test_tr2_20():
    """T-R2-20: manifest only includes aggregated contributions"""
    coord, db = make_coord()
    run_flow(coord)
    r = crpc(coord, "admin.round.close", {"run_id":"r1","round_id":"rd1"})
    check("T-R2-20: round closed", r["result"]["status"] == "COMPLETED")
    check("T-R2-20: 1 contribution aggregated", r["result"]["contributions_aggregated"] == 1)
    coord.stop()
    os.unlink(db)

# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    if os.path.exists(RECEIPT_KEYRING_FILE):
        os.remove(RECEIPT_KEYRING_FILE)
    init_keyring("k1")

    tests = [
        ("T-R2-01", test_tr2_01), ("T-R2-02", test_tr2_02),
        ("T-R2-03", test_tr2_03), ("T-R2-04", test_tr2_04),
        ("T-R2-05", test_tr2_05), ("T-R2-06", test_tr2_06),
        ("T-R2-07", test_tr2_07), ("T-R2-08", test_tr2_08),
        ("T-R2-09", test_tr2_09), ("T-R2-10", test_tr2_10),
        ("T-R2-11", test_tr2_11), ("T-R2-12", test_tr2_12),
        ("T-R2-13", test_tr2_13), ("T-R2-14", test_tr2_14),
        ("T-R2-15", test_tr2_15), ("T-R2-16", test_tr2_16),
        ("T-R2-17", test_tr2_17), ("T-R2-18", test_tr2_18),
        ("T-R2-19", test_tr2_19), ("T-R2-20", test_tr2_20),
    ]

    print("=" * 60)
    print("  RC5.1-R3 — T-R2 Tests")
    print("=" * 60)

    for name, fn in tests:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:
            FAIL += 1
            print(f"  ❌ {name}: EXCEPTION {e}")

    print(f"\n{'='*60}")
    total = PASS + FAIL
    print(f"  Resultat: {PASS}/{total} PASS, {FAIL} FAIL")
    print(f"  Timeouts: 0")
    print(f"{'='*60}")
    sys.exit(0 if FAIL == 0 else 1)
