"""Client del protocol core (JSON-RPC HTTP) per al servei d'aplicació.

El producte parla amb el core certificat EXACTAMENT com un client més del
protocol HTTP (training_http_server): mateixos mètodes, mateix admin_token.
Aquest mòdul NO és un driver de test ni modifica el core.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class CoreRpcError(Exception):
    def __init__(self, method: str, reason: str, details: Any = None):
        super().__init__(f"{method}: {reason}")
        self.method = method
        self.reason = reason
        self.details = details


def rpc(server_url: str, method: str, params: dict, timeout: int = 300) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params,
                       "id": 1}).encode("utf-8")
    req = urllib.request.Request(server_url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise CoreRpcError(method, f"core no accessible: {e}")
    if "error" in out:
        err = out["error"]
        raise CoreRpcError(method, err.get("message", str(err)),
                           err.get("data"))
    return out.get("result")


class CoreClient:
    """Operacions ADMIN/protocol sobre el servidor core."""

    def __init__(self, server_url: str, admin_token: str):
        self.url = server_url
        self.admin_token = admin_token

    def _admin(self, method: str, params: dict) -> Any:
        params = dict(params)
        params["admin_token"] = self.admin_token
        return rpc(self.url, method, params)

    # ── operacions ADMIN (frontera certificada) ──────────────────────────
    def round_create(self, run_id, round_id, backend_id, base_model_hash,
                     adapter_0_sha, adapter_pre_hash, dataset_manifest_sha=""):
        return self._admin("round.create", {
            "run_id": run_id, "round_id": round_id, "backend_id": backend_id,
            "base_model_hash": base_model_hash, "adapter_0_sha": adapter_0_sha,
            "adapter_pre_hash": adapter_pre_hash,
            "dataset_manifest_sha": dataset_manifest_sha})

    def assignment_create(self, run_id, round_id, assignment_id, worker_id,
                          shard_id, shard_manifest_sha, base_model_hash,
                          adapter_0_sha, expected_ett, revision=1):
        return self._admin("assignment.create", {
            "run_id": run_id, "round_id": round_id,
            "assignment_id": assignment_id, "worker_id": worker_id,
            "shard_id": shard_id, "shard_manifest_sha": shard_manifest_sha,
            "base_model_hash": base_model_hash, "adapter_0_sha": adapter_0_sha,
            "expected_ett": expected_ett, "revision": revision})

    def contribution_validate(self, contribution_id: str):
        return self._admin("contribution.validate",
                           {"contribution_id": contribution_id})

    def contribution_activate(self, contribution_id: str):
        return self._admin("contribution.activate",
                           {"contribution_id": contribution_id})

    def round_fedavg(self, run_id, round_id, adapter_0_bundle_b64: str,
                     assignment_id="asg-A", worker_id="w-A"):
        return self._admin("round.fedavg", {
            "run_id": run_id, "round_id": round_id,
            "assignment_id": assignment_id, "worker_id": worker_id,
            "adapter_0_bundle_b64": adapter_0_bundle_b64})

    # ── operacions de consulta (no ADMIN) ────────────────────────────────
    def contribution_register_status(self, contribution_id: str):
        return rpc(self.url, "contribution.register",
                   {"contribution_id": contribution_id})
