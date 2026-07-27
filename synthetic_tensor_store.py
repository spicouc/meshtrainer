"""
synthetic_tensor_store.py — RC3.3.2 In-memory tensor store with SHA-256 validation.

Thread-safe in-memory store for synthetic tensor payloads.
Used for explicit tensor transport between TrainingWorker and Coordinator.

Upload reference format: "synthetic://<run_id>/<batch_id>/<delta_sha256>[:12]"
References are immutable: same payload → same reference.
"""

import hashlib
import threading
from typing import Optional


class SyntheticTensorStore:
    """Thread-safe in-memory store for synthetic tensor payloads.

    Stores bytes payloads keyed by upload_reference, organized by run_id.
    Validates SHA-256 on put(). Rejects overwrites with different content.
    """

    def __init__(self):
        self._data: dict = {}  # {run_id: {upload_reference: bytes}}
        self._lock = threading.RLock()

    def _make_reference(self, run_id: str, batch_id: str,
                        sha256_hex: str) -> str:
        """Build an upload reference from run_id, batch_id and sha256."""
        short = sha256_hex[:12]
        return f"synthetic://{run_id}/{batch_id}/{short}"

    def put(self, run_id: str, batch_id: str, payload: bytes,
            expected_sha256: str) -> str:
        """Store a tensor payload, validating SHA-256.

        Args:
            run_id: Run identifier.
            batch_id: Batch identifier.
            payload: Serialized tensor data (bytes).
            expected_sha256: Expected SHA-256 hex digest.

        Returns:
            Upload reference string.

        Raises:
            ValueError: If payload SHA-256 doesn't match expected_sha256,
                       or if existing reference has different content.
        """
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"SHA-256 mismatch: expected {expected_sha256}, "
                f"got {actual_sha256} (len={len(payload)} bytes)"
            )

        ref = self._make_reference(run_id, batch_id, actual_sha256)

        with self._lock:
            if run_id not in self._data:
                self._data[run_id] = {}

            existing = self._data[run_id].get(ref)
            if existing is not None:
                if existing != payload:
                    raise ValueError(
                        f"Reference '{ref}' already exists with "
                        f"different content"
                    )
                # Same content, idempotent — return existing reference
                return ref

            self._data[run_id][ref] = payload

        return ref

    def get(self, upload_reference: str) -> bytes:
        """Retrieve a tensor payload by upload reference.

        Args:
            upload_reference: Reference string (e.g. "synthetic://run_1/b_0/abc...").

        Returns:
            Bytes payload.

        Raises:
            KeyError: If reference not found.
        """
        run_id, ref = self._parse_reference(upload_reference)
        with self._lock:
            run_data = self._data.get(run_id)
            if run_data is None:
                raise KeyError(
                    f"Upload reference not found: {upload_reference} "
                    f"(run_id={run_id} not found)"
                )
            payload = run_data.get(upload_reference)
            if payload is None:
                raise KeyError(
                    f"Upload reference not found: {upload_reference}"
                )
            return payload

    def exists(self, upload_reference: str) -> bool:
        """Check if a reference exists in the store."""
        try:
            self.get(upload_reference)
            return True
        except KeyError:
            return False

    def delete(self, upload_reference: str) -> None:
        """Delete a specific upload reference.

        Args:
            upload_reference: Reference string.

        Raises:
            KeyError: If reference not found.
        """
        run_id, ref = self._parse_reference(upload_reference)
        with self._lock:
            run_data = self._data.get(run_id)
            if run_data is None or upload_reference not in run_data:
                raise KeyError(
                    f"Upload reference not found for deletion: "
                    f"{upload_reference}"
                )
            del run_data[upload_reference]
            if not run_data:
                del self._data[run_id]

    def cleanup_run(self, run_id: str) -> None:
        """Remove all data for a run.

        Args:
            run_id: Run identifier to clean up.
        """
        with self._lock:
            self._data.pop(run_id, None)

    def list_references(self, run_id: str) -> list:
        """List all upload references for a run (for debugging)."""
        with self._lock:
            run_data = self._data.get(run_id, {})
            return list(run_data.keys())

    def _parse_reference(self, ref: str) -> tuple:
        """Parse upload_reference into (run_id, full_ref)."""
        if not ref.startswith("synthetic://"):
            raise ValueError(
                f"Invalid upload_reference format: '{ref}' — "
                f"must start with 'synthetic://'"
            )
        parts = ref[len("synthetic://"):].split("/", 2)
        if len(parts) < 2:
            raise ValueError(
                f"Invalid upload_reference format: '{ref}'"
            )
        run_id = parts[0]
        return run_id, ref

    @property
    def size(self) -> int:
        """Total number of stored payloads across all runs."""
        with self._lock:
            return sum(len(v) for v in self._data.values())
