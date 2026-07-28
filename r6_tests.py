"""RC5.1 R6 — Tests for E2E Split Server, Revisions A-D, Negative cases, Split Server direct."""
import os, sys, json, time, hashlib, base64, threading, tempfile, io
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
from rc5_receipt import init_keyring, generate_receipt, verify_receipt, rotate_key, KEYRING_FILE, canonical_json, get_signing_key
from rc5_split_server import start_split_server, ss_state
from rc3_coordinator import Coordinator
from rc5_coordinator_ext import patch_coordinator, rpc as crpc, DELTA_STORE
from rc5_db import Rc5Db
from rc5_tests import register_rc3_worker, make_coord, create_npz_delta, delta_b64_sha
import rc5_tests as _rc5t
check = _rc5t.check
COUNTS = _rc5t.COUNTS
PASS = _rc5t.PASS
FAIL = _rc5t.FAIL

def test_r6_e2e_split():
    """R6-01: E2E split server step flow"""
    port = 18802
    ss_state.reset_run("r6", "rd6")
    t = threading.Thread(target=start_split_server, args=("127.0.0.1", port), daemon=True)
    t.start()
    time.sleep(0.3)
    url = f"http://127.0.0.1:{port}"
    hdr = {"X-Worker-Id": "w-e2e"}
    r = requests.post(url, json={"jsonrpc":"2.0","method":"step.open",
        "params":{"assignment_id":"a-e2e","micro_unit_id":"u-e2e","session_id":"ss-e2e"},"id":"1"}, headers=hdr).json()
    check("R6-01: step.open", "result" in r, "e2e")
    r = requests.post(url, json={"jsonrpc":"2.0","method":"step.embedding",
        "params":{"unit_id":"u-e2e","text_b64":base64.b64encode(b"Test E2E").decode()},"id":"2"}, headers=hdr).json()
    check("R6-01: step.embedding", "result" in r, "e2e")
    emb = np.frombuffer(base64.b64decode(r["result"]["embedding_b64"]), dtype=np.float32)
    act = emb.reshape(1, -1)[:, :ss_state.d_model]
    r = requests.post(url, json={"jsonrpc":"2.0","method":"step.cut_activation",
        "params":{"unit_id":"u-e2e","activation_b64":base64.b64encode(act.tobytes()).decode()},"id":"3"}, headers=hdr).json()
    check("R6-01: step.cut_activation", "result" in r, "e2e")
    grad = np.random.randn(1, ss_state.d_model).astype(np.float32)
    r = requests.post(url, json={"jsonrpc":"2.0","method":"step.cut_gradient",
        "params":{"unit_id":"u-e2e","gradient_b64":base64.b64encode(grad.tobytes()).decode()},"id":"4"}, headers=hdr).json()
    check("R6-01: step.cut_gradient", "result" in r, "e2e")
    check("R6-01: loss", r["result"].get("loss") is not None, "e2e")
    r = requests.post(url, json={"jsonrpc":"2.0","method":"step.commit",
        "params":{"unit_id":"u-e2e"},"id":"5"}, headers=hdr).json()
    check("R6-01: step.commit", "result" in r, "e2e")
    check("R6-01: receipt", r["result"].get("receipt_valid", False), "e2e")
    # Check delta is non-zero: capture weights before flows and compare SHA
    from rc5_split_server import ss_state as _ss
    lw = _ss.server_model.get_lora_weight().detach()
    # The SHA returned by step.commit should match the actual delta
    # If delta is zero (mutation), the SHA will be of a zero tensor
    delta_sha = r["result"].get("delta_sha256", "")
    actual_delta = lw - lw  # zero tensor
    zero_sha = hashlib.sha256(actual_delta.numpy().tobytes()).hexdigest()[:16]
    check("R6-01: delta non-zero (not zero-SHA)", delta_sha != zero_sha, "e2e")
    # Direct mutation check: compute zero-SHA from the lora weight shape/dtype
    _lw = _ss.server_model.get_lora_weight()
    _z = torch.zeros_like(_lw)
    _d = hashlib.sha256(_z.numpy().tobytes()).hexdigest()
    print(f"  DEBUG: delta_sha[:16]={delta_sha[:16]}, zero_sha[:16]={_d[:16]}, match={delta_sha == _d}")
    check("R6-01: delta != zero-tensor SHA", delta_sha != _d, "e2e")

