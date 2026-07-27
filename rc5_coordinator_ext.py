"""RC5.1 — Coordinator extensions: ledger, receipt verification, FedAvg."""
import json, time, hashlib, uuid, copy

from rc5_receipt import verify_receipt, _load_keyring

def patch_coordinator(coord):
    if not hasattr(coord, "_rc5_ledger"):
        coord._rc5_ledger = []
    if not hasattr(coord, "_rc5_workers"):
        coord._rc5_workers = {}
    if not hasattr(coord, "_rc5_assignments"):
        coord._rc5_assignments = {}
    if not hasattr(coord, "_rc5_contrib_index"):
        coord._rc5_contrib_index = {}
    if not hasattr(coord, "_rc5_nonces"):
        coord._rc5_nonces = set()
    if not hasattr(coord, "_rc5_frozen_profile"):
        coord._rc5_frozen_profile = {
            "base_adapter_hash": "sha256:base",
            "model_hash": "sha256:model",
            "adapter_schema_hash": "sha256:schema",
            "loss_definition_hash": "sha256:loss",
            "precision_profile": "fp32",
        }

    def handle_worker_join(params, worker_id=""):
        session_id = params.get("session_id", str(uuid.uuid4()))
        coord._rc5_workers[worker_id] = {
            "session_id": session_id, "status": "REGISTERED",
            "joined_at": time.time(),
        }
        return {"jsonrpc":"2.0","result":{"worker_id":worker_id,"session_id":session_id,"status":"REGISTERED"},"id":"1"}

    def handle_work_request(params, worker_id=""):
        if worker_id not in coord._rc5_workers:
            return {"jsonrpc":"2.0","error":{"code":-32010,"message":"Worker not registered"},"id":"1"}
        w = coord._rc5_workers[worker_id]
        for aid, a in coord._rc5_assignments.items():
            if a["worker_id"] == worker_id and a["status"] == "PENDING":
                w["status"] = "ASSIGNED"
                return {"jsonrpc":"2.0","result":{"assignment_id":aid,"unit_count":len(a["unit_ids"]),
                        "status":"ASSIGNED","micro_unit_ids":a["unit_ids"]},"id":"1"}
        return {"jsonrpc":"2.0","result":{"assignment_id":None,"status":"NO_WORK"},"id":"1"}

    def handle_work_accept(params, worker_id=""):
        aid = params.get("assignment_id")
        if aid not in coord._rc5_assignments:
            return {"jsonrpc":"2.0","error":{"code":-32012,"message":"Unknown assignment"},"id":"1"}
        a = coord._rc5_assignments[aid]
        if a["worker_id"] != worker_id:
            return {"jsonrpc":"2.0","error":{"code":-32013,"message":"Not your assignment"},"id":"1"}
        a["status"] = "ACTIVE"
        if worker_id in coord._rc5_workers:
            coord._rc5_workers[worker_id]["status"] = "ACTIVE"
        return {"jsonrpc":"2.0","result":{"assignment_id":aid,"status":"ACTIVE"},"id":"1"}

    def handle_checkpoint_upload(params, worker_id=""):
        """Full receipt verification before ledger update."""
        receipt = params.get("receipt", {})
        run_id = params.get("run_id", "")
        round_id = params.get("round_id", "")
        assignment_id = params.get("assignment_id", "")
        delta_sha256 = params.get("delta_sha256", "")

        # ---- P1: 17 verification checks ----
        # 1. HMAC valid
        valid, msg = verify_receipt(receipt)
        if not valid:
            return _reject(f"receipt HMAC invalid: {msg}")
        # 2. key_id known and active
        kr = _load_keyring()
        kid = receipt.get("receipt_key_id", "")
        if kid not in kr["active"]:
            return _reject(f"key_id {kid} not active")
        # 3. nonce not reused
        nonce = receipt.get("receipt_nonce", "")
        if nonce in coord._rc5_nonces:
            return _reject(f"nonce {nonce} replayed")
        # 4. not expired
        expires = receipt.get("expires_at", 0)
        if time.time() > expires:
            return _reject("receipt expired")
        # 5. worker_id matches
        if receipt.get("worker_id", "") != worker_id:
            return _reject("worker_id mismatch")
        # 6. session_id matches
        expected_sid = coord._rc5_workers.get(worker_id, {}).get("session_id", "")
        if expected_sid and receipt.get("session_id", "") != expected_sid:
            return _reject("session_id mismatch")
        # 7. run_id matches
        if receipt.get("run_id", "") != run_id:
            return _reject("run_id mismatch")
        # 8. round_id matches
        if receipt.get("round_id", "") != round_id:
            return _reject("round_id mismatch")
        # 9. assignment_id matches
        if receipt.get("assignment_id", "") != assignment_id:
            return _reject("assignment_id mismatch")
        # 10. assignment owned by worker
        a = coord._rc5_assignments.get(assignment_id)
        if a is None:
            return _reject("assignment not found")
        if a["worker_id"] != worker_id:
            return _reject("assignment not owned by worker")
        # 11. micro_unit_id belongs to assignment
        muid = receipt.get("micro_unit_id", "")
        if muid not in a.get("unit_ids", []):
            return _reject("micro_unit_id not in assignment")
        # 12-15. hashes match frozen profile
        prof = coord._rc5_frozen_profile
        if receipt.get("base_adapter_hash", "") != prof["base_adapter_hash"]:
            return _reject("base_adapter_hash mismatch")
        if receipt.get("model_hash", "") != prof["model_hash"]:
            return _reject("model_hash mismatch")
        if receipt.get("adapter_schema_hash", "") != prof["adapter_schema_hash"]:
            return _reject("adapter_schema_hash mismatch")
        if receipt.get("loss_definition_hash", "") != prof["loss_definition_hash"]:
            return _reject("loss_definition_hash mismatch")
        # 16. precision_profile = fp32
        if receipt.get("precision_profile", "") != "fp32":
            return _reject("precision_profile not fp32")
        # 17. ett > 0
        ett = receipt.get("effective_trainable_tokens", 0)
        if ett <= 0:
            return _reject("effective_trainable_tokens must be > 0")
        # 18. delta_sha256 present in receipt
        if "delta_sha256" not in receipt:
            return _reject("delta_sha256 missing from receipt")
        # 19. delta_sha256 format (64 hex lowercase chars)
        ds = receipt["delta_sha256"]
        if not isinstance(ds, str) or len(ds) != 64 or not all(c in "0123456789abcdef" for c in ds):
            return _reject("delta_sha256 must be 64 hex lowercase chars")
        # 20. delta_sha256 in receipt matches params
        if receipt.get("delta_sha256", "") != delta_sha256:
            return _reject("delta_sha256 mismatch between receipt and upload params")

        # All checks passed
        coord._rc5_nonces.add(nonce)
        key = (run_id, round_id, assignment_id, worker_id)

        # Find previous contribution for this key (if any)
        # DO NOT supersede yet — only mark SUPERSEDED when new one reaches ACTIVE
        previous = None
        for c in coord._rc5_ledger:
            if (c["run_id"] == run_id and c["round_id"] == round_id
                    and c["assignment_id"] == assignment_id
                    and c["worker_id"] == worker_id):
                previous = c  # keep reference but don't change status

        revision = (previous["revision"] + 1) if previous else 1
        contrib = {
            "contribution_id": hashlib.sha256(f"{assignment_id}:{time.time()}:{nonce}".encode()).hexdigest()[:16],
            "revision": revision,
            "supersedes_contribution_id": previous["contribution_id"] if previous else None,
            "status": "RECEIVED",  # new contribution starts as RECEIVED
            "receipt": receipt,
            "delta_sha256": delta_sha256,
            "worker_id": worker_id,
            "assignment_id": assignment_id,
            "run_id": run_id,
            "round_id": round_id,
            "submitted_at": time.time(),
            "effective_trainable_tokens": ett,
        }
        coord._rc5_ledger.append(contrib)
        coord._rc5_contrib_index[key] = contrib["contribution_id"]

        # Previous contribution REMAINS in its current state
        # It will be SUPERSEDED only when the new one reaches ACTIVE

        return {"jsonrpc":"2.0","result":{"contribution_id":contrib["contribution_id"],
                "revision":revision,"status":"RECEIVED"},"id":"1"}

    def handle_validate_contribution(params, worker_id=""):
        """Explicit validation step: RECEIVED -> VALIDATED."""
        cid = params.get("contribution_id")
        for c in coord._rc5_ledger:
            if c["contribution_id"] == cid and c["status"] == "RECEIVED":
                c["status"] = "VALIDATED"
                return {"jsonrpc":"2.0","result":{"contribution_id":cid,"status":"VALIDATED"},"id":"1"}
        return {"jsonrpc":"2.0","error":{"code":-32020,"message":"contribution not found or not RECEIVED"},"id":"1"}

    def handle_activate_contribution(params, worker_id=""):
        """Explicit activation step: VALIDATED -> ACTIVE. Previous ACTIVE becomes SUPERSEDED."""
        cid = params.get("contribution_id")
        for c in coord._rc5_ledger:
            if c["contribution_id"] == cid and c["status"] == "VALIDATED":
                # Only supersede the PREVIOUS ACTIVE for same key
                for c2 in coord._rc5_ledger:
                    if (c2["run_id"] == c["run_id"] and c2["round_id"] == c["round_id"]
                            and c2["assignment_id"] == c["assignment_id"]
                            and c2["worker_id"] == c["worker_id"]
                            and c2["status"] == "ACTIVE"):
                        c2["status"] = "SUPERSEDED"
                c["status"] = "ACTIVE"
                return {"jsonrpc":"2.0","result":{"contribution_id":cid,"status":"ACTIVE",
                        "superseded": c.get("supersedes_contribution_id")},"id":"1"}
        return {"jsonrpc":"2.0","error":{"code":-32020,"message":"contribution not found or not VALIDATED"},"id":"1"}

    def handle_round_close(params, worker_id=""):
        """FedAvg over ACTIVE contributions. Only ACTIVE is aggregable."""
        run_id = params.get("run_id")
        round_id = params.get("round_id")
        if not run_id or not round_id:
            return {"jsonrpc":"2.0","error":{"code":-32602,"message":"run_id and round_id required"},"id":"1"}

        # Collect ACTIVE contributions for this round (one per assignment)
        active = {}
        for c in coord._rc5_ledger:
            if c["run_id"] == run_id and c["round_id"] == round_id:
                if c["status"] == "ACTIVE":
                    # Keep latest revision per assignment
                    aid = c["assignment_id"]
                    if aid not in active or c["revision"] > active[aid]["revision"]:
                        active[aid] = c

        if not active:
            return {"jsonrpc":"2.0","result":{"contributions_aggregated":0,
                    "total_ett":0,"fedavg_result":"NO_CONTRIBUTIONS"},"id":"1"}

        # FedAvg: Δ_global = Σ(ett_i * Δ_i) / Σ ett_i
        # For PoC: deltas are scalar placeholders since real deltas are on Split Server
        total_ett = sum(c["effective_trainable_tokens"] for c in active.values())
        # In PoC, the actual FedAvg of real tensors would happen here
        result = {
            "run_id": run_id,
            "round_id": round_id,
            "contributions_aggregated": len(active),
            "total_ett": total_ett,
            "status": "AGGREGATED",
        }

        # Mark all aggregated contributions
        for c in active.values():
            c["status"] = "AGGREGATED"

        return {"jsonrpc":"2.0","result":result,"id":"1"}

    coord._rc5_extensions = {
        "worker.join": handle_worker_join,
        "work.request": handle_work_request,
        "work.accept": handle_work_accept,
        "checkpoint.upload": handle_checkpoint_upload,
        "admin.validate.contribution": handle_validate_contribution,
        "admin.activate.contribution": handle_activate_contribution,
        "admin.round.close": handle_round_close,
    }
    return coord

def _reject(reason):
    return {"jsonrpc":"2.0","result":{"status":"REJECTED","reason":reason},"id":"1"}

def rpc(coord, method, params, worker_id=""):
    if hasattr(coord, "_rc5_extensions") and method in coord._rc5_extensions:
        return coord._rc5_extensions[method](params, worker_id)
    import json as _j
    from rc3_state import RC3_PROTOCOL_VERSION
    return _j.loads(coord.handle_request(
        {"jsonrpc":"2.0","method":method,"params":params,"id":"t"},
        {"X-Protocol-Version":RC3_PROTOCOL_VERSION,"X-Worker-Id":worker_id,"X-Worker-Token":""}
    ))
