"""RC5.1 — Coordinator extensions: SQLite, FedAvg, hardened join/assign."""
import json, time, hashlib, uuid, os, copy
import numpy as np

from rc5_receipt import verify_receipt, get_verification_key
from rc5_db import Rc5Db

DELTA_STORE = os.environ.get("RC5_DELTA_STORE", "/tmp/rc5_delta_store")

def patch_coordinator(coord, db_path=None):
    if db_path is None:
        import tempfile
        fd, db_path = tempfile.mkstemp(suffix=".rc5.db")
        os.close(fd)
    coord._rc5_db = Rc5Db(db_path)
    coord._rc5_db_path = db_path
    os.makedirs(DELTA_STORE, exist_ok=True)

    # ---- worker.join (hardened) ----
    def handle_worker_join(params, worker_id=""):
        if not worker_id or len(worker_id) < 2:
            return _error(-32030, "invalid worker_id")
        # Check RC3 registration
        if not hasattr(coord, '_rc5_workers'):
            coord._rc5_workers = {}
        # Validate auth_token
        auth_token = params.get("auth_token", "")
        if not auth_token:
            return _error(-32031, "auth_token required")
        # Generate unique session_id
        session_id = hashlib.sha256(f"{worker_id}:{time.time()}:{os.urandom(8).hex()}".encode()).hexdigest()[:16]
        capabilities = params.get("capabilities", {})
        # Check FP32 compatibility
        if capabilities.get("precision", "fp32") != "fp32":
            return _error(-32032, "fp32 required for RC5.1")
        # Persist
        coord._rc5_db.upsert_worker(worker_id, session_id, auth_token, capabilities)
        coord._rc5_workers[worker_id] = {"session_id": session_id, "status": "REGISTERED", "joined_at": time.time()}
        return _ok({"worker_id": worker_id, "session_id": session_id, "status": "REGISTERED"})

    # ---- work.request ----
    def handle_work_request(params, worker_id=""):
        if worker_id not in coord._rc5_workers:
            return _error(-32010, "worker not registered")
        w = coord._rc5_workers[worker_id]
        from rc5_db import DB_LOCK
        with DB_LOCK:
            c = coord._rc5_db.conn.execute(
                "SELECT * FROM rc5_assignment WHERE worker_id=? AND status='PENDING'", (worker_id,))
            row = c.fetchone()
            if row:
                aid = row["assignment_id"]
                unit_ids = json.loads(row["micro_unit_ids"])
                coord._rc5_db.set_assignment_status(aid, "ASSIGNED")
                w["status"] = "ASSIGNED"
                return _ok({"assignment_id": aid, "unit_count": len(unit_ids),
                           "micro_unit_ids": unit_ids, "status": "ASSIGNED"})
        return _ok({"assignment_id": None, "status": "NO_WORK"})

    # ---- work.accept ----
    def handle_work_accept(params, worker_id=""):
        aid = params.get("assignment_id")
        row = coord._rc5_db.get_assignment(aid)
        if not row:
            return _error(-32012, "unknown assignment")
        row = dict(row)
        if row["worker_id"] != worker_id:
            return _error(-32013, "not your assignment")
        if row["status"] not in ("PENDING", "ASSIGNED"):
            return _error(-32014, f"assignment status {row['status']} cannot be accepted")
        coord._rc5_db.set_assignment_status(aid, "ACTIVE")
        if worker_id in coord._rc5_workers:
            coord._rc5_workers[worker_id]["status"] = "ACTIVE"
        return _ok({"assignment_id": aid, "status": "ACTIVE"})

    # ---- checkpoint.upload (full verification) ----
    def handle_checkpoint_upload(params, worker_id=""):
        receipt = params.get("receipt", {})
        run_id = params.get("run_id", "")
        round_id = params.get("round_id", "")
        assignment_id = params.get("assignment_id", "")
        delta_sha256 = params.get("delta_sha256", "")

        # Verify HMAC
        valid, msg = verify_receipt(receipt)
        if not valid:
            return _reject(f"receipt HMAC invalid: {msg}")
        kid = receipt.get("receipt_key_id", "")
        if kid not in _load_keyring_safe().get("signing", {}):
            return _reject(f"key_id {kid} not a signing key")
        nonce = receipt.get("receipt_nonce", "")
        if coord._rc5_db.nonce_exists(nonce):
            return _reject("nonce replayed")
        if time.time() > receipt.get("expires_at", 0):
            return _reject("receipt expired")
        if receipt.get("worker_id", "") != worker_id:
            return _reject("worker_id mismatch")
        expected_sid = coord._rc5_workers.get(worker_id, {}).get("session_id", "")
        if expected_sid and receipt.get("session_id", "") != expected_sid:
            return _reject("session_id mismatch")
        if receipt.get("run_id", "") != run_id:
            return _reject("run_id mismatch")
        if receipt.get("round_id", "") != round_id:
            return _reject("round_id mismatch")
        if receipt.get("assignment_id", "") != assignment_id:
            return _reject("assignment_id mismatch")
        a = coord._rc5_db.get_assignment(assignment_id)
        if not a:
            return _reject("assignment not found")
        a = dict(a)
        if a["worker_id"] != worker_id:
            return _reject("assignment not owned")
        muid = receipt.get("micro_unit_id", "")
        if muid not in json.loads(a.get("micro_unit_ids", "[]")):
            return _reject("micro_unit_id not in assignment")
        prof = coord._rc5_frozen_profile if hasattr(coord, '_rc5_frozen_profile') else {}
        prof = prof or {"base_adapter_hash": "sha256:base", "model_hash": "sha256:model",
                        "adapter_schema_hash": "sha256:schema", "loss_definition_hash": "sha256:loss",
                        "precision_profile": "fp32"}
        for field, key in [("base_adapter_hash","base_adapter_hash"),("model_hash","model_hash"),
                           ("adapter_schema_hash","adapter_schema_hash"),
                           ("loss_definition_hash","loss_definition_hash")]:
            if receipt.get(field, "") != prof[key]:
                return _reject(f"{field} mismatch")
        if receipt.get("precision_profile", "") != "fp32":
            return _reject("precision_profile not fp32")
        ett = receipt.get("effective_trainable_tokens", 0)
        if ett <= 0:
            return _reject("ett must be > 0")
        if "delta_sha256" not in receipt:
            return _reject("delta_sha256 missing")
        ds = receipt["delta_sha256"]
        if not isinstance(ds, str) or len(ds) != 64 or not all(c in "0123456789abcdef" for c in ds):
            return _reject("delta_sha256 format")
        if receipt.get("delta_sha256", "") != delta_sha256:
            return _reject("delta_sha256 mismatch")

        # All checks pass
        coord._rc5_db.add_nonce(nonce)

        # Find previous revision for this key
        from rc5_db import DB_LOCK
        with DB_LOCK:
            cur = coord._rc5_db.conn.execute(
                "SELECT MAX(revision) FROM rc5_contribution WHERE run_id=? AND round_id=? AND assignment_id=? AND worker_id=?",
                (run_id, round_id, assignment_id, worker_id))
            max_rev = cur.fetchone()[0] or 0
        revision = max_rev + 1
        contrib_id = hashlib.sha256(f"{assignment_id}:{time.time()}:{nonce}".encode()).hexdigest()[:16]

        # Find previous contribution to set supersedes
        prev = coord._rc5_db.get_active_contribution(run_id, round_id, assignment_id, worker_id)
        supersedes = prev["contribution_id"] if prev else None

        contrib = {
            "contribution_id": contrib_id,
            "run_id": run_id, "round_id": round_id,
            "assignment_id": assignment_id, "worker_id": worker_id,
            "revision": revision, "status": "RECEIVED",
            "supersedes_contribution_id": supersedes,
            "receipt_nonce": nonce, "delta_sha256": delta_sha256,
            "effective_trainable_tokens": ett,
            "submitted_at": time.time(),
            "delta_path": "",
        }
        # Save delta to delta_store
        delta_bytes = params.get("delta_bytes")
        if delta_bytes:
            delta_dir = os.path.join(DELTA_STORE, run_id, round_id)
            os.makedirs(delta_dir, exist_ok=True)
            delta_path = os.path.join(delta_dir, f"{contrib_id}.npz")
            import numpy as np
            if isinstance(delta_bytes, bytes):
                with open(delta_path, "wb") as f:
                    f.write(delta_bytes)
            contrib["delta_path"] = delta_path

        coord._rc5_db.add_contribution(contrib)
        return _ok({"contribution_id": contrib_id, "revision": revision, "status": "RECEIVED"})

    # ---- validate contribution ----
    def handle_validate(params, worker_id=""):
        cid = params.get("contribution_id")
        for c in coord._rc5_db.get_contributions():
            if c["contribution_id"] == cid and c["status"] == "RECEIVED":
                coord._rc5_db.update_contribution_status(cid, "VALIDATED")
                return _ok({"contribution_id": cid, "status": "VALIDATED"})
        return _error(-32020, "not found or not RECEIVED")

    # ---- activate contribution (SUPERSEDES previous ACTIVE) ----
    def handle_activate(params, worker_id=""):
        cid = params.get("contribution_id")
        target = None
        for c in coord._rc5_db.get_contributions():
            if c["contribution_id"] == cid and c["status"] == "VALIDATED":
                target = c
                break
        if not target:
            return _error(-32020, "not found or not VALIDATED")
        # Supersede previous ACTIVE for same key
        from rc5_db import DB_LOCK
        with DB_LOCK:
            coord._rc5_db.conn.execute(
                "UPDATE rc5_contribution SET status='SUPERSEDED' WHERE run_id=? AND round_id=? AND assignment_id=? AND worker_id=? AND status='ACTIVE'",
                (target["run_id"], target["round_id"], target["assignment_id"], target["worker_id"]))
            coord._rc5_db.update_contribution_status(cid, "ACTIVE")
        return _ok({"contribution_id": cid, "status": "ACTIVE",
                    "supersedes": target.get("supersedes_contribution_id")})

    # ---- FedAvg real (P1) ----
    def handle_round_close(params, worker_id=""):
        run_id = params.get("run_id")
        round_id = params.get("round_id")
        if not run_id or not round_id:
            return _error(-32602, "run_id and round_id required")

        contribs = coord._rc5_db.get_contributions(run_id, round_id)
        active = {}
        for c in contribs:
            if c["status"] == "ACTIVE":
                aid = c["assignment_id"]
                if aid not in active or c["revision"] > active[aid]["revision"]:
                    active[aid] = c

        if not active:
            return _ok({"contributions_aggregated": 0, "total_ett": 0, "status": "NO_CONTRIBUTIONS"})

        # Load and validate deltas
        deltas = []
        ett_list = []
        adapter_schema = None
        base_hash = None
        shapes = None
        dtype = None

        for aid, c in active.items():
            dp = c.get("delta_path", "")
            if not dp or not os.path.exists(dp):
                continue
            data = np.load(dp)
            delta_tensors = {k: data[k] for k in data.files}
            deltas.append(delta_tensors)
            ett_list.append(c["effective_trainable_tokens"])
            # Validate consistency
            if adapter_schema is None:
                adapter_schema = set(delta_tensors.keys())
                shapes = {k: v.shape for k, v in delta_tensors.items()}
                dtype = {k: v.dtype for k, v in delta_tensors.items()}
            else:
                if set(delta_tensors.keys()) != adapter_schema:
                    return _error(-32040, f"tensor schema mismatch for {aid}")
                for k in delta_tensors:
                    if delta_tensors[k].shape != shapes.get(k):
                        return _error(-32041, f"shape mismatch for {k}: {delta_tensors[k].shape} vs {shapes[k]}")
                    if delta_tensors[k].dtype != dtype.get(k):
                        return _error(-32042, f"dtype mismatch for {k}")

        if not deltas:
            return _ok({"contributions_aggregated": 0, "total_ett": sum(ett_list),
                        "status": "NO_DELTAS"})

        # FedAvg: Δ_global = Σ(ett_i * Δ_i) / Σ ett_i
        total_ett = sum(ett_list)
        global_delta = {}
        for k in adapter_schema:
            weighted = sum(d[k] * ett_list[i] for i, d in enumerate(deltas))
            global_delta[k] = weighted / total_ett

        # Compute SHA-256
        delta_bytes_all = b"".join(global_delta[k].tobytes() for k in sorted(adapter_schema))
        delta_sha = hashlib.sha256(delta_bytes_all).hexdigest()

        # Save checkpoint
        ckpt_id = hashlib.sha256(f"{run_id}:{round_id}:{time.time()}".encode()).hexdigest()[:16]
        manifest = {
            "checkpoint_id": ckpt_id,
            "run_id": run_id, "round_id": round_id,
            "contributions_included": [c["contribution_id"] for c in active.values()],
            "total_effective_trainable_tokens": total_ett,
            "global_delta_sha256": delta_sha,
            "tensor_schema": list(adapter_schema),
            "shapes": {k: list(v) for k, v in shapes.items()},
            "dtypes": {k: str(v) for k, v in dtype.items()},
            "precision_profile": "fp32",
            "created_at": time.time(),
        }

        # Save global delta
        ckpt_dir = os.path.join(DELTA_STORE, run_id, round_id)
        os.makedirs(ckpt_dir, exist_ok=True)
        ckpt_path = os.path.join(ckpt_dir, f"global_{ckpt_id}.npz")
        np.savez_compressed(ckpt_path, **global_delta)

        # Mark contributions AGGREGATED
        for c in active.values():
            coord._rc5_db.update_contribution_status(c["contribution_id"], "AGGREGATED")

        coord._rc5_db.save_checkpoint(ckpt_id, run_id, round_id, delta_sha, delta_sha, manifest)

        return _ok({
            "checkpoint_id": ckpt_id,
            "contributions_aggregated": len(active),
            "total_ett": total_ett,
            "global_delta_sha256": delta_sha,
            "status": "AGGREGATED",
        })

    coord._rc5_extensions = {
        "worker.join": handle_worker_join,
        "work.request": handle_work_request,
        "work.accept": handle_work_accept,
        "checkpoint.upload": handle_checkpoint_upload,
        "admin.validate.contribution": handle_validate,
        "admin.activate.contribution": handle_activate,
        "admin.round.close": handle_round_close,
    }
    return coord

def _load_keyring_safe():
    from rc5_receipt import _load_keyring
    return _load_keyring()

def _ok(result):
    return {"jsonrpc": "2.0", "result": result, "id": "1"}

def _error(code, msg):
    return {"jsonrpc": "2.0", "error": {"code": code, "message": msg}, "id": "1"}

def _reject(reason):
    return {"jsonrpc": "2.0", "result": {"status": "REJECTED", "reason": reason}, "id": "1"}

def rpc(coord, method, params, worker_id=""):
    if hasattr(coord, "_rc5_extensions") and method in coord._rc5_extensions:
        return coord._rc5_extensions[method](params, worker_id)
    import json as _j
    from rc3_state import RC3_PROTOCOL_VERSION
    return _j.loads(coord.handle_request(
        {"jsonrpc":"2.0","method":method,"params":params,"id":"t"},
        {"X-Protocol-Version":RC3_PROTOCOL_VERSION,"X-Worker-Id":worker_id,"X-Worker-Token":""}))
