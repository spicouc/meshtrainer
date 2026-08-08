"""Qwen3TrainingBackend — backend real Qwen3 + LoRA per al pipeline MeshTrainer.

ADDITIU: no modifica Coordinator/FedAvg/WorkerRuntime/leases (congelats).
S'integra per import amb:
  - rc5_2_numerical_adapter.ett_from_labels   (ETT des de labels)
  - aggregation.FedAvgLoRA                     (FedAvg ponderat per ETT)
  - rc5_4_leases.LeaseManager                  (recovery exactly-once)
"""
import base64
import hashlib
import json
import struct
import time
import torch
import numpy as np

from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model

from rc5_2_numerical_adapter import ett_from_labels
from rc5_2_tensor_bundle import tensor_bundle_v1_pack, tensor_bundle_v1_unpack, bundle_sha256

LORA_DEFAULT = dict(r=8, alpha=16, dropout=0.1,
                    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"])


class Qwen3TrainingBackend:
    """Worker real: Qwen3-0.6B + LoRA + optimizer amb exactly-once via journal."""

    def __init__(self, model_path: str, db_path: str = "qwen_worker_journal.db",
                 seq_len: int = 256, lora: dict | None = None, seed: int = 424242):
        self.model_path = model_path
        self.seq_len = seq_len
        self.lora_cfg = dict(LORA_DEFAULT, **(lora or {}))
        self.db_path = db_path
        self.seed = seed
        self.tokenizer = None
        self.model = None
        self.optimizer = None
        self._lora_pre = None       # dict name -> tensor (abans del pas)
        self._lora_post = None
        self._last_loss = None
        self._loaded = False
        # journal PERSISTENT (SQLite) per a recovery exactly-once entre processos
        import sqlite3 as _sq
        self._conn = _sq.connect(db_path, check_same_thread=False)
        self._conn.row_factory = _sq.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS qwen_journal (
                update_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                delta_b64 TEXT,
                delta_sha TEXT,
                pre_hash TEXT,
                post_hash TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self._conn.commit()

    # ── càrrega ──────────────────────────────────────────────────────────
    def load(self):
        torch.manual_seed(self.seed)
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, use_fast=False)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        base = AutoModelForCausalLM.from_pretrained(
            self.model_path, torch_dtype=torch.float32, low_cpu_mem_usage=True)
        cfg = LoraConfig(
            r=self.lora_cfg["r"],
            lora_alpha=self.lora_cfg["alpha"],
            lora_dropout=self.lora_cfg["dropout"],
            target_modules=self.lora_cfg["target_modules"],
            bias="none",
            task_type="CAUSAL_LM",
        )
        self.model = get_peft_model(base, cfg)
        self.model.train()
        self.optimizer = torch.optim.AdamW(
            [p for n, p in self.model.named_parameters() if "lora_" in n],
            lr=2e-4)
        self._loaded = True
        return self

    @property
    def lora_params(self) -> dict:
        return {n: p.detach().clone().float()
                for n, p in self.model.named_parameters() if "lora_" in n}

    def hash_adapter(self) -> str:
        parts = []
        for n in sorted(self.lora_params):
            parts.append(n.encode("utf-8"))
            parts.append(self.lora_params[n].cpu().numpy().tobytes())
        return hashlib.sha256(b"".join(parts)).hexdigest()

    def adapter_to_bundle(self) -> bytes:
        """Serialitza l'estat LoRA complet (adapter_0/1/2) en format propi."""
        return self._serialize_delta(self.lora_params)

    def load_adapter_from_bundle(self, data: bytes, expected_sha: str | None = None):
        tensors = self._deserialize_delta(data)
        state = dict(self.model.named_parameters())
        missing = [n for n in tensors if n not in state]
        if missing:
            raise ValueError(f"Adapter load mismatch: missing={missing}")
        for n, t in tensors.items():
            state[n].data.copy_(torch.as_tensor(t))
        return {"missing": []}

    # ── tokenització / ETT ───────────────────────────────────────────────
    @staticmethod
    def _flat_ids(x) -> list[int]:
        """Normalitza el retorn d'apply_chat_template a una llista plana d'ids."""
        if hasattr(x, "get") and "input_ids" in x:
            x = x["input_ids"]
        if not x:
            return []
        if isinstance(x[0], list):
            return [int(i) for i in x[0]]
        return [int(i) for i in x]

    def tokenize_chat(self, instruction: str, response: str | None = None):
        """Format chat Qwen amb labels EXACTES (mètode prompt+resposta separats).

        - prompt_ids: template chat fins a 'assistant\\n' (add_generation_prompt)
        - resp_ids: resposta codificada sense special tokens
        - labels: -100 a la part del prompt; els tokens de la resposta contribueixen
        """
        prompt_ids = self._flat_ids(self.tokenizer.apply_chat_template(
            [{"role": "user", "content": instruction}], tokenize=True,
            add_generation_prompt=True, truncation=True,
            max_length=self.seq_len))
        if response is None:
            input_ids = torch.tensor([prompt_ids], dtype=torch.long)
            return {"input_ids": input_ids,
                    "attention_mask": torch.ones_like(input_ids)}
        resp_ids = self.tokenizer.encode(response, add_special_tokens=False)
        # truncament: mantenim el prompt sencer i retallem la resposta
        budget = self.seq_len - len(prompt_ids)
        if budget <= 0:
            raise ValueError("Prompt massa llarg per a seq_len")
        resp_ids = resp_ids[:budget]
        seq = prompt_ids + resp_ids
        input_ids = torch.tensor([seq], dtype=torch.long)
        labels = torch.full_like(input_ids, -100)
        labels[0, len(prompt_ids):] = torch.tensor(resp_ids, dtype=torch.long)
        attention_mask = torch.ones_like(input_ids)
        return input_ids, attention_mask, labels

    @staticmethod
    def compute_ett(labels: torch.Tensor) -> int:
        return int(ett_from_labels(labels))

    # ── pas d'entrenament exactly-once ───────────────────────────────────
    def train_step(self, update_id: str, instruction: str, response: str):
        """UN forward + UN backward + UN optimizer.step. Exactly-once:
        replay (status APPLIED) retorna el MATEIX delta sense segon pas."""
        r = self._conn.execute(
            "SELECT status, delta_b64, delta_sha FROM qwen_journal WHERE update_id=?",
            (update_id,)).fetchone()
        if r is not None:
            if r["status"] == "APPLIED":
                return r["delta_b64"], r["delta_sha"]
            if r["status"] == "COMMITTED":
                raise ValueError(f"update_id {update_id} already COMMITTED")
        self._lora_pre = self.lora_params
        input_ids, attn, labels = self.tokenize_chat(instruction, response)
        self.optimizer.zero_grad()
        out = self.model(input_ids=input_ids, attention_mask=attn, labels=labels)
        loss = out.loss
        loss.backward()
        self.optimizer.step()          # exactament UNA vegada
        self._last_loss = float(loss.detach())
        self._lora_post = self.lora_params
        delta = {n: (self._lora_post[n] - self._lora_pre[n]) for n in self._lora_pre}
        db = self._serialize_delta(delta)
        dsha = bundle_sha256(db)
        self._conn.execute(
            "INSERT OR REPLACE INTO qwen_journal (update_id, status, delta_b64,"
            " delta_sha, pre_hash, post_hash) VALUES (?,?,?,?,?,?)",
            (update_id, "APPLIED", base64.b64encode(db).decode("ascii"), dsha,
             self.hash_adapter_pre(), self.hash_adapter()))
        self._conn.commit()
        return base64.b64encode(db).decode("ascii"), dsha

    def hash_adapter_pre(self) -> str:
        if self._lora_pre is None:
            return ""
        parts = []
        for n in sorted(self._lora_pre):
            parts.append(n.encode("utf-8"))
            parts.append(self._lora_pre[n].cpu().numpy().tobytes())
        return hashlib.sha256(b"".join(parts)).hexdigest()

    @staticmethod
    def _serialize_delta(delta: dict) -> bytes:
        """Delta LoRA en format propi qwen_bundle (float32).

        NO es fa servir tensor_bundle_v1_pack: el capçaler d'aquest mòdul
        congelat té un límit de ~32KB i els 224 tensors LoRA del Qwen amb noms
        llargs el superen (Header too large). El format propi és additiu i
        deserialitzable per delta_tensors(); el FedAvg (RC5.3) treballa amb
        dicts de np.ndarray, no amb aquests bytes."""
        import io
        names = sorted(delta.keys())
        buf = io.BytesIO()
        header = {"schema": "qwen_bundle_v1", "tensors": []}
        offset = 0
        for n in names:
            t = delta[n].detach().float().contiguous()
            b = t.numpy().tobytes()
            header["tensors"].append({"name": n, "offset": offset,
                                      "byte_length": len(b),
                                      "shape": list(t.shape),
                                      "dtype": "float32"})
            offset += len(b)
        hdr = json.dumps(header, separators=(",", ":")).encode("utf-8")
        out = struct.pack("<I", len(hdr)) + hdr
        for n in names:
            out += delta[n].detach().float().contiguous().numpy().tobytes()
        return out

    @staticmethod
    def _deserialize_delta(data: bytes) -> dict:
        import struct as _s
        hdr_len = _s.unpack_from("<I", data, 0)[0]
        header = json.loads(data[4:4 + hdr_len])
        if header.get("schema") != "qwen_bundle_v1":
            raise ValueError("qwen_bundle_v1 esperat")
        out = {}
        for t in header["tensors"]:
            start = 4 + hdr_len + t["offset"]
            arr = np.frombuffer(data[start:start + t["byte_length"]],
                                dtype=np.float32).reshape(t["shape"])
            out[t["name"]] = torch.as_tensor(arr.copy())
        return out

    def mark_committed(self, update_id: str):
        self._conn.execute("UPDATE qwen_journal SET status='COMMITTED' WHERE update_id=?",
                           (update_id,))
        self._conn.commit()

    def journal_status(self, update_id: str) -> str | None:
        r = self._conn.execute("SELECT status FROM qwen_journal WHERE update_id=?",
                               (update_id,)).fetchone()
        return r["status"] if r else None

    def delta_tensors(self, update_id: str) -> dict:
        """Retorna el delta com a dict de np.ndarray (per al FedAvg real)."""
        r = self._conn.execute(
            "SELECT delta_b64, delta_sha FROM qwen_journal WHERE update_id=?",
            (update_id,)).fetchone()
        if r is None or not r["delta_b64"]:
            raise ValueError(f"update_id {update_id} sense delta")
        tensors = self._deserialize_delta(base64.b64decode(r["delta_b64"]))
        return {n: np.asarray(t, dtype=np.float32) for n, t in tensors.items()}

    # ── inferència ───────────────────────────────────────────────────────
    @torch.no_grad()
    def generate(self, prompt: str, max_new_tokens: int = 60) -> str:
        self.model.eval()
        enc = self.tokenize_chat(prompt)
        out = self.model.generate(
            input_ids=enc["input_ids"],
            attention_mask=enc.get("attention_mask"),
            max_new_tokens=max_new_tokens,
            do_sample=False, pad_token_id=self.tokenizer.pad_token_id)
        self.model.train()
        text = self.tokenizer.decode(out[0][enc["input_ids"].shape[1]:],
                                     skip_special_tokens=True)
        return text.strip()

    @torch.no_grad()
    def eval_loss(self, instruction: str, response: str) -> float:
        self.model.eval()
        input_ids, attn, labels = self.tokenize_chat(instruction, response)
        loss = self.model(input_ids=input_ids, attention_mask=attn,
                          labels=labels).loss
        self.model.train()
        return float(loss.detach())

    def metrics(self) -> dict:
        return {"loss": self._last_loss,
                "pre_hash": self.hash_adapter_pre(),
                "post_hash": self.hash_adapter(),
                "delta_norm": self.delta_norm()}

    def delta_norm(self) -> float:
        if self._lora_pre is None or self._lora_post is None:
            return 0.0
        tot = 0.0
        for n in self._lora_pre:
            tot += float((self._lora_post[n] - self._lora_pre[n]).pow(2).sum())
        return float(np.sqrt(tot))


def fedavg_qwen(adapter_0: dict, deltas_ett: list[tuple[dict, int]]) -> dict:
    """adapter_1 = adapter_0 + Σ(delta_i * ETT_i) / Σ ETT_i  (float64 -> float32).

    Fórmula idèntica a FedAvgLoRA._aggregate_real (aggregation.py), càlcul
    independent per a l'oracle."""
    total = sum(e for _, e in deltas_ett)
    if total == 0:
        raise ValueError("Σ ETT = 0")
    names = sorted({n for d, _ in deltas_ett for n in d})
    acc = {n: np.zeros_like(np.asarray(adapter_0[n]), dtype=np.float64)
           for n in names}
    for d, e in deltas_ett:
        for n in names:
            acc[n] += np.asarray(d[n], dtype=np.float64) * e
    return {n: (adapter_0[n].astype(np.float64) + acc[n] / total).astype(np.float32)
            for n in names}
