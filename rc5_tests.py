"""RC5.1 — T-SPLIT tests: end-to-end Split Server + Coordinator."""
import os, sys, json, time, hashlib, base64, threading, tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rc5_receipt import verify_receipt

PASS = 0
FAIL = 0

def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {msg}")
    else:
        FAIL += 1
        print(f"  ❌ {msg}")

def run_test_t_split_1():
    """T-SPLIT-1: Single worker, single micro-unit, full step flow."""
    print("\n--- T-SPLIT-1: 1 worker, 1 micro-unit E2E ---")
    from rc5_split_server import start_split_server, state as ss_state
    from rc3_coordinator import Coordinator
    from rc5_coordinator_ext import patch_coordinator, rpc as crpc

    # Start Split Server in background
    ss_state.reset_run("run-001", "rnd-001")
    import socketserver
    ss_port = 18793
    ss_thread = threading.Thread(
        target=start_split_server, args=("127.0.0.1", ss_port), daemon=True
    )
    ss_thread.start()
    time.sleep(0.2)

    # Create Coordinator
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    coord = Coordinator(db_path, admin_mode=True)
    coord.train_manager.ensure_schema()
    coord._recovered = True
    patch_coordinator(coord)

    import requests
    ss_url = f"http://127.0.0.1:{ss_port}"

    # Worker joins Coordinator
    r = crpc(coord, "worker.join", {"worker_id": "w-001", "auth_token": "tok1"}, "w-001")
    check(r["result"]["worker_id"] == "w-001", "worker.join returns worker_id")
    check(r["result"]["status"] == "REGISTERED", "worker status REGISTERED")

    # Start run and round on split server
    ss_state.run_id = "run-001"
    ss_state.round_id = "rnd-001"
    ss_state.unit_queue = ["u-000001"]

    # Create empty assignments on coordinator for the split server
    unit_ids = ["u-000001"]
    coord._rc5_assignments["a-t1-001"] = {
        "worker_id": "w-001",
        "unit_ids": unit_ids,
        "status": "PENDING",
    }

    # Work request
    r = crpc(coord, "work.request", {}, "w-001")
    check(r["result"]["assignment_id"] == "a-t1-001", "work.request returns a-t1-001")
    assignment_id = r["result"]["assignment_id"]

    # Accept work
    r = crpc(coord, "work.accept", {"assignment_id": assignment_id}, "w-001")
    check(r["result"]["status"] == "ACTIVE", "work.accept -> ACTIVE")

    # Step: open
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.open","params":{},"id":"1"},
                      headers={"X-Worker-Id":"w-001"}).json()
    check("result" in r, "step.open returns result")
    unit_id = r["result"]["unit_id"]
    check(unit_id is not None, "step.open returns unit_id")

    # Step: embedding (worker sends text)
    text = "Hola mon, aquest es un test"
    text_b64 = base64.b64encode(text.encode()).decode()
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.embedding",
                    "params":{"unit_id":unit_id,"text_b64":text_b64},"id":"2"},
                    headers={"X-Worker-Id":"w-001"}).json()
    check("result" in r, "step.embedding returns result")
    check("embedding_b64" in r["result"], "embedding_b64 present")
    emb = np.frombuffer(base64.b64decode(r["result"]["embedding_b64"]), dtype=np.float32)
    check(len(emb) > 0, f"embedding has {len(emb)} floats")

    # Step: cut_activation
    act = np.random.randn(1, ss_state.d_model).astype(np.float32)
    act_b64 = base64.b64encode(act.tobytes()).decode()
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.cut_activation",
                    "params":{"unit_id":unit_id,"activation_b64":act_b64},"id":"3"},
                    headers={"X-Worker-Id":"w-001"}).json()
    check("result" in r, "step.cut_activation returns result")

    # Step: cut_gradient
    grad = np.random.randn(1, ss_state.d_model).astype(np.float32)
    grad_b64 = base64.b64encode(grad.tobytes()).decode()
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.cut_gradient",
                    "params":{"unit_id":unit_id,"gradient_b64":grad_b64},"id":"4"},
                    headers={"X-Worker-Id":"w-001"}).json()
    check("result" in r, "step.cut_gradient returns result")

    # Step: commit
    r = requests.post(ss_url, json={"jsonrpc":"2.0","method":"step.commit",
                    "params":{"unit_id":unit_id},"id":"5"},
                    headers={"X-Worker-Id":"w-001"}).json()
    check("result" in r, "step.commit returns result")
    receipt = r["result"]["receipt"]
    check(receipt is not None, "receipt present")
    check(receipt.get("effective_trainable_tokens") == len(text), f"ett = {len(text)} tokens")
    check(receipt.get("receipt_nonce") is not None, "receipt_nonce present")
    check(receipt.get("receipt_key_id") is not None, "receipt_key_id present")
    check(receipt.get("signature") is not None, "signature present")
    check(verify_receipt(receipt), "receipt HMAC verification PASS")

    # Upload contribution to Coordinator
    r = crpc(coord, "checkpoint.upload", {
        "run_id": "run-001",
        "round_id": "rnd-001",
        "assignment_id": assignment_id,
        "receipt": receipt,
        "delta_sha256": "abc123",
    }, "w-001")
    check(r["result"]["contribution_id"] is not None, "contribution_id returned")
    check(r["result"]["status"] == "RECEIVED", "contribution status RECEIVED")

    # Close round
    r = crpc(coord, "admin.round.close", {"run_id": "run-001", "round_id": "rnd-001"})
    check("result" in r, "round.close returns result")
    check(r["result"]["contributions_aggregated"] == 1, f"1 contribution aggregated")

    coord.stop()
    os.unlink(db_path)
    print(f"  T-SPLIT-1: {PASS}/{PASS+FAIL} checks")

