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
import os
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
                request_sha TEXT,
                worker_id TEXT,
                assignment_id TEXT,
                shard_id TEXT,
                example_id TEXT,
                optimizer_b64 TEXT,
                rng_b64 TEXT,
                loss REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        # migració per a BDs creades abans de R2 (columnes noves)
        for _col, _ddl in (("request_sha", "TEXT"), ("worker_id", "TEXT"),
                           ("assignment_id", "TEXT"), ("shard_id", "TEXT"),
                           ("example_id", "TEXT"), ("optimizer_b64", "TEXT"),
                           ("rng_b64", "TEXT"), ("loss", "REAL")):
            try:
                self._conn.execute(f"ALTER TABLE qwen_journal ADD COLUMN {_col} {_ddl}")
            except _sq.OperationalError:
                pass  # la columna ja existeix
        self._conn.commit()
        self._base_identity = None

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
        self._base_identity = self._compute_base_identity()
        self._loaded = True
        return self

    def _compute_base_identity(self) -> dict:
        """Fingerprint canònic del model BASE (pesos no-LoRA), calculat UNA
        vegada a la càrrega i persistit. No es re-hasheja a cada request."""
        import hashlib as _h
        cfg_sha = None
        cfg_path = os.path.join(self.model_path, "config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg_sha = _h.sha256(f.read()).hexdigest()
        h = _h.sha256()
        manifest = []
        for n, p in self.model.named_parameters():
            if "lora_" not in n:
                manifest.append(f"{n}:{list(p.shape)}:{p.dtype}")
                # MOSTRA dels pesos (4KB per tensor) + manifest complet de
                # noms/shapes: fingerprint canònic equivalent, sense hashejar
                # els 600M paràmetres a cada càrrega.
                flat = p.detach().cpu().numpy().reshape(-1)
                h.update(flat[:4096].tobytes())
        manifest_sha = _h.sha256(
            "\n".join(sorted(manifest)).encode("utf-8")).hexdigest()
        base_hash = h.hexdigest()
        return {"model_identifier": self.model_path,
                "config_sha": cfg_sha,
                "weight_manifest_sha": manifest_sha,
                "base_model_hash": base_hash,
                "seed": self.seed}

    def base_model_hash(self) -> str:
        """Hash canònic del model base (pesos no-LoRA). Sense re-hashejar:
        retorna el valor persistit a la càrrega."""
        if self._base_identity is None:
            raise ValueError("base_model_hash: carrega el backend primer")
        return self._base_identity["base_model_hash"]

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

    def load_adapter_from_bundle(self, data: bytes, expected_sha: str | None = None,
                                 strict: bool = True):
        """Carrega l'adapter des del bundle. Mode MeshTrainer (strict=True):
        expected_sha OBLIGATORI; SHA-256(data)==expected_sha; schema complet
        (exactament els tensors LoRA: cap absent, cap extra, noms exactes,
        shapes exactes, dtype admès). Wrong/partial/stale -> REJECTED."""
        if strict and expected_sha is None:
            raise ValueError("expected_sha OBLIGATORI en mode MeshTrainer — REJECTED")
        if expected_sha is not None:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected_sha:
                raise ValueError(
                    f"adapter SHA mismatch: {actual[:12]} != {expected_sha[:12]} — REJECTED")
        tensors = self._deserialize_delta(data)
        state = dict(self.model.named_parameters())
        lora_names = {n for n in state if "lora_" in n}
        # schema complet: cap tensor absent, cap tensor extra
        missing = sorted(lora_names - set(tensors))
        extra = sorted(set(tensors) - lora_names)
        if missing or extra:
            raise ValueError(
                f"adapter schema mismatch: missing={missing[:3]} extra={extra[:3]} — REJECTED")
        # noms exactes, shapes exactes, dtype admès (float32)
        for n in lora_names:
            t = tensors[n]
            if tuple(t.shape) != tuple(state[n].shape):
                raise ValueError(
                    f"shape mismatch {n}: {tuple(t.shape)} != {tuple(state[n].shape)} — REJECTED")
            if t.dtype != torch.float32:
                raise ValueError(f"dtype no admès {n}: {t.dtype} — REJECTED")
        for n, t in tensors.items():
            state[n].data.copy_(torch.as_tensor(t))
        return {"missing": [], "unexpected": []}

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

    # ── estat de l'optimizer (persistit al journal per al recovery exacte) ──
    def _lora_param_names(self) -> list[str]:
        """Noms dels params LoRA en l'ordre de l'optimizer (ordre de creació)."""
        return [n for n, p in self.model.named_parameters() if "lora_" in n]

    def _optimizer_state_b64(self) -> str:
        """Serialitza l'estat de l'optimizer AdamW amb el format EXACTE del
        state_dict: moments 1D float32 (foreach), step amb dtype original i
        param_groups AMB els índexs 'params' originals (la clau que faltava
        a la versió anterior i que feia que load_state_dict no apliqués els
        moments). Compacte (~1MB) i fidel: el pas següent és IDÈNTIC al
        no-crash."""
        sd = self.optimizer.state_dict()
        out = {"state": {}, "param_groups": []}
        for k, v in sd["state"].items():
            out["state"][str(k)] = {
                "step_dtype": str(v["step"].dtype).split(".")[-1],
                "step": float(v["step"]),
                "exp_avg_shape": list(v["exp_avg"].shape),
                "exp_avg": base64.b64encode(
                    v["exp_avg"].numpy().tobytes()).decode("ascii"),
                "exp_avg_sq_shape": list(v["exp_avg_sq"].shape),
                "exp_avg_sq": base64.b64encode(
                    v["exp_avg_sq"].numpy().tobytes()).decode("ascii"),
            }
        for g in sd["param_groups"]:
            g2 = {k2: v2 for k2, v2 in g.items() if k2 != "params"}
            g2["params"] = [int(i) for i in g["params"]]   # índexs ORIGINALS
            out["param_groups"].append(g2)
        return base64.b64encode(json.dumps(out).encode()).decode("ascii")

    def _load_optimizer_state_b64(self, b64str: str):
        st = json.loads(base64.b64decode(b64str))
        # CLAUS INT: el state_dict de torch 2.13 té les claus de l'estat com a
        # int (0..223). Amb claus str el load_state_dict NO les mapeja i els
        # moments no s'apliquen (optimizer fresh -> pas incorrecte).
        sd = {"state": {}, "param_groups": st["param_groups"]}
        for k, v in st["state"].items():
            step = torch.tensor(v["step"], dtype=getattr(torch, v["step_dtype"]))
            sd["state"][int(k)] = {
                "step": step,
                "exp_avg": torch.from_numpy(np.frombuffer(
                    base64.b64decode(v["exp_avg"]), dtype=np.float32).copy()
                    .reshape(v["exp_avg_shape"])),
                "exp_avg_sq": torch.from_numpy(np.frombuffer(
                    base64.b64decode(v["exp_avg_sq"]), dtype=np.float32).copy()
                    .reshape(v["exp_avg_sq_shape"])),
            }
        self.optimizer.load_state_dict(sd)

    # ── pas d'entrenament exactly-once ───────────────────────────────────
    @staticmethod
    def _make_request_sha(update_id, worker_id, assignment_id, shard_id,
                          adapter_pre_hash, example_id, instruction, response) -> str:
        """request_sha canònic: update_id + worker_id + assignment_id + shard_id
        + adapter_pre_hash + example_id + payload training hash."""
        payload = json.dumps({"instruction": instruction, "response": response},
                             ensure_ascii=False, sort_keys=True)
        canonical = json.dumps({
            "update_id": update_id, "worker_id": worker_id,
            "assignment_id": assignment_id, "shard_id": shard_id,
            "adapter_pre_hash": adapter_pre_hash, "example_id": example_id,
            "payload_sha": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def train_step(self, update_id: str, instruction: str, response: str, *,
                   worker_id: str = "", assignment_id: str = "", shard_id: str = "",
                   example_id: str = "", request_sha: str | None = None):
        """UN forward + UN backward + UN optimizer.step. Exactly-once estricte:
        - mateix update_id + mateix request_sha -> MATEIX delta (sense 2n pas)
        - mateix update_id + request_sha DIFERENT -> REJECTED (mai el delta antic)
        """
        r = self._conn.execute(
            "SELECT * FROM qwen_journal WHERE update_id=?", (update_id,)).fetchone()
        if r is not None:
            if r["status"] == "APPLIED":
                if request_sha is None:
                    request_sha = self._make_request_sha(
                        update_id, worker_id, assignment_id, shard_id,
                        r["pre_hash"], example_id, instruction, response)
                if r["request_sha"] and r["request_sha"] != request_sha:
                    raise ValueError(
                        f"update_id {update_id}: request_sha mismatch — REJECTED "
                        f"(mateix update_id, payload diferent)")
                return r["delta_b64"], r["delta_sha"]
            if r["status"] == "COMMITTED":
                raise ValueError(f"update_id {update_id} already COMMITTED")
        self._lora_pre = self.lora_params
        pre_hash = self.hash_adapter_pre()
        if request_sha is None:
            request_sha = self._make_request_sha(
                update_id, worker_id, assignment_id, shard_id,
                pre_hash, example_id, instruction, response)
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
            " delta_sha, pre_hash, post_hash, request_sha, worker_id,"
            " assignment_id, shard_id, example_id, optimizer_b64, rng_b64, loss)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (update_id, "APPLIED", base64.b64encode(db).decode("ascii"), dsha,
             pre_hash, self.hash_adapter_tolerant(), request_sha, worker_id,
             assignment_id, shard_id, example_id, self._optimizer_state_b64(),
             base64.b64encode(torch.get_rng_state().numpy().tobytes()).decode("ascii"),
             float(self._last_loss)))
        self._conn.commit()
        return base64.b64encode(db).decode("ascii"), dsha

    def recover_applied(self, update_id: str) -> dict | None:
        """Recovery POST-APPLIED: reconstrueix adapter_post = adapter_pre + delta
        SENSE optimizer.step (el journal persisteix el delta real). Verifica
        pre_hash vs l'estat actual abans i post_hash després de reconstruir.
        Després el worker pot executar un SEGON exemple d'entrenament."""
        r = self._conn.execute(
            "SELECT * FROM qwen_journal WHERE update_id=?", (update_id,)).fetchone()
        if r is None:
            return None
        if r["status"] != "APPLIED":
            raise ValueError(f"update_id {update_id} status={r['status']} — "
                             "recovery només per APPLIED")
        if r["pre_hash"] and r["pre_hash"] != self.hash_adapter():
            raise ValueError(
                "pre_hash mismatch: l'estat actual no és l'adapter_pre — REJECTED")
        delta = self._deserialize_delta(base64.b64decode(r["delta_b64"]))
        state = dict(self.model.named_parameters())
        for n, t in delta.items():
            state[n].data.add_(torch.as_tensor(t))
        if r["post_hash"] and r["post_hash"] != self.hash_adapter_tolerant():
            raise ValueError(
                "post_hash mismatch després de la reconstrucció — REJECTED")
        if r["optimizer_b64"]:
            self._load_optimizer_state_b64(r["optimizer_b64"])
        if r["rng_b64"]:
            # l'estat del RNG post-pas (dropout determinista al pas següent)
            torch.set_rng_state(torch.from_numpy(np.frombuffer(
                base64.b64decode(r["rng_b64"]), dtype=np.uint8).copy()))
        return {"update_id": update_id, "status": "APPLIED",
                "adapter_post_hash": r["post_hash"]}

    def hash_adapter_tolerant(self) -> str:
        """Hash dels LoRA amb els tensors com a float16: estable sota 1 ulp
        d'arrodoniment de la reconstrucció pre+delta (la suma no és
        associativa en float32) però detecta qualsevol error real."""
        parts = []
        for n in sorted(self.lora_params):
            parts.append(n.encode("utf-8"))
            parts.append(self.lora_params[n].cpu().numpy()
                         .astype(np.float16).tobytes())
        return hashlib.sha256(b"".join(parts)).hexdigest()

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
