"""RC5.1 — Coordinator extensions: worker.join, work.request, ledger."""
import json
import time
import hashlib
import uuid

from rc3_coordinator import Coordinator

def rpc_success(result, id_="1"):
    return {"jsonrpc": "2.0", "result": result, "id": id_}

def rpc_error(code, message, id_="1"):
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": id_}

def patch_coordinator(coord):
    """Patch a Coordinator instance with RC5 extensions."""

    # Ledger: contributions per (run, round, assignment, worker)
    if not hasattr(coord, "_rc5_ledger"):
        coord._rc5_ledger = {}  # key: (run_id, round_id, assignment_id, worker_id) -> contribution
    if not hasattr(coord, "_rc5_workers"):
        coord._rc5_workers = {}  # worker_id -> {session_id, status, joined_at}
    if not hasattr(coord, "_rc5_assignments"):
        coord._rc5_assignments = {}  # assignment_id -> {worker_id, unit_ids, status}

    # ---- worker.join ----
    def handle_worker_join(params, worker_id=""):
        session_id = params.get("session_id", str(uuid.uuid4()))
        auth_token = params.get("auth_token", "")
        coord._rc5_workers[worker_id] = {
            "session_id": session_id,
            "status": "REGISTERED",
            "joined_at": time.time(),
            "auth_token": auth_token,
        }
        return rpc_success({
            "worker_id": worker_id,
            "session_id": session_id,
            "status": "REGISTERED",
        })

    # ---- work.request ----
    def handle_work_request(params, worker_id=""):
        if worker_id not in coord._rc5_workers:
            return rpc_error(-32010, f"Worker not registered: {worker_id}")
        worker = coord._rc5_workers[worker_id]
        if worker["status"] not in ("REGISTERED", "IDLE"):
            return rpc_error(-32011, f"Worker status not eligible: {worker['status']}")
        # Check if there's a pending assignment
        for aid, asc in coord._rc5_assignments.items():
            if asc["worker_id"] == worker_id and asc["status"] == "PENDING":
                worker["status"] = "ASSIGNED"
                return rpc_success({
                    "assignment_id": aid,
                    "unit_count": len(asc["unit_ids"]),
                    "status": "ASSIGNED",
                    "micro_unit_ids": asc["unit_ids"],
                })
        return rpc_success({
            "assignment_id": None,
            "status": "NO_WORK",
        })

    # ---- accept work ----
    def handle_work_accept(params, worker_id=""):
        assignment_id = params.get("assignment_id")
        if assignment_id not in coord._rc5_assignments:
            return rpc_error(-32012, f"Unknown assignment: {assignment_id}")
        asc = coord._rc5_assignments[assignment_id]
        if asc["worker_id"] != worker_id:
            return rpc_error(-32013, f"Assignment not for this worker")
        asc["status"] = "ACTIVE"
        if worker_id in coord._rc5_workers:
            coord._rc5_workers[worker_id]["status"] = "ACTIVE"
        return rpc_success({
            "assignment_id": assignment_id,
            "status": "ACTIVE",
        })

    # ---- contribution submission (checkpoint.upload) ----
    def handle_checkpoint_upload(params, worker_id=""):
        assignment_id = params.get("assignment_id")
        receipt = params.get("receipt", {})
        delta_sha256 = params.get("delta_sha256", "")
        if assignment_id not in coord._rc5_assignments:
            return rpc_error(-32012, f"Unknown assignment: {assignment_id}")
        asc = coord._rc5_assignments[assignment_id]
        run_id = params.get("run_id", "")
        round_id = params.get("round_id", "")
        key = (run_id, round_id, assignment_id, worker_id)
        # Check existing contribution
        existing = coord._rc5_ledger.get(key)
        contribution_id = hashlib.sha256(f"{assignment_id}:{time.time()}".encode()).hexdigest()[:16]
        revision = (existing["revision"] + 1) if existing else 1
        coord._rc5_ledger[key] = {
            "contribution_id": contribution_id,
            "revision": revision,
            "supersedes_contribution_id": existing["contribution_id"] if existing else None,
            "status": "RECEIVED",
            "receipt": receipt,
            "delta_sha256": delta_sha256,
            "worker_id": worker_id,
            "assignment_id": assignment_id,
            "run_id": run_id,
            "round_id": round_id,
            "submitted_at": time.time(),
            "checkpoint_id": None,
        }
        # If previous was ACTIVE, mark it SUPERSEDED
        if existing and existing["status"] == "ACTIVE":
            existing["status"] = "SUPERSEDED"
        return rpc_success({
            "contribution_id": contribution_id,
            "revision": revision,
            "status": "RECEIVED",
        })

    # ---- FedAvg at round close ----
    def handle_round_close(params, worker_id=""):
        run_id = params.get("run_id")
        round_id = params.get("round_id")
        if not run_id or not round_id:
            return rpc_error(-32602, "run_id and round_id required")
        # Collect all ACTIVE contributions for this round
        active_contribs = {}
        for key, contrib in coord._rc5_ledger.items():
            kr, rr, ar, wr = key
            if kr == run_id and rr == round_id and contrib["status"] in ("RECEIVED", "VALIDATED", "ACTIVE"):
                # One per assignment
                if ar not in active_contribs:
                    active_contribs[ar] = contrib
                else:
                    # Keep only the most recent revision
                    if contrib["revision"] > active_contribs[ar]["revision"]:
                        active_contribs[ar]["status"] = "SUPERSEDED"
                        active_contribs[ar] = contrib
        # Mark as ACTIVE then AGGREGATED
        total_ett = 0
        weighted_delta = {}
        for aid, contrib in active_contribs.items():
            contrib["status"] = "ACTIVE"
            ett = contrib.get("receipt", {}).get("effective_trainable_tokens", 0)
            total_ett += ett
            contrib["status"] = "AGGREGATED"
        return rpc_success({
            "run_id": run_id,
            "round_id": round_id,
            "contributions_aggregated": len(active_contribs),
            "total_effective_trainable_tokens": total_ett,
            "status": "AGGREGATED",
        })

    # Register handlers
    handlers = {
        "worker.join": handle_worker_join,
        "work.request": handle_work_request,
        "work.accept": handle_work_accept,
        "checkpoint.upload": handle_checkpoint_upload,
        "admin.round.close": handle_round_close,
    }
    coord._rc5_extensions = handlers
    return coord

def rpc(coord, method, params, worker_id=""):
    """Call an RC5 extension on a patched coordinator."""
    if hasattr(coord, "_rc5_extensions") and method in coord._rc5_extensions:
        return coord._rc5_extensions[method](params, worker_id)
    # Fallback to original handle_request
    import json as _j
    from rc3_state import RC3_PROTOCOL_VERSION
    body = _j.dumps({"jsonrpc":"2.0","method":method,"params":params,"id":"t"})
    return _j.loads(coord.handle_request(
        {"jsonrpc":"2.0","method":method,"params":params,"id":"t"},
        {"X-Protocol-Version":RC3_PROTOCOL_VERSION,"X-Worker-Id":worker_id,"X-Worker-Token":""}
    ))
