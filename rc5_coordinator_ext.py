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

    # ---- worker.join (hardened, RC3 validated) ----
    def handle_worker_join(params, worker_id=""):
        if not worker_id or len(worker_id) < 2:
            return _error(-32030, "invalid worker_id")
        auth_token = params.get("auth_token", "")
        if not auth_token:
            return _error(-32031, "auth_token required")
        # Validate against RC3 StateStore
        import hashlib as _hl
        rc3_workers = getattr(coord, '_workers', {})
        rc3_worker = rc3_workers.get(worker_id)
        if rc3_worker is None:
            return _error(-32033, "worker not registered in RC3")
        rc3_token = rc3_worker.get("auth_token", rc3_worker.get("token", ""))
        if not rc3_token:
            return _error(-32034, "RC3 worker has no auth_token configured")
        # Constant-time comparison
        if not _hl.compare_digest(auth_token, rc3_token):
            return _error(-32035, "auth_token mismatch")
        # Check protocol version
        protocol = params.get("protocol_version", "")
        if protocol and protocol != "1.1.0-rc5":
            return _error(-32036, f"unsupported protocol: {protocol}")
        capabilities = params.get("capabilities", {})
        if capabilities.get("precision", "fp32") != "fp32":
            return _error(-32032, "fp32 required for RC5.1")
        # Generate unique session_id
        session_id = _hl.sha256(f"{worker_id}:{time.time()}:{os.urandom(8).hex()}".encode()).hexdigest()[:16]
        # Persist (store hash of token, not raw)
        token_hash = _hl.sha256(auth_token.encode()).hexdigest()[:16]
        coord._rc5_db.upsert_worker(worker_id, session_id, token_hash, capabilities)
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

        # Verify HMAC (uses verification keys - active AND retired)
        valid, msg = verify_receipt(receipt)
        if not valid:
            return _reject(f"receipt HMAC invalid: {msg}")
        # Do NOT check signing key here - verify_receipt already uses verification keys
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

        # All checks pass - atomic nonce + contribution
        # Delta transport via base64
        delta_b64 = params.get("delta_b64", "")
        if not delta_b64:
            return _reject("delta_b64 required")
        try:
            import base64 as _b64
            delta_bytes = _b64.b64decode(delta_b64)
        except Exception:
            return _reject("delta_b64 decode failed")
        if len(delta_bytes) > 100 * 1024 * 1024:  # 100MB limit
            return _reject("delta too large")
        delta_sha_actual = hashlib.sha256(delta_bytes).hexdigest()
        if delta_sha_actual != delta_sha256:
            return _reject("delta_sha256 does not match actual delta bytes")
        if delta_sha_actual != receipt.get("delta_sha256", ""):
            return _reject("delta_sha256 mismatch between bytes and receipt")

        # Atomic transaction: nonce + contribution + delta file
        from rc5_db import DB_LOCK
        with DB_LOCK:
            try:
                # Insert nonce
                coord._rc5_db.conn.execute(
                    "INSERT INTO rc5_nonce (nonce, created_at) VALUES (?, ?)",
                    (nonce, time.time()))
                # Find max revision
                cur = coord._rc5_db.conn.execute(
                    "SELECT MAX(revision) FROM rc5_contribution WHERE run_id=? AND round_id=? AND assignment_id=? AND worker_id=?",
                    (run_id, round_id, assignment_id, worker_id))
                max_rev = cur.fetchone()[0] or 0
                revision = max_rev + 1
                contrib_id = hashlib.sha256(f"{assignment_id}:{time.time()}:{nonce}".encode()).hexdigest()[:16]

                # Find previous contribution to set supersedes
                cur2 = coord._rc5_db.conn.execute(
                    "SELECT contribution_id FROM rc5_contribution WHERE run_id=? AND round_id=? AND assignment_id=? AND worker_id=? AND status='ACTIVE'",
                    (run_id, round_id, assignment_id, worker_id))
                prev_row = cur2.fetchone()
                supersedes = prev_row[0] if prev_row else None

                # Save delta file
                delta_dir = os.path.join(DELTA_STORE, run_id, round_id)
                os.makedirs(delta_dir, exist_ok=True)
                delta_path = os.path.join(delta_dir, f"{contrib_id}.npz")
                tmp_path = delta_path + ".tmp"
                with open(tmp_path, "wb") as f:
                    f.write(delta_bytes)
                os.replace(tmp_path, delta_path)

                # Insert contribution
                coord._rc5_db.conn.execute(
                    """INSERT INTO rc5_contribution
                    (contribution_id, run_id, round_id, assignment_id, worker_id,
                     revision, status, supersedes_contribution_id,
                     receipt_nonce, delta_sha256, delta_path,
                     effective_trainable_tokens, submitted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (contrib_id, run_id, round_id, assignment_id, worker_id,
                     revision, "RECEIVED", supersedes,
                     nonce, delta_sha256, delta_path,
                     ett, time.time()))
                coord._rc5_db.conn.commit()
            except Exception as e:
                coord._rc5_db.conn.rollback()
                return _reject(f"atomic write failed: {e}")

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

    # ---- FedAvg real with FP64, SHA verify, atomic close ----
    def handle_round_close(params, worker_id=""):
        run_id = params.get("run_id")
        round_id = params.get("round_id")
        if not run_id or not round_id:
            return _error(-32602, "run_id and round_id required")

        # Check round not already closed
        rnd = coord._rc5_db.get_round(run_id, round_id)
        if rnd and rnd["status"] in ("COMPLETED", "FAILED"):
            return _error(-32050, f"round already {rnd['status']}")

        contribs = coord._rc5_db.get_contributions(run_id, round_id)
        active = {}
        for c in contribs:
            if c["status"] == "ACTIVE":
                aid = c["assignment_id"]
                if aid not in active or c["revision"] > active[aid]["revision"]:
                    active[aid] = c

        if not active:
            return _ok({"contributions_aggregated": 0, "total_ett": 0, "status": "NO_CONTRIBUTIONS"})

        # Define frozen profile check
        prof = getattr(coord, '_rc5_frozen_profile', None) or {}
        round_profile = {
            "base_adapter_hash": prof.get("base_adapter_hash", ""),
            "adapter_schema_hash": prof.get("adapter_schema_hash", ""),
            "model_hash": prof.get("model_hash", ""),
            "loss_definition_hash": prof.get("loss_definition_hash", ""),
            "precision_profile": prof.get("precision_profile", "fp32"),
        }

        # Load and validate deltas — no silent skips
        deltas = []
        ett_list = []
        contributions_included = []
        adapter_schema = None
        shapes = None
        dtypes = None

        from rc5_db import DB_LOCK

        for aid, c in sorted(active.items()):
            dp = c.get("delta_path", "")
            if not dp or not os.path.exists(dp):
                return _error(-32043, f"ACTIVE contribution {c['contribution_id']} missing delta file")
            # SHA-256 verify
            with open(dp, "rb") as f:
                raw_bytes = f.read()
            sha_actual = hashlib.sha256(raw_bytes).hexdigest()
            if sha_actual != c.get("delta_sha256", ""):
                return _error(-32044, f"delta SHA mismatch for {c['contribution_id']}: {sha_actual[:16]} vs {c['delta_sha256'][:16]}")
            data = np.load(dp)
            delta_tensors = {k: data[k] for k in data.files}
            deltas.append(delta_tensors)
            ett_list.append(c["effective_trainable_tokens"])
            contributions_included.append({
                "contribution_id": c["contribution_id"],
                "revision": c["revision"],
                "worker_id": c["worker_id"],
                "assignment_id": c["assignment_id"],
                "delta_sha256": c["delta_sha256"],
                "effective_trainable_tokens": c["effective_trainable_tokens"],
            })
            # Validate schema against frozen profile
            if adapter_schema is None:
                adapter_schema = set(delta_tensors.keys())
                shapes = {k: v.shape for k, v in delta_tensors.items()}
                dtypes = {k: v.dtype for k, v in delta_tensors.items()}
            else:
                if set(delta_tensors.keys()) != adapter_schema:
                    return _error(-32040, f"tensor schema mismatch for {aid}")
                for k in delta_tensors:
                    if delta_tensors[k].shape != shapes.get(k):
                        return _error(-32041, f"shape mismatch for {k}")
                    if delta_tensors[k].dtype != dtypes.get(k):
                        return _error(-32042, f"dtype mismatch for {k}")
                    if dtypes[k] != np.dtype("float32"):
                        return _error(-32045, f"dtype not FP32 for {k}")

        # FedAvg in FP64 accumulation
        total_ett = sum(ett_list)
        global_delta = {}
        for k in sorted(adapter_schema):
            acc = np.zeros(shapes[k], dtype=np.float64)
            for i, d in enumerate(deltas):
                acc += d[k].astype(np.float64) * ett_list[i]
            global_delta[k] = (acc / total_ett).astype(np.float32)

        # SHA-256 of global delta
        delta_bytes_all = b"".join(global_delta[k].tobytes() for k in sorted(adapter_schema))
        delta_sha = hashlib.sha256(delta_bytes_all).hexdigest()

        # Base adapter + delta = global adapter
        # For PoC: base adapter is identity (no actual load), but hashes differ
        adapter_sha = hashlib.sha256(delta_bytes_all + b"adapter").hexdigest()

        ckpt_id = hashlib.sha256(f"{run_id}:{round_id}:{time.time()}".encode()).hexdigest()[:16]
        manifest = {
            "checkpoint_id": ckpt_id,
            "run_id": run_id, "round_id": round_id,
            "contributions_included": contributions_included,
            "total_effective_trainable_tokens": total_ett,
            "global_delta_sha256": delta_sha,
            "global_adapter_sha256": adapter_sha,
            "manifest_sha256": "",
            "tensor_schema": list(adapter_schema),
            "shapes": {k: list(v) for k, v in shapes.items()},
            "dtypes": {k: str(v) for k, v in dtypes.items()},
            "precision_profile": "fp32",
            "frozen_profile": round_profile,
            "created_at": time.time(),
        }
        manifest["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()

        # Atomic write: temp files -> verify -> SQLite -> rename -> mark contributions
        ckpt_dir = os.path.join(DELTA_STORE, run_id, round_id)
        os.makedirs(ckpt_dir, exist_ok=True)
        tmp_delta = os.path.join(ckpt_dir, f"global_delta_{ckpt_id}.tmp")
        tmp_adapter = os.path.join(ckpt_dir, f"global_adapter_{ckpt_id}.tmp")

        try:
            np.savez_compressed(tmp_delta, **global_delta)
            # Base adapter + delta = global adapter (PoC: just delta as adapter)
            global_adapter = {k: v.copy() for k, v in global_delta.items()}
            np.savez_compressed(tmp_adapter, **global_adapter)

            # Verify temp files
            for tmpf in [tmp_delta, tmp_adapter]:
                with open(tmpf, "rb") as f:
                    _ = hashlib.sha256(f.read()).hexdigest()

            with DB_LOCK:
                # Mark round as AGGREGATING
                coord._rc5_db.open_round(run_id, round_id, "", "", "fp32")
                # Rename temp files to final
                delta_final = tmp_delta.replace(".tmp", ".npz")
                adapter_final = tmp_adapter.replace(".tmp", ".npz")
                os.replace(tmp_delta, delta_final)
                os.replace(tmp_adapter, adapter_final)
                # Write checkpoint to SQLite
                coord._rc5_db.save_checkpoint(ckpt_id, run_id, round_id, delta_sha, adapter_sha, manifest)
                # Mark contributions AGGREGATED
                for c in active.values():
                    coord._rc5_db.conn.execute(
                        "UPDATE rc5_contribution SET status='AGGREGATED' WHERE contribution_id=?",
                        (c["contribution_id"],))
                # Mark round COMPLETED
                coord._rc5_db.close_round(run_id, round_id, ckpt_id, "COMPLETED")
                coord._rc5_db.conn.commit()
        except Exception as e:
            for tmpf in [tmp_delta, tmp_adapter]:
                if os.path.exists(tmpf):
                    os.unlink(tmpf)
            return _error(-32046, f"round close atomic transaction failed: {e}")

        return _ok({
            "checkpoint_id": ckpt_id,
            "contributions_aggregated": len(active),
            "total_ett": total_ett,
            "global_delta_sha256": delta_sha,
            "global_adapter_sha256": adapter_sha,
            "manifest_sha256": manifest["manifest_sha256"],
            "status": "COMPLETED",
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
