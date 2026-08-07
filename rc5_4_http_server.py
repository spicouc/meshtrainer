"""RC5.4 Stage A R3 — HTTP server with lease-gated dispatcher (REAL integration).

RC54RecoveryCoordinator governs the real JSON-RPC dispatcher: every pipeline
operation (step.open, forward, backward, update, commit, checkpoint.upload)
is routed through it and REQUIRES a valid lease (lease_id, lease_nonce,
run_id, round_id, assignment_id, micro_unit_id, worker_id, session_id).
Missing/invalid/expired lease -> REJECTED (RecoveryError -> RPC error).

Additive: ProtocolHandler54 extends the RC5.2 ProtocolHandler; when
rc54_enabled=True the 6 gated methods are handled by the recovery
coordinator (which delegates execution to the real RC5.2 handlers after the
lease gate). step.abort and everything else stay on the base handler.
"""
import json
import hashlib
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from rc5_2_canonical import canonical_json_v1
from rc5_2_http_server import ProtocolHandler, RpcHandler
from rc5_4_integration import RC54RecoveryCoordinator, RecoveryError

# JSON-RPC methods gated by RC5.4 lease enforcement
RC54_GATED = {
    "step.open": "step_open",
    "step.worker_forward.submit": "worker_forward_submit",
    "step.server_backward.fetch": "server_backward_fetch",
    "step.worker_update.submit": "worker_update_submit",
    "step.commit": "step_commit",
    "checkpoint.upload": "checkpoint_upload",
}


class ProtocolHandler54(ProtocolHandler):
    """RC5.2 ProtocolHandler + mandatory RC5.4 lease gate on the 6 ops."""

    def __init__(self, db_path="rpc_state.db", signing_key=None,
                 rc54_enabled=True, recovery_db=None, coordinator=None):
        super().__init__(db_path=db_path, signing_key=signing_key)
        # autocommit: avoid implicit transactions from the base handler that
        # would block concurrent writers (RoundCoordinator uses the same mode)
        try:
            self._conn.isolation_level = None
        except Exception:
            pass
        self.rc54_enabled = rc54_enabled
        self.recovery = None
        self.rc53_coordinator = None
        if rc54_enabled:
            # share the ProtocolHandler's own connection (single writer)
            self.recovery = RC54RecoveryCoordinator(
                recovery_db or db_path, conn=self._conn)
            self.recovery.attach_pipeline(self)
            self.recovery.attach_coordinator(self.coordinator)
        if coordinator is not None:
            self.attach_coordinator(coordinator)

    def attach_coordinator(self, coord):
        """Bind the RC5.3 RoundCoordinator for contribution registration."""
        self.rc53_coordinator = coord
        if self.recovery is not None:
            self.recovery.attach_coordinator(coord)

    def handle(self, method, params):
        if self.rc54_enabled and method in RC54_GATED:
            if self.recovery is None:
                raise ValueError("RC5.4 recovery not initialized")
            fn = getattr(self.recovery, RC54_GATED[method])
            # same idempotency envelope as the base dispatcher
            with self._lock:
                lk = self._logical_key(method, params)
                sha = hashlib.sha256(canonical_json_v1(params)).hexdigest()
                cached = self._check_idem(lk, sha)
                if cached:
                    return cached
                result = fn(params)
                self._save_idem(lk, sha, result)
                return result
        return super().handle(method, params)


class RpcHandler54(RpcHandler):
    """HTTP handler backed by ProtocolHandler54."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._respond({"jsonrpc": "2.0", "error": {"code": -32700,
                          "message": "Parse error"}, "id": None})
            return
        try:
            method = body.get("method", "")
            params = body.get("params", {})
            result = self.server.proto.handle(method, params)
            resp = {"jsonrpc": "2.0", "result": result, "id": body.get("id")}
        except RecoveryError as e:
            resp = {"jsonrpc": "2.0",
                    "error": {"code": -32042, "message": e.reason,
                              "data": e.details}, "id": body.get("id")}
        except ValueError as e:
            resp = {"jsonrpc": "2.0",
                    "error": {"code": -32003, "message": str(e)},
                    "id": body.get("id")}
        except Exception as e:  # noqa: BLE001
            resp = {"jsonrpc": "2.0",
                    "error": {"code": -32000, "message": str(e)},
                    "id": body.get("id")}
        self._respond(resp)


def serve(host="127.0.0.1", port=19852, db_path="rpc_state.db",
          signing_key=None, rc54_enabled=True, recovery_db=None,
          coordinator=None):
    """Start a lease-gated HTTP server (RC5.4 R3)."""
    import torch  # noqa: F401  (keep parity with base serve)
    server = ThreadingHTTPServer((host, port), RpcHandler54)
    server.proto = ProtocolHandler54(db_path=db_path, signing_key=signing_key,
                                     rc54_enabled=rc54_enabled,
                                     recovery_db=recovery_db,
                                     coordinator=coordinator)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t
