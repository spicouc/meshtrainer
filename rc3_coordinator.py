"""
rc3_coordinator.py — RC3.1 Coordinator (JSON-RPC server).

Protocol version: rc3-protocol-v1
Transport: HTTP JSON-RPC 2.0
Estratègia: agnòstic (serveix per a B, C i futures)

Admin mode (--admin):
  - Activa handlers administratius per a tests E2E
  - NO activat per defecte (segur per a producció)
  - Només vincula a localhost en mode admin
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure the rc3 directory is in path
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from rc3_state import (
    StateStore, TaskState, RC3_PROTOCOL_VERSION,
    LEASE_DEFAULT_SECONDS, HEARTBEAT_TIMEOUT,
    simulated_compute,
)
from rc3_train_state import (
    TrainingRunManager, TrainingRunState, BatchState,
    TRAIN_RC3_PROTOCOL_VERSION, TRAIN_RC3_EXTENSION_VERSION,
    TRAIN_LEASE_DEFAULT_SECONDS, TRAIN_HEARTBEAT_INTERVAL,
    TRAIN_HEARTBEAT_TIMEOUT, TRAIN_SUBMIT_TIMEOUT_SECONDS,
    TRAIN_MAX_ATTEMPTS_PER_BATCH, TRAIN_DEFAULT_MAX_CONCURRENT_BATCHES,
    ERR_GENERAL, ERR_NOT_FOUND, ERR_INVALID_PARAMS, ERR_IDEMPOTENCY_COLLISION,
    ERR_RUN_NOT_TRAINING, ERR_WORKER_NOT_ASSIGNED,
    ERR_DELTA_CONFLICT, ERR_STALE_GENERATION, ERR_NO_BATCHES,
)
from aggregation import FedAvgLoRA
from synthetic_tensor_store import SyntheticTensorStore

logging.basicConfig(level=logging.INFO,
                    format="[%(levelname)s] %(message)s")
log = logging.getLogger("coordinator")

# ── CORS configuration ────────────────────────────────────────────────

# Origins permeses per CORS (llistat blanc)
ALLOWED_ORIGINS_DEFAULT = ["http://localhost:5173", "http://127.0.0.1:5173"]
ALLOWED_METHODS = "POST, GET, OPTIONS"
ALLOWED_HEADERS = "Content-Type, X-Protocol-Version, X-Worker-Id, X-Worker-Token"


# ── Admin method list ─────────────────────────────────────────────────

ADMIN_METHODS = {
    "admin.create_project",
    "admin.create_task",
    "admin.list_workers",
    "admin.get_task",
    "admin.get_worker",
    "admin.get_audit_log",
    "admin.timeout_task",
    "admin.get_protocol_version",
}

ADMIN_TRAIN_METHODS = {
    "admin.train.force_aggregate",
    "admin.train.cancel_run",
    "admin.train.pause_run",
    "admin.train.resume_run",
    "admin.train.retry_run",
}


# ── JSON-RPC helpers ──────────────────────────────────────────────────

def jsonrpc_error(code: int, message: str, request_id: Any = None,
                  data: Optional[dict] = None) -> str:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return json.dumps({
        "jsonrpc": "2.0",
        "error": err,
        "id": request_id,
    })


def jsonrpc_result(result: Any, request_id: Any = None) -> str:
    return json.dumps({
        "jsonrpc": "2.0",
        "result": result,
        "id": request_id,
    })


# ── Task result normalization ───────────────────────────────────────────

def normalize_task_result(task: dict) -> dict:
    """Add public API aliases alongside internal column names.

    Returns a new dict with aliases added; never removes fields.
    """
    t = dict(task)
    t["status"] = t.get("state", t.get("status", ""))
    t["worker_id"] = t.get("assigned_worker_id", t.get("worker_id", ""))
    t["result"] = t.get("output_hash", t.get("result", ""))
    return t


# ── Protocol version extraction ────────────────────────────────────────

def extract_protocol_version(params: dict,
                              header_version: str = "") -> tuple:
    """Extract and validate protocol_version from params and/or header.

    Checks (in priority order):
      1. params['capabilities']['protocol_version'] (worker.ts contract)
      2. params['protocol_version'] (direct field, test compatibility)
      3. header_version (X-Protocol-Version, Python tests)

    Returns: (protocol_version_or_None, error_response_or_None)
    """
    # 1. Check inside capabilities (worker.ts contract)
    caps = params.get("capabilities", {})
    if isinstance(caps, dict):
        raw = caps.get("protocol_version")
        if raw is not None:
            if not isinstance(raw, str):
                return (None, jsonrpc_error(
                    -32602, "Invalid params: protocol_version must be a string",
                    data={"expected": RC3_PROTOCOL_VERSION,
                          "received": str(type(raw).__name__)}))
            if raw != RC3_PROTOCOL_VERSION:
                return (None, jsonrpc_error(
                    -32000, "Unsupported protocol version",
                    data={"expected": RC3_PROTOCOL_VERSION, "received": raw}))
            return (raw, None)

    # 2. Check direct params field (test compatibility)
    raw = params.get("protocol_version")
    if raw is not None:
        if not isinstance(raw, str):
            return (None, jsonrpc_error(
                -32602, "Invalid params: protocol_version must be a string",
                data={"expected": RC3_PROTOCOL_VERSION,
                      "received": str(type(raw).__name__)}))
        if raw != RC3_PROTOCOL_VERSION:
            return (None, jsonrpc_error(
                -32000, "Unsupported protocol version",
                data={"expected": RC3_PROTOCOL_VERSION, "received": raw}))
        return (raw, None)

    # 3. Fall back to header (Python tests, some JS clients)
    if header_version and header_version != RC3_PROTOCOL_VERSION:
        return (None, jsonrpc_error(
            -32000, "Unsupported protocol version",
            data={"expected": RC3_PROTOCOL_VERSION, "received": header_version}))

    if header_version:
        return (header_version, None)

    # 4. Not found anywhere
    return (None, jsonrpc_error(
        -32602, "Invalid params: protocol_version is required",
        data={"expected": RC3_PROTOCOL_VERSION}))


# ── Coordinator ────────────────────────────────────────────────────────

class Coordinator:
    """RC3.1 Coordinator — gestiona workers, tasques, estat."""

    def __init__(self, db_path: str, admin_mode: bool = False,
                 tensor_store: SyntheticTensorStore = None):
        self.store = StateStore(db_path)
        self.train_manager = TrainingRunManager(
            self.store._conn, self.store._lock)
        self.train_manager.ensure_schema()
        self._aggregator = FedAvgLoRA()
        self.tensor_store = tensor_store or SyntheticTensorStore()
        self._lock = threading.Lock()
        self._running = True
        self._admin_mode = admin_mode

        # Store upload_reference -> batch_id mapping for tensor retrieval
        self._upload_refs: Dict[str, str] = {}

        # Start background housekeeping thread
        self._housekeeper = threading.Thread(target=self._housekeeping_loop,
                                             daemon=True)
        self._housekeeper.start()

    def stop(self):
        self._running = False
        self.store.close()

    # ── Properties ──────────────────────────────────────────────────

    @property
    def admin_mode(self) -> bool:
        return self._admin_mode

    # ── Housekeeping ────────────────────────────────────────────────

    def _housekeeping_loop(self):
        """Periodic tasks: timeout expired leases, mark offline workers,
        expire training leases."""
        while self._running:
            time.sleep(15)
            try:
                timed_out = self.store.timeout_tasks()
                for t in timed_out:
                    log.warning(f"Task {t['task_id']} TIMEOUT (lease expired)")
                    self.store.reassign_task(t["task_id"])
                    log.info(f"  → reassigned {t['task_id']} back to PENDING")
                self.store.mark_workers_offline()

                # RC3.3: expire training leases
                try:
                    expired = self.train_manager.expire_leases()
                    for assign in expired:
                        log.info(f"Training lease expired: "
                                 f"batch={assign['batch_id']}, "
                                 f"assignment={assign['assignment_id']}")
                except Exception as e:
                    log.error(f"Training lease expiry error: {e}")
            except Exception as e:
                log.error(f"Housekeeping error: {e}")

    # ── JSON-RPC dispatch ───────────────────────────────────────────

    def handle_request(self, body: dict,
                       headers: Optional[dict] = None) -> str:
        """Dispatch a JSON-RPC request."""
        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id")

        worker_id = (headers or {}).get("X-Worker-Id", "")
        header_proto = (headers or {}).get("X-Protocol-Version", "")

        # Admin methods require admin_mode
        if method in ADMIN_METHODS and not self._admin_mode:
            return jsonrpc_error(-32001,
                                 f"Admin mode not enabled (method: {method})",
                                 req_id)

        # Admin train methods require admin_mode
        if method in ADMIN_TRAIN_METHODS and not self._admin_mode:
            return jsonrpc_error(-32001,
                                 f"Admin mode not enabled (method: {method})",
                                 req_id)

        handlers = {
            "worker.register": self._handle_register,
            "worker.heartbeat": self._handle_heartbeat,
            "task.request": self._handle_task_request,
            "task.submit": self._handle_task_submit,
            "worker.status": self._handle_worker_status,
            "admin.ping": self._handle_ping,
            "admin.recover": self._handle_recover,
            "admin.tasks": self._handle_list_tasks,
            "admin.audit": self._handle_audit,
            "admin.create_project": self._handle_create_project,
            "admin.create_task": self._handle_create_task,
            "admin.list_workers": self._handle_list_workers,
            "admin.get_task": self._handle_get_task,
            "admin.get_worker": self._handle_get_worker,
            "admin.get_audit_log": self._handle_get_audit_log,
            "admin.timeout_task": self._handle_timeout_task,
            "admin.get_protocol_version": self._handle_get_protocol_version,
            # RC3.3 training methods
            "train.create_run": self._handle_train_create_run,
            "train.get_run": self._handle_train_get_run,
            "train.list_runs": self._handle_train_list_runs,
            "train.request_batch": self._handle_train_request_batch,
            "train.report_progress": self._handle_train_report_progress,
            "train.complete_upload": self._handle_train_complete_upload,
            "train.submit_delta": self._handle_train_submit_delta,
            # RC3.3 admin training methods
            "admin.train.force_aggregate": self._handle_admin_force_aggregate,
            "admin.train.cancel_run": self._handle_admin_cancel_run,
            "admin.train.pause_run": self._handle_admin_pause_run,
            "admin.train.resume_run": self._handle_admin_resume_run,
            "admin.train.retry_run": self._handle_admin_retry_run,
        }

        handler = handlers.get(method)
        if not handler:
            return jsonrpc_error(-32601, f"Method not found: {method}", req_id)

        try:
            header_proto = (headers or {}).get("X-Protocol-Version", "")
            # Non-register methods: reject mismatched header version
            if method != "worker.register" and header_proto \
               and header_proto != RC3_PROTOCOL_VERSION:
                return jsonrpc_error(-32000,
                    f"Unsupported protocol version: {header_proto}",
                    req_id)
            if method == "worker.register":
                return handler(params, req_id, worker_id, header_proto)
            return handler(params, req_id, worker_id)
        except Exception as e:
            log.exception(f"Handler error: {method}")
            return jsonrpc_error(-32603, f"Internal error: {e}", req_id)

    # ── Worker handlers ─────────────────────────────────────────────

    def _handle_register(self, params: dict, req_id: Any,
                         worker_id: str,
                         header_version: str = "") -> str:
        # Validate protocol version
        proto, err = extract_protocol_version(params, header_version)
        if err is not None:
            return err

        caps = params.get("capabilities", {})
        wtype = caps.get("type", params.get("type", "python"))
        wid, token = self.store.register_worker(wtype, caps, proto)
        log.info(f"Worker registered: {wid} (type={wtype}, protocol={proto})")
        return jsonrpc_result({
            "worker_id": wid,
            "auth_token": token,
            "protocol_version": RC3_PROTOCOL_VERSION,
            "lease_default_seconds": LEASE_DEFAULT_SECONDS,
            "heartbeat_interval_seconds": 30,
        }, req_id)

    def _handle_heartbeat(self, params: dict, req_id: Any,
                          worker_id: str) -> str:
        wid = params.get("worker_id", worker_id)
        status = params.get("status", "idle")
        self.store.update_worker_heartbeat(wid, status)
        return jsonrpc_result({
            "status": "ok",
            "pending_actions": [],
        }, req_id)

    def _handle_task_request(self, params: dict, req_id: Any,
                             worker_id: str) -> str:
        strategy = params.get("preferred_strategy")
        tasks = self.store.get_pending_tasks(strategy=strategy)
        if not tasks:
            return jsonrpc_result({
                "task": None,
                "retry_after_seconds": 15,
            }, req_id)

        task = tasks[0]
        success = self.store.assign_task(
            task["task_id"], worker_id,
            lease_seconds=LEASE_DEFAULT_SECONDS,
        )
        if not success:
            return jsonrpc_result({
                "task": None,
                "retry_after_seconds": 5,
            }, req_id)

        log.info(f"Task {task['task_id']} assigned to {worker_id}")
        return jsonrpc_result({
            "task": {
                "task_id": task["task_id"],
                "run_id": task["run_id"],
                "protocol_version": RC3_PROTOCOL_VERSION,
                "strategy": task.get("strategy", "simulated"),
                "config_hash": task.get("config_hash"),
                "input_hash": task.get("input_hash"),
                "lease_expires_at": task["lease_expires_at"],
                "expected_output": ["result.txt", "manifest.json"],
                "task_params": json.loads(task.get("task_params", "{}")) if task.get("task_params") else {},
            }
        }, req_id)

    def _handle_task_submit(self, params: dict, req_id: Any,
                            worker_id: str) -> str:
        task_id = params.get("task_id", "")
        status = params.get("status", "completed")
        output_hash = params.get("output_hash")
        meta = params.get("execution_metadata", {})

        task = self.store.get_task(task_id)
        if task and task.get("input_hash") and status == "completed":
            expected = simulated_compute(
                task["input_hash"],
                int(task.get("config_hash", "42")[:8], 16) if task.get("config_hash") else 42,
            )
            if output_hash and output_hash != expected:
                self.store.reject_result(task_id, worker_id,
                                         "output_hash_mismatch")
                log.warning(f"Task {task_id}: output hash mismatch "
                            f"(got {output_hash}, expected {expected})")
                return jsonrpc_result({
                    "status": "rejected",
                    "validated_ok": False,
                    "validation_details": {"reason": "output_hash_mismatch"},
                    "next_action": "wait",
                }, req_id)

        result = self.store.submit_result(
            task_id, worker_id, status,
            output_hash=output_hash,
            execution_metadata=meta,
        )

        if result["accepted"]:
            log.info(f"Task {task_id} COMPLETED by {worker_id}")
            return jsonrpc_result({
                "status": "accepted",
                "validated_ok": True,
                "validation_details": {},
                "next_action": "request_new",
            }, req_id)
        else:
            log.warning(f"Task {task_id} rejected: {result['reason']}")
            return jsonrpc_result({
                "status": "rejected",
                "validated_ok": False,
                "validation_details": {"reason": result["reason"]},
                "next_action": "wait",
            }, req_id)

    def _handle_worker_status(self, params: dict, req_id: Any,
                              worker_id: str) -> str:
        worker = self.store.get_worker(worker_id)
        if not worker:
            return jsonrpc_error(-32001, "Worker not found", req_id)
        return jsonrpc_result({
            "worker_id": worker["worker_id"],
            "status": worker["status"],
            "worker_type": worker["worker_type"],
            "last_heartbeat_at": worker["last_heartbeat_at"],
            "protocol_version": worker["protocol_version"],
        }, req_id)

    def _handle_ping(self, params: dict, req_id: Any,
                     worker_id: str) -> str:
        return jsonrpc_result({
            "status": "ok",
            "protocol_version": RC3_PROTOCOL_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, req_id)

    def _handle_recover(self, params: dict, req_id: Any,
                        worker_id: str) -> str:
        self.store.recover_after_restart()
        log.info("Recovery completed after restart")
        return jsonrpc_result({"status": "recovered"}, req_id)

    def _handle_list_tasks(self, params: dict, req_id: Any,
                           worker_id: str) -> str:
        run_id = params.get("run_id")
        if run_id:
            tasks = self.store.get_tasks_by_run(run_id)
        else:
            tasks = self.store.get_pending_tasks()
        return jsonrpc_result({
            "tasks": [{k: v for k, v in t.items()
                       if k != "auth_token"}
                      for t in tasks],
        }, req_id)

    def _handle_audit(self, params: dict, req_id: Any,
                      worker_id: str) -> str:
        task_id = params.get("task_id")
        log_entries = self.store.get_audit_log(task_id=task_id)
        return jsonrpc_result({"audit_log": log_entries}, req_id)

    # ── Admin helpers (only when admin_mode=True) ────────────────────

    def _handle_create_project(self, params: dict, req_id: Any,
                               worker_id: str) -> str:
        name = params.get("name", "unnamed")
        config_hash = params.get("config_hash")
        pid = self.store.create_project(name, config_hash)
        return jsonrpc_result({"project_id": pid}, req_id)

    def _handle_create_task(self, params: dict, req_id: Any,
                            worker_id: str) -> str:
        project_id = params.get("project_id", "p-unknown")
        run_id = params.get("run_id", f"run-{uuid.uuid4().hex[:8]}")
        strategy = params.get("strategy", "simulated")
        config_hash = params.get("config_hash")
        input_hash = params.get("input_hash")
        task_params = params.get("task_params")
        tid = self.store.create_task(run_id, project_id, strategy,
                                     config_hash, input_hash, task_params)
        return jsonrpc_result({"task_id": tid}, req_id)

    def _handle_list_workers(self, params: dict, req_id: Any,
                             worker_id: str) -> str:
        all_w = []
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT * FROM worker"
            ).fetchall()
            all_w = [dict(r) for r in rows]
        for w in all_w:
            w.pop("auth_token", None)
        return jsonrpc_result({"workers": all_w}, req_id)

    def _handle_get_task(self, params: dict, req_id: Any,
                         worker_id: str) -> str:
        task_id = params.get("task_id", "")
        row = self.store.get_task(task_id)
        if row:
            row.pop("auth_token", None)
            row = normalize_task_result(row)
        return jsonrpc_result(row or {"error": "not_found"}, req_id)

    def _handle_get_worker(self, params: dict, req_id: Any,
                           worker_id: str) -> str:
        wid = params.get("worker_id", "")
        row = self.store.get_worker(wid)
        if row:
            row.pop("auth_token", None)
        return jsonrpc_result(row or {"error": "not_found"}, req_id)

    def _handle_get_audit_log(self, params: dict, req_id: Any,
                              worker_id: str) -> str:
        task_id = params.get("task_id")
        entries = self.store.get_audit_log(task_id=task_id)
        return jsonrpc_result({"audit_log": entries}, req_id)

    def _handle_timeout_task(self, params: dict, req_id: Any,
                             worker_id: str) -> str:
        task_id = params.get("task_id", "")
        self.store._conn.execute(
            "UPDATE task SET state='TIMEOUT' WHERE task_id=? AND state='ASSIGNED'",
            (task_id,),
        )
        self.store._conn.commit()
        self.store._audit("task_timed_out", task_id=task_id)
        return jsonrpc_result({"status": "timed_out"}, req_id)

    def _handle_get_protocol_version(self, params: dict, req_id: Any,
                                     worker_id: str) -> str:
        return jsonrpc_result({
            "protocol_version": RC3_PROTOCOL_VERSION,
        }, req_id)

    # ── RC3.3 Training handlers ──────────────────────────────────────

    def _handle_train_create_run(self, params: dict, req_id: Any,
                                 worker_id: str) -> str:
        run_id, err = self.train_manager.create_run(params)
        if err is not None:
            return err

        run = self.train_manager.get_run(run_id)
        if not run:
            return jsonrpc_error(ERR_INTERNAL, "Run created but not found", req_id)

        batches = [
            {"batch_id": b["batch_id"], "batch_index": b["batch_index"],
             "status": b["status"]}
            for b in run.get("batches", [])
        ]
        return jsonrpc_result({
            "run_id": run_id,
            "status": run["status"],
            "aggregation_generation": run["aggregation_generation"],
            "batches": batches,
            "created_at": run["created_at"],
        }, req_id)

    def _handle_train_get_run(self, params: dict, req_id: Any,
                              worker_id: str) -> str:
        run_id = params.get("run_id", "")
        if not run_id:
            return jsonrpc_error(ERR_INVALID_PARAMS, "run_id is required", req_id)

        run = self.train_manager.get_run(run_id)
        if not run:
            return jsonrpc_result({"error": "not_found"}, req_id)

        batches = []
        for b in run.get("batches", []):
            batch_info = {
                "batch_id": b["batch_id"],
                "batch_index": b["batch_index"],
                "status": b["status"],
            }
            # Add assignment details if available
            assign = self.train_manager.get_active_assignment(b["batch_id"])
            if assign:
                batch_info["worker_session_id"] = assign["worker_session_id"]
            if b["status"] == "COMPLETED":
                assign_hist = self.train_manager._conn.execute(
                    "SELECT loss, samples_processed FROM batch_assignment "
                    "WHERE batch_id=? AND result_status='accepted'",
                    (b["batch_id"],)
                ).fetchone()
                if assign_hist:
                    batch_info["loss"] = assign_hist["loss"]
                    batch_info["samples_processed"] = assign_hist["samples_processed"]
            batches.append(batch_info)

        completed = self.train_manager.get_batch_count_by_status(run_id, "COMPLETED")
        return jsonrpc_result({
            "run_id": run["run_id"],
            "status": run["status"],
            "aggregation_generation": run["aggregation_generation"],
            "batches": batches,
            "aggregated_batches": completed,
            "total_batches": run["total_batches"],
            "started_at": run.get("started_at", ""),
            "updated_at": run["updated_at"],
        }, req_id)

    def _handle_train_list_runs(self, params: dict, req_id: Any,
                                worker_id: str) -> str:
        status_filter = params.get("status")
        limit = params.get("limit", 10)
        runs, total = self.train_manager.list_runs(status=status_filter, limit=limit)

        run_list = []
        for r in runs:
            run_list.append({
                "run_id": r["run_id"],
                "status": r["status"],
                "aggregation_generation": r["aggregation_generation"],
                "total_batches": r["total_batches"],
                "completed_batches": r.get("completed_batches", 0),
                "created_at": r["created_at"],
            })

        return jsonrpc_result({
            "runs": run_list,
            "total_count": total,
        }, req_id)

    def _handle_train_request_batch(self, params: dict, req_id: Any,
                                    worker_id: str) -> str:
        run_id = params.get("run_id", "")
        worker_session_id = params.get("worker_session_id", "")

        if not run_id:
            return jsonrpc_error(ERR_INVALID_PARAMS, "run_id is required", req_id)
        if not worker_session_id:
            return jsonrpc_error(ERR_INVALID_PARAMS,
                                 "worker_session_id is required", req_id)

        # Check run is in TRAINING state (or WAITING which transitions on first assign)
        run = self.train_manager.get_run_raw(run_id)
        if not run:
            return jsonrpc_error(ERR_NOT_FOUND, f"Run not found: {run_id}", req_id)

        if run["status"] not in ("WAITING", "TRAINING"):
            return jsonrpc_error(ERR_RUN_NOT_TRAINING,
                                 f"Run is not in TRAINING state (status={run['status']})",
                                 req_id)

        if run["status"] == "PAUSED":
            return jsonrpc_error(ERR_RUN_NOT_TRAINING,
                                 "Run is PAUSED, no batches available", req_id)

        # Get next waiting batch
        batch = self.train_manager.get_next_waiting_batch(run_id)
        if not batch:
            return jsonrpc_error(ERR_NO_BATCHES,
                                 "No batches available for assignment", req_id)

        attempt = batch["attempt_number"] + 1  # increment from last attempt
        if attempt > TRAIN_MAX_ATTEMPTS_PER_BATCH:
            # Mark batch as FAILED
            self.train_manager.transition_batch(batch["batch_id"], "FAILED")
            return jsonrpc_error(ERR_NO_BATCHES,
                                 "Batch max attempts exceeded", req_id)

        # Validate generation compatibility if capabilities provided
        caps = params.get("capabilities", {})
        caps_model_rev = caps.get("model_revision")
        if caps_model_rev and caps_model_rev != run["model_revision"]:
            return jsonrpc_error(ERR_STALE_GENERATION,
                                 f"Worker model_revision mismatch: "
                                 f"worker={caps_model_rev}, "
                                 f"run={run['model_revision']}",
                                 req_id)

        # Assign batch
        result = self.train_manager.assign_batch(
            run_id, batch["batch_id"], worker_id,
            worker_session_id, attempt_number=attempt,
        )

        if not result:
            return jsonrpc_error(ERR_INTERNAL,
                                 "Failed to assign batch", req_id)

        return jsonrpc_result(result, req_id)

    def _handle_train_report_progress(self, params: dict, req_id: Any,
                                      worker_id: str) -> str:
        run_id = params.get("run_id", "")
        batch_id = params.get("batch_id", "")
        assignment_id = params.get("assignment_id", "")
        lease_token = params.get("lease_token", "")
        progress_pct = params.get("progress_pct")

        # Validate assignment
        assign = self.train_manager.get_active_assignment(batch_id)
        if not assign:
            return jsonrpc_error(ERR_WORKER_NOT_ASSIGNED,
                                 "No active assignment for this batch", req_id)
        if assign["lease_token"] != lease_token:
            return jsonrpc_error(ERR_WORKER_NOT_ASSIGNED,
                                 "lease_token mismatch", req_id)
        if assign["worker_session_id"] != params.get("worker_session_id"):
            return jsonrpc_error(ERR_WORKER_NOT_ASSIGNED,
                                 "worker_session_id mismatch", req_id)

        # Extend lease
        new_expiry = self.train_manager.extend_lease(assignment_id)

        # Update batch to TRAINING if not already
        batch = self.train_manager.get_batch(batch_id)
        if batch and batch["status"] == "ASSIGNED":
            self.train_manager.transition_batch(batch_id, "TRAINING")

        # Update assignment progress
        if progress_pct is not None:
            with self.train_manager._lock:
                self.train_manager._conn.execute(
                    "UPDATE batch_assignment SET progress_pct=? "
                    "WHERE assignment_id=?",
                    (progress_pct, assignment_id),
                )
                self.train_manager._conn.commit()

        return jsonrpc_result({
            "accepted": True,
            "lease_extended_to": new_expiry or assign["expires_at"],
        }, req_id)

    def _handle_train_complete_upload(self, params: dict, req_id: Any,
                                      worker_id: str) -> str:
        run_id = params.get("run_id", "")
        batch_id = params.get("batch_id", "")
        assignment_id = params.get("assignment_id", "")
        lease_token = params.get("lease_token", "")

        # Validate assignment
        assign = self.train_manager.get_active_assignment(batch_id)
        if not assign:
            return jsonrpc_error(ERR_WORKER_NOT_ASSIGNED,
                                 "No active assignment for this batch", req_id)
        if assign["lease_token"] != lease_token:
            return jsonrpc_error(ERR_WORKER_NOT_ASSIGNED,
                                 "lease_token mismatch", req_id)

        # Transition batch to UPLOADING
        self.train_manager.transition_batch(batch_id, "UPLOADING")

        # Extend lease for submit timeout
        self.train_manager.extend_lease(
            assignment_id, TRAIN_SUBMIT_TIMEOUT_SECONDS)

        upload_id = f"up-{uuid.uuid4().hex[:8]}"
        return jsonrpc_result({
            "accepted": True,
            "upload_id": upload_id,
            "status": "UPLOADING",
        }, req_id)

    def _handle_train_submit_delta(self, params: dict, req_id: Any,
                                   worker_id: str) -> str:
        run_id = params.get("run_id", "")
        batch_id = params.get("batch_id", "")

        if not run_id or not batch_id:
            return jsonrpc_error(ERR_INVALID_PARAMS,
                                 "run_id and batch_id are required", req_id)

        # Validate delta manifest if provided
        manifest = params.get("delta_manifest", {})
        if manifest:
            run = self.train_manager.get_run_raw(run_id)
            if run:
                validation = self._aggregator.validate_delta_manifest(
                    manifest, run)
                if not validation["valid"]:
                    return jsonrpc_error(ERR_GENERAL,
                                         f"Manifest validation failed: "
                                         f"{validation['reason']}", req_id)

        # Submit via training manager (15 checks)
        result = self.train_manager.submit_delta(run_id, batch_id, params,
                                                  worker_id)

        if result["accepted"]:
            # Store upload_reference mapping for tensor retrieval
            upload_ref = params.get("upload_reference", "")
            if upload_ref:
                self._upload_refs[f"{run_id}:{batch_id}"] = upload_ref

            # Re-read run to get updated counts
            run = self.train_manager.get_run_raw(run_id)
            completed = self.train_manager.get_batch_count_by_status(
                run_id, "COMPLETED")
            return jsonrpc_result({
                "batch_id": batch_id,
                "status": "COMPLETED",
                "aggregated_batches": completed,
                "total_batches": run["total_batches"] if run else 0,
                "run_status": run["status"] if run else "UNKNOWN",
            }, req_id)
        else:
            err_code = result.get("error_code", ERR_GENERAL)
            return jsonrpc_error(err_code, result.get("reason", "rejected"),
                                 req_id)

    def _handle_admin_force_aggregate(self, params: dict, req_id: Any,
                                      worker_id: str) -> str:
        run_id = params.get("run_id", "")
        if not run_id:
            return jsonrpc_error(ERR_INVALID_PARAMS, "run_id is required", req_id)

        result = self.train_manager.force_aggregate(run_id)
        if result["status"] == "error":
            return jsonrpc_error(ERR_NOT_FOUND, result["reason"], req_id)

        return jsonrpc_result(result, req_id)

    def _handle_admin_cancel_run(self, params: dict, req_id: Any,
                                 worker_id: str) -> str:
        run_id = params.get("run_id", "")
        if not run_id:
            return jsonrpc_error(ERR_INVALID_PARAMS, "run_id is required", req_id)

        result = self.train_manager.cancel_run(run_id)
        if result["status"] == "error":
            return jsonrpc_error(ERR_NOT_FOUND, result["reason"], req_id)

        return jsonrpc_result(result, req_id)

    def _handle_admin_pause_run(self, params: dict, req_id: Any,
                                worker_id: str) -> str:
        run_id = params.get("run_id", "")
        if not run_id:
            return jsonrpc_error(ERR_INVALID_PARAMS, "run_id is required", req_id)

        result = self.train_manager.pause_run(run_id)
        if result["status"] == "error":
            return jsonrpc_error(ERR_NOT_FOUND, result["reason"], req_id)

        return jsonrpc_result(result, req_id)

    def _handle_admin_resume_run(self, params: dict, req_id: Any,
                                 worker_id: str) -> str:
        run_id = params.get("run_id", "")
        if not run_id:
            return jsonrpc_error(ERR_INVALID_PARAMS, "run_id is required", req_id)

        result = self.train_manager.resume_run(run_id)
        if result["status"] == "error":
            return jsonrpc_error(ERR_NOT_FOUND, result["reason"], req_id)

        return jsonrpc_result(result, req_id)

    def _handle_admin_retry_run(self, params: dict, req_id: Any,
                                worker_id: str) -> str:
        run_id = params.get("run_id", "")
        if not run_id:
            return jsonrpc_error(ERR_INVALID_PARAMS, "run_id is required", req_id)

        result = self.train_manager.retry_run(run_id)
        if result["status"] == "error":
            return jsonrpc_error(ERR_NOT_FOUND, result["reason"], req_id)

        return jsonrpc_result(result, req_id)


# ── HTTP Server ────────────────────────────────────────────────────────

class CoordinatorHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler that wraps Coordinator's JSON-RPC."""

    coordinator: Coordinator = None  # Set by server
    allowed_origins: List[str] = ALLOWED_ORIGINS_DEFAULT  # Override per test

    def log_message(self, format, *args):
        pass  # Suppress HTTP log (we use our own)

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, jsonrpc_error(-32700, "Parse error"))
            return

        headers = {
            "X-Protocol-Version": self.headers.get("X-Protocol-Version", ""),
            "X-Worker-Id": self.headers.get("X-Worker-Id", ""),
            "X-Worker-Token": self.headers.get("X-Worker-Token", ""),
        }

        response = self.coordinator.handle_request(req, headers=headers)
        self._send_json(200, response)

    def do_GET(self):
        self.send_response(200)
        self._send_cors_headers()
        if self.path == "/health":
            body = json.dumps({
                "status": "ok",
                "protocol_version": RC3_PROTOCOL_VERSION,
            })
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
        else:
            self.end_headers()

    def _send_cors_headers(self):
        """Send CORS headers restricted to allowed origins."""
        origin = self.headers.get("Origin", "")
        # If origin is in allowed list, echo it back; otherwise use first allowed
        if origin in self.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
        elif self.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", self.allowed_origins[0])
        else:
            self.send_header("Access-Control-Allow-Origin", "http://localhost:5173")
        self.send_header("Access-Control-Allow-Methods", ALLOWED_METHODS)
        self.send_header("Access-Control-Allow-Headers", ALLOWED_HEADERS)
        self.send_header("Access-Control-Allow-Credentials", "false")

    def _send_json(self, status: int, body: str):
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Protocol-Version", RC3_PROTOCOL_VERSION)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