def run_test_t_split_2():
    """T-SPLIT-2: Cumulative contribution supersedes previous."""
    print("\n--- T-SPLIT-2: Cumulative contribution supersedes ---")
    from rc3_coordinator import Coordinator
    from rc5_coordinator_ext import patch_coordinator, rpc as crpc

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    coord = Coordinator(db_path, admin_mode=True)
    coord.train_manager.ensure_schema()
    coord._recovered = True
    patch_coordinator(coord)

    crpc(coord, "worker.join", {"worker_id": "w-002", "auth_token": "tok2"}, "w-002")

    # Create assignment for this test
    coord._rc5_assignments["a-0001"] = {
        "worker_id": "w-002",
        "unit_ids": ["u-000002"],
        "status": "PENDING",
    }

    # First contribution
    r1 = crpc(coord, "checkpoint.upload", {
        "run_id": "run-002", "round_id": "rnd-002",
        "assignment_id": "a-0001",
        "receipt": {"effective_trainable_tokens": 10},
        "delta_sha256": "abc",
    }, "w-002")
    print(f"  r1 raw: {json.dumps(r1, default=str)[:200]}")
    cid1 = r1["result"]["contribution_id"]

    # Second contribution (same assignment)
    r2 = crpc(coord, "checkpoint.upload", {
        "run_id": "run-002", "round_id": "rnd-002",
        "assignment_id": "a-0001",
        "receipt": {"effective_trainable_tokens": 20},
        "delta_sha256": "def",
    }, "w-002")
    cid2 = r2["result"]["contribution_id"]

    check(cid1 != cid2, "different contribution_id for second upload")
    check(r2["result"]["revision"] == 2, "revision incremented to 2")

    # Close round - should aggregate only 1 (the latest)
    r = crpc(coord, "admin.round.close", {"run_id": "run-002", "round_id": "rnd-002"})
    check(r["result"]["contributions_aggregated"] == 1, "1 contribution aggregated (not 2)")
    check(r["result"]["total_effective_trainable_tokens"] == 20, "ett=20 (latest, not 10+20)")

    coord.stop()
    os.unlink(db_path)
    print(f"  T-SPLIT-2: {PASS}/{PASS+FAIL} subtotal")

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.1 — T-SPLIT Tests")
    print("=" * 60)
    run_test_t_split_1()
    run_test_t_split_2()
    print(f"\n{'='*60}")
    total = PASS + FAIL
    print(f"  Resultat: {PASS}/{total} PASS, {FAIL} FAIL")
    print(f"{'='*60}")
    sys.exit(0 if FAIL == 0 else 1)
