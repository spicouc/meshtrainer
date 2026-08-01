"""RC5.2 Phase 2 R9: WorkerRuntime with SQLite exactly-once journal."""
import torch, json, copy, hashlib, sqlite3, os, threading
from rc5_2_numerical_models import WorkerNumericalModel
from rc5_2_numerical_profile import PROFILE as P
from rc5_2_tensor_bundle import delta_bundle_pack, bundle_sha256

class WorkerRuntime:
    def __init__(self, db_path="worker_journal.db"):
        self.worker = WorkerNumericalModel()
        self._cut_activation = None
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS journal (
                update_id TEXT PRIMARY KEY,
                unit_id TEXT, status TEXT,
                pre_adapter_hash TEXT, post_adapter_hash TEXT,
                delta_bundle_sha256 TEXT, delta_bundle_b64 TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self._conn.commit()

    def load_state(self, ref_state: dict):
        for name, p in self.worker.named_parameters():
            if name in ref_state:
                p.data.copy_(ref_state[name])

    def worker_forward(self, activation, mask=None):
        self._cut_activation = self.worker.worker_forward(activation, mask)
        return self._cut_activation

    def worker_backward(self, cut_gradient):
        self.worker.worker_backward(self._cut_activation, cut_gradient)

    def compute_delta_bundle(self) -> bytes:
        return delta_bundle_pack(
            self.worker.lora_wrapper.lora_A.weight.detach().cpu(),
            self.worker.lora_wrapper.lora_B.weight.detach().cpu(),
        )

    def load_from_artifacts(self, base_model_bytes: bytes, base_adapter_bytes: bytes):
        """Load worker state from REAL artifact bundles (strict: zero missing/unexpected)."""
        from rc5_2_tensor_bundle import tensor_bundle_v1_unpack, bundle_sha256
        bm = tensor_bundle_v1_unpack(base_model_bytes, bundle_sha256(base_model_bytes))
        ad = tensor_bundle_v1_unpack(base_adapter_bytes, bundle_sha256(base_adapter_bytes))
        params = dict(self.worker.named_parameters())
        missing, unexpected = [], []
        for n, p in params.items():
            if "lora" not in n:
                if n in bm: p.data.copy_(bm[n])
                else: missing.append(n)
        for k in bm:
            if k not in params: unexpected.append(k)
        if "local_layers.0.linear1.lora_A.weight" in ad:
            self.worker.lora_wrapper.lora_A.weight.data.copy_(ad["local_layers.0.linear1.lora_A.weight"])
            self.worker.lora_wrapper.lora_B.weight.data.copy_(ad["local_layers.0.linear1.lora_B.weight"])
        if missing or unexpected:
            raise ValueError(f"Artifact load mismatch: missing={missing} unexpected={unexpected}")
        return {"missing": missing, "unexpected": unexpected}

    def get_journal_status(self, update_id):
        r = self._conn.execute("SELECT status FROM journal WHERE update_id=?", (update_id,)).fetchone()
        return r["status"] if r else None

    def prepare_update(self, update_id, unit_id):
        """Persist PREPARED if not present; reject if ALREADY APPLIED."""
        r = self._conn.execute("SELECT status FROM journal WHERE update_id=?", (update_id,)).fetchone()
        if r and r["status"] == "APPLIED":
            raise ValueError(f"update_id {update_id} already APPLIED")
        self._conn.execute("INSERT OR IGNORE INTO journal (update_id, unit_id, status) VALUES (?,?, 'PREPARED')",
                           (update_id, unit_id))
        self._conn.commit()

    def apply_update_once(self, update_id, cut_activation, cut_gradient):
        """Exactly-once: backward + single optimizer.step, persist APPLIED + delta bundle."""
        r = self._conn.execute("SELECT * FROM journal WHERE update_id=?", (update_id,)).fetchone()
        if r is None:
            raise ValueError(f"update_id {update_id} not prepared")
        if r["status"] == "APPLIED":
            # Return the SAME bundle, no second optimizer step
            import base64
            return r["delta_bundle_b64"], r["delta_bundle_sha256"]
        if r["status"] == "COMMITTED":
            raise ValueError(f"update_id {update_id} already COMMITTED")
        # fresh application
        self.worker.optimizer.zero_grad(set_to_none=True)
        self._cut_activation = cut_activation
        self.worker.worker_backward(cut_activation, cut_gradient)
        self.worker.optimizer.step()
        delta = self.compute_delta_bundle()
        dsha = bundle_sha256(delta)
        import base64
        self._conn.execute(
            "UPDATE journal SET status='APPLIED', delta_bundle_sha256=?, delta_bundle_b64=? WHERE update_id=?",
            (dsha, base64.b64encode(delta).decode("ascii"), update_id))
        self._conn.commit()
        return base64.b64encode(delta).decode("ascii"), dsha

    def get_prepared_bundle(self, update_id):
        r = self._conn.execute("SELECT delta_bundle_b64, delta_bundle_sha256 FROM journal WHERE update_id=? AND status='APPLIED'", (update_id,)).fetchone()
        if r is None: raise ValueError(f"update_id {update_id} not APPLIED")
        return r["delta_bundle_b64"], r["delta_bundle_sha256"]

    def mark_submitted(self, update_id):
        self._conn.execute("UPDATE journal SET status='SUBMITTED' WHERE update_id=?", (update_id,))
        self._conn.commit()

    def mark_committed(self, update_id):
        self._conn.execute("UPDATE journal SET status='COMMITTED' WHERE update_id=?", (update_id,))
        self._conn.commit()

    def get_journal_status(self, update_id: str) -> str:
        r = self._conn.execute("SELECT status FROM journal WHERE update_id=?", (update_id,)).fetchone()
        return r["status"] if r else None