# ── Server entry points ───────────────────────────────────────────────

def serve(db_path: str, host: str = "0.0.0.0", port: int = 8791,
          admin_mode: bool = False, allowed_origins: Optional[List[str]] = None):
    """Start the coordinator server."""
    coordinator = Coordinator(db_path, admin_mode=admin_mode)
    CoordinatorHTTPHandler.coordinator = coordinator
    if allowed_origins is not None:
        CoordinatorHTTPHandler.allowed_origins = allowed_origins

    server = HTTPServer((host, port), CoordinatorHTTPHandler)
    log.info(f"RC3.1 Coordinator on {host}:{port} (db={db_path}, admin={admin_mode})")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        coordinator.stop()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="RC3.1 Coordinator")
    parser.add_argument("--db", default="/tmp/rc3_coordinator.db",
                        help="SQLite database path")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--recover", action="store_true",
                        help="Recover state after restart before serving")
    parser.add_argument("--admin", action="store_true",
                        help="Enable admin handlers (for testing)")
    args = parser.parse_args()

    coordinator = Coordinator(args.db, admin_mode=args.admin)
    if args.recover:
        coordinator.store.recover_after_restart()
        log.info("Recovery applied before starting server")

    CoordinatorHTTPHandler.coordinator = coordinator
    server = HTTPServer((args.host, args.port), CoordinatorHTTPHandler)
    mode = "admin" if args.admin else "production"
    log.info(f"RC3.1 Coordinator on {args.host}:{args.port} "
             f"(db={args.db}, mode={mode})")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutdown")
    finally:
        coordinator.stop()
        server.server_close()


if __name__ == "__main__":
    main()