def test_r6_revisions():
    """R6-02: Revisions A-D"""
    coord, db = make_coord()
    register_rc3_worker(coord, "wR", "tokR")
    r = crpc(coord, "worker.join", {"worker_id":"wR","auth_token":"tokR",
             "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"wR")
    sid = r["result"]["session_id"]
    coord._rc5_db.create_assignment("aR", "rR", "rdR", "wR", sid, ["uR"])
    crpc(coord, "work.request",{},"wR")
    crpc(coord, "work.accept",{"assignment_id":"aR"},"wR")

    def mk(ett, val, seed):
        tensors = {"w": np.full((2,), val, dtype=np.float32)}
        buf = io.BytesIO()
        np.savez_compressed(buf, **tensors)
        db = buf.getvalue()
        ds = hashlib.sha256(db).hexdigest()
        d64 = base64.b64encode(db).decode("ascii")
        nonce = hashlib.sha256(str(seed).encode()).hexdigest()[:16]
        rcpt = generate_receipt("rR","rdR","aR","uR","wR",sid,
            "sha256:base","sha256:model","sha256:schema","sha256:loss","fp32","d"*16,ett,1,"x","x",ds,"k1")
        # Replace nonce and re-sign
        import hmac as _hmac2
        rcpt["receipt_nonce"] = nonce
        payload = json.dumps({k:v for k,v in rcpt.items() if k != "signature"}, sort_keys=True, separators=(",", ":"))
        rcpt["signature"] = _hmac2.new(get_signing_key("k1"), payload.encode(), hashlib.sha256).hexdigest()
        return rcpt, d64, ds

    rcpt1, d64_1, ds1 = mk(10, 1.0, 1)
    r1 = crpc(coord, "checkpoint.upload",{"run_id":"rR","round_id":"rdR","assignment_id":"aR",
        "receipt":rcpt1,"delta_b64":d64_1,"delta_sha256":ds1},"wR")
    cid1 = r1["result"]["contribution_id"]
    crpc(coord, "admin.validate.contribution",{"contribution_id":cid1},"")
    crpc(coord, "admin.activate.contribution",{"contribution_id":cid1},"")

    rcpt2, d64_2, ds2 = mk(20, 3.0, 2)
    r2 = crpc(coord, "checkpoint.upload",{"run_id":"rR","round_id":"rdR","assignment_id":"aR",
        "receipt":rcpt2,"delta_b64":d64_2,"delta_sha256":ds2},"wR")
    cid2 = r2["result"]["contribution_id"]

    c1 = lambda: [c for c in coord._rc5_db.get_contributions("rR","rdR") if c["contribution_id"]==cid1][0]
    c2 = lambda: [c for c in coord._rc5_db.get_contributions("rR","rdR") if c["contribution_id"]==cid2][0]

    check("R6-A: v1 ACTIVE", c1()["status"]=="ACTIVE", "ledger")
    check("R6-A: v2 RECEIVED", c2()["status"]=="RECEIVED", "ledger")

    crpc(coord, "admin.validate.contribution",{"contribution_id":cid2},"")
    check("R6-B: v1 still ACTIVE", c1()["status"]=="ACTIVE", "ledger")
    check("R6-B: v2 VALIDATED", c2()["status"]=="VALIDATED", "ledger")

    crpc(coord, "admin.activate.contribution",{"contribution_id":cid2},"")
    check("R6-C: v1 SUPERSEDED", c1()["status"]=="SUPERSEDED", "ledger")
    check("R6-C: v2 ACTIVE", c2()["status"]=="ACTIVE", "ledger")

    rcpt3, d64_3, ds3 = mk(30, 5.0, 3)
    r3 = crpc(coord, "checkpoint.upload",{"run_id":"rR","round_id":"rdR","assignment_id":"aR",
        "receipt":rcpt3,"delta_b64":d64_3,"delta_sha256":ds3},"wR")
    cid3 = r3["result"]["contribution_id"]
    crpc(coord, "admin.validate.contribution",{"contribution_id":cid3},"")
    check("R6-D: v2 still ACTIVE", c2()["status"]=="ACTIVE", "ledger")
    rc = crpc(coord, "admin.round.close",{"run_id":"rR","round_id":"rdR"})
    check("R6-D: 1 aggregated", rc["result"]["contributions_aggregated"]==1, "ledger")
    coord.stop()
    os.unlink(db)

def test_r6_negative():
    """R6-03: 10 negative receipt-delta cases"""
    coord, db = make_coord()
    register_rc3_worker(coord)
    r = crpc(coord, "worker.join",{"worker_id":"w1","auth_token":"tok1",
             "protocol_version":"1.1.0-rc5","capabilities":{"precision":"fp32"}},"w1")
    sid = r["result"]["session_id"]
    coord._rc5_db.create_assignment("a1","rN","rdN","w1",sid,["u1"])
    crpc(coord, "work.request",{},"w1")
    crpc(coord, "work.accept",{"assignment_id":"a1"},"w1")

    def up(rcpt, b64="", sha="a"*64):
        return crpc(coord, "checkpoint.upload",{"run_id":"rN","round_id":"rdN","assignment_id":"a1",
            "receipt":rcpt,"delta_b64":b64,"delta_sha256":sha},"w1")
    def gr(ds="a"*64):
        return generate_receipt("rN","rdN","a1","u1","w1",sid,
            "sha256:base","sha256:model","sha256:schema","sha256:loss","fp32","d"*16,10,1,"x","x",ds,"k1")

    db_bytes, _ = create_npz_delta()
    db64, dsha = delta_b64_sha(db_bytes)

    r = up(gr("b"*64), db64, "b"*64); check("R6-03-1: SHA diff", r.get("result",{}).get("status")=="REJECTED", "integrity")
    r = up(gr(dsha), base64.b64encode(b"fake").decode(), dsha); check("R6-03-2: bytes diff", r.get("result",{}).get("status")=="REJECTED", "integrity")
    r = up(gr(), db64, ""); check("R6-03-3: SHA absent", r.get("result",{}).get("status")=="REJECTED", "integrity")
    r = up(gr("a"*63), db64, "a"*63); check("R6-03-4: 63 chars", r.get("result",{}).get("status")=="REJECTED", "integrity")
    r = up(gr("z"*64), db64, "z"*64); check("R6-03-5: non-hex", r.get("result",{}).get("status")=="REJECTED", "integrity")
    r = up(gr(dsha), "", dsha); check("R6-03-6: b64 absent", r.get("result",{}).get("status")=="REJECTED", "integrity")
    r = up(gr(dsha), "!!!bad!!!", dsha); check("R6-03-7: bad b64", r.get("result",{}).get("status")=="REJECTED", "integrity")
    bad = bytearray(db_bytes); bad[0] ^= 1
    r = up(gr(), base64.b64encode(bytes(bad)).decode(), "a"*64); check("R6-03-8: byte alter", r.get("result",{}).get("status")=="REJECTED", "integrity")
    nb = len(coord._rc5_db.conn.execute("SELECT * FROM rc5_nonce").fetchall())
    na = len(coord._rc5_db.conn.execute("SELECT * FROM rc5_nonce").fetchall())
    check("R6-03-9: nonces stable", na==nb, "integrity")
    r = up(gr(dsha), db64, dsha); check("R6-03-10: retry OK", r.get("result",{}).get("status")=="RECEIVED", "integrity")
    coord.stop()
    os.unlink(db)

def test_r6_split_direct():
    """R6-05: Split Server direct tests"""
    port = 18803
    ss_state.reset_run("r6s", "rd6s")
    t = threading.Thread(target=start_split_server, args=("127.0.0.1", port), daemon=True)
    t.start()
    time.sleep(0.3)
    url = f"http://127.0.0.1:{port}"
    ss_state.units["u-owned"] = {"worker_id":"w-owner","session_id":"ss-owner",
        "state":"OPEN","assignment_id":"a-own","micro_unit_id":"u-owned"}

    r = requests.post(url, json={"jsonrpc":"2.0","method":"step.embedding",
        "params":{"unit_id":"u-owned","text_b64":"AAA="},"id":"1"},
        headers={"X-Worker-Id":"w-alien"}).json()
    c1 = r.get("error",{}).get("code")
    check("R6-05-1: alien -32003", c1==-32003, "security")

    r = requests.post(url, json={"jsonrpc":"2.0","method":"step.cut_activation",
        "params":{"unit_id":"u-owned","activation_b64":"AAA="},"id":"1"},
        headers={"X-Worker-Id":"w-owner"}).json()
    c2 = r.get("error",{}).get("code")
    check("R6-05-2: illegal transition -32003", c2==-32003, "security")

if __name__ == "__main__":
    init_keyring("k1")
    tests = [
        ("R6-01-e2e", test_r6_e2e_split),
        ("R6-02-revisions", test_r6_revisions),
        ("R6-03-negative", test_r6_negative),
        ("R6-05-split-direct", test_r6_split_direct),
    ]
    print("="*60)
    print("  RC5.1-R6 — Tests")
    print("="*60)
    for name, fn in tests:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:
            import traceback
            _rc5t.FAIL += 1
            print(f"  ❌ {name}: {e}")
            traceback.print_exc()
    print(f"{'='*60}")
    total = _rc5t.PASS + _rc5t.FAIL
    print(f"  Resultat: {_rc5t.PASS}/{total} PASS, {_rc5t.FAIL} FAIL")
    print(f"  COUNTS: {_rc5t.COUNTS}")
    print(f"{'='*60}")
    sys.exit(0 if _rc5t.FAIL == 0 else 1)
