"""backends/dummy_backend.py — BACKEND DUMMY/TINY DE TEST (R2.1, punt 10).

Implementa exactament TrainingBackend amb un modelet numèric TINY (2 capes
lineals) SENSE transformers/PEFT. Serveix per certificar que el core
(HTTP server, Coordinator, Worker orchestration, leases, FedAvg) és
model-agnostic: si el protocol passa amb Dummy, no depèn de Qwen.

El modelet té tensors amb noms "lora_*" per simular adapters LoRA.
El journal SQLite és idèntic en semàntica al de Qwen (exactly-once,
recovery post-APPLIED) però amb tensors diminuts (ràpid).
"""
import base64
import hashlib
import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from training_backend import TrainingBackend  # noqa: E402
from adapter_codec import (pack_tensors, unpack_tensors, bundle_sha256,
                           to_numpy_dict)     # noqa: E402


class DummyBackend(TrainingBackend):
    """Modelet Tiny: W1 (8x4) + W2 (4x2) amb noms lora_. 8+2 tensors."""

    name = "dummy"
    journal_table = "dummy_journal"

    def __init__(self, model_path: str = "dummy", db_path: str = "dummy_journal.db",
                 seq_len: int = 16, lora: dict | None = None, seed: int = 7):
        self.model_path = model_path
        self.db_path = db_path
        self.seq_len = seq_len
        self.seed = seed
        self._rng = np.random.default_rng(seed)
        self.model = None
        self.optimizer = None
        self._loaded = False
        self._base_identity = None
        self._lora_pre = None
        self._lora_post = None
        self._last_loss = None
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS dummy_journal (
                update_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                delta_b64 TEXT,
                delta_sha TEXT,
                pre_hash TEXT,
                post_hash TEXT,
                request_sha TEXT,
                worker_id TEXT,
                assignment_id TEXT,
                shard_id TEXT,
                example_id TEXT,
                loss REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self._conn.commit()

    # ── model ────────────────────────────────────────────────────────────
    def _init_model(self):
        rng = self._rng
        # MLP tiny: input 8 -> hidden 4 -> output 2 (tensors "lora_*")
        self.model = {
            "lora_W1": (rng.standard_normal((4, 8)) * 0.1).astype(np.float32),
            "lora_b1": np.zeros(4, dtype=np.float32),
            "lora_W2": (rng.standard_normal((2, 4)) * 0.1).astype(np.float32),
            "lora_b2": np.zeros(2, dtype=np.float32),
        }
        self._grads = {n: np.zeros_like(v) for n, v in self.model.items()}

    def load_model(self):
        np.random.seed(self.seed)
        self._init_model()
        self._base_identity = self._compute_base_identity()
        self._loaded = True
        return self

    def _compute_base_identity(self) -> dict:
        # el "model base" dummy: hash d'una constant + config
        h = hashlib.sha256(f"dummy-base-v1:{self.seed}".encode()).hexdigest()
        return {"model_identifier": self.model_path,
                "config_sha": hashlib.sha256(
                    json.dumps({"seed": self.seed, "seq_len": self.seq_len},
                               sort_keys=True).encode()).hexdigest(),
                "weight_manifest_sha": hashlib.sha256(
                    b"dummy-manifest-v1").hexdigest(),
                "base_model_hash": h,
                "seed": self.seed}

    def base_model_identity(self) -> dict:
        return dict(self._base_identity)

    def base_model_hash(self) -> str:
        return self._base_identity["base_model_hash"]

    # ── adapters ─────────────────────────────────────────────────────────
    def load_adapter(self, data: bytes, expected_sha: str | None = None,
                     strict: bool = True):
        if strict and expected_sha is None:
            raise ValueError("expected_sha OBLIGATORI en mode MeshTrainer")
        if expected_sha is not None:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected_sha:
                raise ValueError("adapter SHA mismatch — REJECTED")
        tensors = unpack_tensors(data)
        # schema complet
        names = set(self.model)
        missing = sorted(names - set(tensors))
        extra = sorted(set(tensors) - names)
        if missing or extra:
            raise ValueError(
                f"adapter schema mismatch: missing={missing[:3]} "
                f"extra={extra[:3]} — REJECTED")
        for n in names:
            if tuple(tensors[n].shape) != tuple(self.model[n].shape):
                raise ValueError(f"shape mismatch {n} — REJECTED")
        for n in names:
            self.model[n] = np.asarray(tensors[n], dtype=np.float32).copy()
        return {"missing": [], "unexpected": []}

    def adapter_to_bundle(self) -> bytes:
        return pack_tensors(self.model)

    def adapter_hash(self) -> str:
        return self._hash_model(self.model)

    def _hash_model(self, m: dict) -> str:
        h = hashlib.sha256()
        for n in sorted(m):
            h.update(n.encode())
            h.update(np.asarray(m[n], dtype=np.float32).tobytes())
        return h.hexdigest()

    # ── dades / entrenament ──────────────────────────────────────────────
    def tokenize_example(self, instruction: str, response: str | None = None):
        # exemple numèric determinista a partir del text.
        # Contracte: (input_ids, attention_mask, labels) amb labels -100 al prompt.
        x = np.frombuffer(hashlib.sha256(instruction.encode()).digest(),
                          dtype=np.uint8)[:8].astype(np.float32) / 255.0
        if response is None:
            return x, np.ones_like(x), x
        # resposta de mida VARIABLE 1..4 (determinista): ETT genuïnament
        # variable entre exemples i shards
        y = np.frombuffer(hashlib.sha256(response.encode()).digest(),
                          dtype=np.uint8)[:4].astype(np.float32) / 255.0
        y = y[:1 + (int(hashlib.sha256((instruction + "|" + response).encode())
                       .hexdigest(), 16) % 4)]
        labels = np.full(8 + y.size, -100, dtype=np.float32)
        labels[8:] = y
        seq = np.concatenate([x, y])
        attn = np.ones_like(seq)
        return seq, attn, labels

    def compute_ett(self, labels) -> int:
        return max(1, int(np.asarray(labels).size))

    def train_step(self, update_id: str, instruction: str, response: str, *,
                   worker_id: str = "", assignment_id: str = "",
                   shard_id: str = "", example_id: str = "",
                   request_sha: str | None = None):
        r = self._conn.execute(
            "SELECT * FROM dummy_journal WHERE update_id=?",
            (update_id,)).fetchone()
        if r is not None:
            if r["status"] == "APPLIED":
                if r["request_sha"] and r["request_sha"] != request_sha:
                    raise ValueError("request_sha mismatch — REJECTED")
                return r["delta_b64"], r["delta_sha"]
            if r["status"] == "COMMITTED":
                raise ValueError(f"update_id {update_id} already COMMITTED")
        self._lora_pre = {n: v.copy() for n, v in self.model.items()}
        pre_hash = self.adapter_hash()
        # forward: h = W1@x + b1 ; pred = W2@h + b2  (x:8 -> h:4 -> pred:2)
        x, _, y = self.tokenize_example(instruction, response)
        xv = np.asarray(x[-8:], dtype=np.float32)
        yv = np.asarray(y[-2:], dtype=np.float32) if y.size >= 2 else np.zeros(2, dtype=np.float32)
        h = self.model["lora_W1"] @ xv + self.model["lora_b1"]
        pred = self.model["lora_W2"] @ h + self.model["lora_b2"]
        err = pred - yv
        loss = float(np.mean(err ** 2))
        # backward + step (SGD amb eta=0.01)
        self.model["lora_W2"] -= 0.01 * np.outer(err, h)
        self.model["lora_b2"] -= 0.01 * err
        dh = self.model["lora_W2"].T @ err
        self.model["lora_W1"] -= 0.01 * np.outer(dh, xv)
        self.model["lora_b1"] -= 0.01 * dh
        self._last_loss = loss
        self._lora_post = {n: v.copy() for n, v in self.model.items()}
        delta = {n: (self._lora_post[n] - self._lora_pre[n])
                 for n in self._lora_pre}
        db = pack_tensors(delta)
        dsha = bundle_sha256(db)
        self._conn.execute(
            "INSERT OR REPLACE INTO dummy_journal (update_id, status,"
            " delta_b64, delta_sha, pre_hash, post_hash, request_sha,"
            " worker_id, assignment_id, shard_id, example_id, loss)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (update_id, "APPLIED", base64.b64encode(db).decode(), dsha,
             pre_hash, self.adapter_hash(), request_sha, worker_id,
             assignment_id, shard_id, example_id, float(loss)))
        self._conn.commit()
        return base64.b64encode(db).decode(), dsha

    def recover_applied(self, update_id: str) -> dict | None:
        r = self._conn.execute(
            "SELECT * FROM dummy_journal WHERE update_id=?",
            (update_id,)).fetchone()
        if r is None:
            return None
        if r["status"] != "APPLIED":
            raise ValueError(f"status={r['status']} — recovery només APPLIED")
        if r["pre_hash"] and r["pre_hash"] != self.adapter_hash():
            raise ValueError("pre_hash mismatch — REJECTED")
        delta = unpack_tensors(base64.b64decode(r["delta_b64"]))
        for n, t in delta.items():
            self.model[n] = self.model[n] + np.asarray(t, dtype=np.float32)
        if r["post_hash"] and r["post_hash"] != self.adapter_hash():
            raise ValueError("post_hash mismatch — REJECTED")
        return {"update_id": update_id, "status": "APPLIED",
                "adapter_post_hash": r["post_hash"]}

    def journal_status(self, update_id: str) -> str | None:
        r = self._conn.execute(
            "SELECT status FROM dummy_journal WHERE update_id=?",
            (update_id,)).fetchone()
        return r["status"] if r else None

    def delta_tensors(self, update_id: str) -> dict:
        r = self._conn.execute(
            "SELECT delta_b64 FROM dummy_journal WHERE update_id=?",
            (update_id,)).fetchone()
        if r is None or not r["delta_b64"]:
            raise ValueError(f"update_id {update_id} sense delta")
        return to_numpy_dict(unpack_tensors(base64.b64decode(r["delta_b64"])))

    # ── codec ────────────────────────────────────────────────────────────
    def serialize_adapter(self, tensors: dict) -> bytes:
        return pack_tensors(tensors)

    def deserialize_adapter(self, data: bytes) -> dict:
        return unpack_tensors(data)

    def validate_adapter_schema(self, data: bytes,
                                expected_sha: str | None = None):
        self.load_adapter(data, expected_sha, strict=True)
        return {"missing": [], "unexpected": []}

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


def build(model_path: str = "dummy", db_path: str = "dummy_journal.db",
          seq_len: int = 16, lora: dict | None = None,
          seed: int = 7) -> DummyBackend:
    return DummyBackend(model_path, db_path=db_path, seq_len=seq_len,
                        lora=lora, seed=seed)
