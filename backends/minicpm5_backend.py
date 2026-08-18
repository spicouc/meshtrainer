"""backends/minicpm5_backend.py — MiniCPM5-1B com a PLUGIN TrainingBackend
(Milestone MINICPM5 BACKEND PORTABILITY).

Implementa EXACTAMENT el contracte generic TrainingBackend (training_backend.py)
amb el model openbmb/MiniCPM5-1B (LlamaForCausalLM) + LoRA (PEFT).

REGLES DEL MILESTONE:
  - NO toca el core generic (Coordinator/HTTP/Worker/FedAvg/leases/recovery).
  - El journal SQLite propi replica la semantica del dummy/qwen (exactly-once,
    recovery post-APPLIED sense 2n optimizer step).
  - Aquest modul POT importar transformers/peft/torch (es el plugin); el core
    nomes el carrega via registry lazy (model_worker.load_backend).
"""
import base64
import hashlib
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from training_backend import TrainingBackend  # noqa: E402
from adapter_codec import (pack_tensors, unpack_tensors, bundle_sha256,
                           to_numpy_dict)     # noqa: E402

# ── identitat canònica del model pinnejada ───────────────────────────────
MINICPM5_MODEL_ID = "openbmb/MiniCPM5-1B"
MINICPM5_REVISION = "4e9de7a0778dc1c362e983e6858f0e77542cbdca"  # pinnejada

# LoRA config (arquitectura Llama — NO assumir els target modules de Qwen)
DEFAULT_LORA = {
    "r": 16,
    "alpha": 32,
    "dropout": 0.05,
    "target_modules": [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    "bias": "none",
    "task_type": "CAUSAL_LM",
}


class MiniCPM5Backend(TrainingBackend):
    """MiniCPM5-1B + LoRA implementant el contracte generic."""

    name = "minicpm5"
    journal_table = "minicpm5_journal"

    def __init__(self, model_path: str = "/root/minicpm5_1b_snapshot",
                 db_path: str = "minicpm5_journal.db", seq_len: int = 512,
                 lora: dict | None = None, seed: int = 424242):
        self.model_path = model_path
        self.db_path = db_path
        self.seq_len = seq_len
        self.lora_cfg = dict(DEFAULT_LORA)
        if lora:
            self.lora_cfg.update(lora)
        self.seed = seed
        self.model = None
        self.tokenizer = None
        self.peft_model = None
        self.optimizer = None
        self._loaded = False
        self._base_identity = None
        self._lora_pre = None
        self._lora_post = None
        self._last_loss = None
        self._lora_param_names = None
        # journal propi (mateixa semantica que qwen/dummy)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS {self.journal_table} (
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

    # ── càrrega / identitat ──────────────────────────────────────────────
    def load_model(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import LoraConfig, get_peft_model

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        # CPU: float32 (els matmuls bf16 en CPU sense hardware dedicat són
        # molt més lents; f32 és el dtype òptim per CPU)
        load_dtype = torch.float32
        base = AutoModelForCausalLM.from_pretrained(
            self.model_path, trust_remote_code=True, dtype=load_dtype)
        base = base.to("cpu")  # CT112 sense GPU: CPU
        base.config.use_cache = False

        # seed fixa: la inicialització LoRA de PEFT ha de ser REPRODUÏBLE
        # entre càrregues (el pre_hash del journal ha de coincidir amb un
        # backend nou en mode recovery)
        import random as _random
        torch.manual_seed(self.seed)
        _random.seed(self.seed)
        np.random.seed(self.seed)

        lora = LoraConfig(
            r=self.lora_cfg["r"], lora_alpha=self.lora_cfg["alpha"],
            lora_dropout=self.lora_cfg["dropout"],
            target_modules=self.lora_cfg["target_modules"],
            bias=self.lora_cfg.get("bias", "none"),
            task_type=self.lora_cfg.get("task_type", "CAUSAL_LM"))
        self.peft_model = get_peft_model(base, lora)
        self.peft_model.train()
        self.model = self.peft_model

        # optimizer (AdamW sobre ELS PARAMETRES LORA, filtrat requires_grad)
        trainable = [p for p in self.peft_model.parameters() if p.requires_grad]
        self.optimizer = torch.optim.AdamW(trainable, lr=1e-4)
        self._lora_param_names = [n for n, p in
                                  self.peft_model.named_parameters()
                                  if p.requires_grad]
        self._loaded = True
        self._base_identity = self._compute_base_identity()
        return self

    def _compute_base_identity(self) -> dict:
        # config_sha: hash del config.json del snapshot
        cfg_path = os.path.join(self.model_path, "config.json")
        if os.path.exists(cfg_path):
            cfg_sha = hashlib.sha256(
                open(cfg_path, "rb").read()).hexdigest()
        else:
            cfg_sha = hashlib.sha256(b"missing-config").hexdigest()
        # weight manifest: nom + sha per fitxer de pesos del snapshot
        manifest = {}
        for f in sorted(os.listdir(self.model_path)):
            if f.endswith((".safetensors", ".bin")):
                fp = os.path.join(self.model_path, f)
                manifest[f] = hashlib.sha256(
                    open(fp, "rb").read()).hexdigest()
        wm_sha = hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        # base_model_hash: identitat estable del model (no adapters)
        h = hashlib.sha256(
            json.dumps({"model_id": MINICPM5_MODEL_ID,
                        "revision": MINICPM5_REVISION,
                        "config_sha": cfg_sha,
                        "weight_manifest_sha": wm_sha,
                        "seq_len": self.seq_len,
                        "lora": self.lora_cfg}, sort_keys=True).encode()
        ).hexdigest()
        return {
            "backend_id": self.name,
            "model_id": MINICPM5_MODEL_ID,
            "model_revision": MINICPM5_REVISION,
            "config_sha": cfg_sha,
            "weight_manifest_sha": wm_sha,
            "base_model_hash": h,
            "tokenizer_identity": hashlib.sha256(
                open(os.path.join(self.model_path, "tokenizer.json"),
                     "rb").read() if os.path.exists(
                    os.path.join(self.model_path, "tokenizer.json"))
                else b"").hexdigest(),
            "seq_len": self.seq_len,
            "lora": self.lora_cfg,
            "trainable_parameter_count": self._count_trainable(),
            "total_parameter_count": self._count_total(),
            "trainable_percent": round(
                100.0 * self._count_trainable() / max(1, self._count_total()),
                4),
            "optimizer": "AdamW",
            "learning_rate": 1e-4,
            "dtype": "float32",   # CPU: f32 (bf16 matmuls lents sense HW)
            "max_sequence_length": self.seq_len,
        }

    def _count_trainable(self) -> int:
        if self.peft_model is None:
            return 0
        return sum(p.numel() for p in self.peft_model.parameters()
                   if p.requires_grad)

    def _count_total(self) -> int:
        if self.peft_model is None:
            return 0
        return sum(p.numel() for p in self.peft_model.parameters())

    def base_model_identity(self) -> dict:
        return dict(self._base_identity)

    def base_model_hash(self) -> str:
        return self._base_identity["base_model_hash"]

    # ── adapters ─────────────────────────────────────────────────────────
    def _lora_state(self) -> dict:
        """Extreu els tensors LoRA entrenables (noms -> torch.Tensor).
        IMPORTANT: NO usa state_dict() del model sencer (serialitza 1.1B
        params i triga desenes de segons); accedeix directament als
        paràmetres LoRA (lora_A/lora_B dels target modules)."""
        st = {}
        for n, p in self.peft_model.named_parameters():
            if not p.requires_grad:
                continue
            st[n] = p.detach().cpu().float()
        return st

    def load_adapter(self, data: bytes, expected_sha: str | None = None,
                     strict: bool = True):
        if strict and expected_sha is None:
            raise ValueError("expected_sha OBLIGATORI en mode MeshTrainer — "
                             "REJECTED")
        if expected_sha is not None:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected_sha:
                raise ValueError(
                    f"adapter SHA mismatch: {actual[:12]} != "
                    f"{expected_sha[:12]} — REJECTED")
        tensors = unpack_tensors(data)  # format codec generic
        self._apply_tensors_strict(tensors)
        return {"missing": [], "unexpected": []}

    def _apply_tensors_strict(self, tensors: dict):
        """Aplica un dict de tensors LoRA al model amb validacio estricta
        (noms exactes, shapes exactes, dtype correcte)."""
        import torch
        cur = self._lora_state()
        names = set(cur)
        missing = sorted(names - set(tensors))
        extra = sorted(set(tensors) - names)
        if missing or extra:
            raise ValueError(
                f"adapter schema mismatch: missing={missing[:3]} "
                f"extra={extra[:3]} — REJECTED")
        for n in names:
            got_np = np.asarray(tensors[n])
            if got_np.dtype not in (np.float32, np.float64, np.float16):
                raise ValueError(f"dtype mismatch {n}: "
                                 f"{got_np.dtype} no és float — REJECTED")
            got = np.asarray(got_np, dtype=np.float32)
            if tuple(got.shape) != tuple(cur[n].shape):
                raise ValueError(f"shape mismatch {n}: "
                                 f"{tuple(got.shape)} != "
                                 f"{tuple(cur[n].shape)} — REJECTED")
        with torch.no_grad():
            params = dict(self.peft_model.named_parameters())
            for n in names:
                params[n].copy_(torch.as_tensor(tensors[n],
                                                dtype=params[n].dtype))

    def adapter_to_bundle(self) -> bytes:
        return pack_tensors(to_numpy_dict(self._lora_state()))

    def adapter_hash(self) -> str:
        return self._hash_state(self._lora_state())

    def post_hash(self) -> str:
        # reconstruccio pre+delta en float32: 1 ulp possible -> hash tolerant
        # com el qwen (mateix criteri: compara amb el mètode de verificacio)
        return self._hash_state(self._lora_state(), tolerant=True)

    def _hash_state(self, state: dict, tolerant: bool = False) -> str:
        h = hashlib.sha256()
        for n in sorted(state):
            h.update(n.encode())
            t = np.asarray(state[n], dtype=np.float32)
            if tolerant:
                t = t.astype(np.float16).astype(np.float32)
            h.update(t.tobytes())
        return h.hexdigest()

    # ── dades / entrenament ──────────────────────────────────────────────
    def tokenize_example(self, instruction: str, response: str | None = None):
        """Tokenitza amb el chat template de MiniCPM5 (NO el de Qwen).
        labels = -100 al prompt; només la resposta és entrenable."""
        if response is None:
            msgs = [{"role": "user", "content": instruction}]
        else:
            msgs = [{"role": "user", "content": instruction},
                    {"role": "assistant", "content": response}]
        text = self.tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=(response is None))
        enc = self.tokenizer(
            text, truncation=True, max_length=self.seq_len,
            return_tensors="pt")
        input_ids = enc["input_ids"][0]
        attn = enc["attention_mask"][0]
        labels = input_ids.clone()
        if response is not None:
            # mascara -100 fins al final del prompt de l'usuari
            prompt_text = self.tokenizer.apply_chat_template(
                [{"role": "user", "content": instruction}],
                tokenize=False, add_generation_prompt=True)
            p_enc = self.tokenizer(
                prompt_text, truncation=True, max_length=self.seq_len)
            p_ids = p_enc["input_ids"]
            if hasattr(p_ids, "shape") and p_ids.ndim > 1:
                p_ids = p_ids[0]
            prompt_len = len(p_ids)
            labels[:prompt_len] = -100
        else:
            labels[:] = -100
        return input_ids, attn, labels

    def compute_ett(self, labels) -> int:
        import torch
        if isinstance(labels, torch.Tensor):
            n = int((labels != -100).sum().item())
        else:
            n = int((np.asarray(labels) != -100).sum())
        return max(1, n)

    def train_step(self, update_id: str, instruction: str, response: str, *,
                   worker_id: str = "", assignment_id: str = "",
                   shard_id: str = "", example_id: str = "",
                   request_sha: str | None = None):
        import torch
        r = self._conn.execute(
            f"SELECT * FROM {self.journal_table} WHERE update_id=?",
            (update_id,)).fetchone()
        if r is not None:
            if r["status"] == "APPLIED":
                if r["request_sha"] and r["request_sha"] != request_sha:
                    raise ValueError("request_sha mismatch — REJECTED")
                return r["delta_b64"], r["delta_sha"]
            if r["status"] == "COMMITTED":
                raise ValueError(f"update_id {update_id} already COMMITTED")
        self._lora_pre = {n: v.clone() for n, v in self._lora_state().items()}
        pre_hash = self.adapter_hash()
        input_ids, attn, labels = self.tokenize_example(instruction, response)
        input_ids = input_ids.unsqueeze(0)
        attn = attn.unsqueeze(0)
        labels = labels.unsqueeze(0)
        self.optimizer.zero_grad()
        out = self.peft_model(input_ids=input_ids, attention_mask=attn,
                              labels=labels)
        loss = out.loss
        if not torch.isfinite(loss):
            raise ValueError(f"loss no finit: {loss.item()} — REJECTED")
        loss.backward()
        self.optimizer.step()
        # ALLIBERA els grads de TOT el model (els no-LoRA no s'actualitzen
        # però el backward els va calcular: ~4.4 GB en f32) — sense això el
        # pic de memòria entra en swap thrashing a CT112 (8 GB).
        self.peft_model.zero_grad(set_to_none=True)
        self._last_loss = float(loss.item())
        self._lora_post = {n: v.clone()
                           for n, v in self._lora_state().items()}
        delta = {n: (self._lora_post[n] - self._lora_pre[n])
                 for n in self._lora_pre}
        db = pack_tensors(to_numpy_dict(delta))
        dsha = bundle_sha256(db)
        # post_hash CERTIFICABLE: hash de la reconstrucció pre+delta en
        # f64->f32 (exacte), que és el que el recovery reprodueix. El hash
        # del model post-step (AdamW f32) pot diferir per 1 ulp.
        post_cert = {n: (self._lora_pre[n].double() +
                         delta[n].double()).float()
                     for n in delta}
        post_cert_hash = self._hash_state(post_cert, tolerant=True)
        self._conn.execute(
            f"INSERT OR REPLACE INTO {self.journal_table} (update_id, status,"
            " delta_b64, delta_sha, pre_hash, post_hash, request_sha,"
            " worker_id, assignment_id, shard_id, example_id, loss)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (update_id, "APPLIED", base64.b64encode(db).decode(), dsha,
             pre_hash, post_cert_hash, request_sha, worker_id,
             assignment_id, shard_id, example_id, self._last_loss))
        self._conn.commit()
        return base64.b64encode(db).decode(), dsha

    def recover_applied(self, update_id: str) -> dict | None:
        r = self._conn.execute(
            f"SELECT * FROM {self.journal_table} WHERE update_id=?",
            (update_id,)).fetchone()
        if r is None:
            return None
        if r["status"] != "APPLIED":
            raise ValueError(f"status={r['status']} — recovery només APPLIED")
        if r["pre_hash"] and r["pre_hash"] != self.adapter_hash():
            raise ValueError("pre_hash mismatch — REJECTED")
        delta = unpack_tensors(base64.b64decode(r["delta_b64"]))
        import torch as _t
        # snapshot pre (per a la comprovació de tolerància)
        pre_state = {n: p.detach().cpu().float().clone()
                     for n, p in self.peft_model.named_parameters()
                     if p.requires_grad}
        with _t.no_grad():
            params = dict(self.peft_model.named_parameters())
            for n, t in delta.items():
                if n not in params:
                    raise ValueError(f"tensor desconegut {n} — REJECTED")
                # suma en f64 -> f32 (mateixa reconstrucció certificable que
                # el post_hash guardat al train_step)
                params[n].copy_(
                    (params[n].double() +
                     _t.as_tensor(np.asarray(t, dtype=np.float32),
                                  dtype=_t.float64)).float())
        # verificació post: la reconstrucció pre+delta pot diferir del post
        # original per 1 ulp float32 (suma en f32). L'ordre exigeix
        # "hash/tensors idèntics DINS TOLERÀNCIA DEFINIDA": comprovem el
        # maxdiff per tensor entre la reconstrucció i pre+delta en float64
        # (exacte), amb llindar 1e-5.
        if r["post_hash"] and r["post_hash"] != self.post_hash():
            maxdiff = 0.0
            for n, d in delta.items():
                if n in pre_state:
                    expected = (pre_state[n].double() +
                                _t.as_tensor(np.asarray(d, dtype=np.float32),
                                             dtype=_t.float64))
                    actual = params[n].detach().cpu().double()
                    md = float((actual - expected).abs().max())
                    maxdiff = max(maxdiff, md)
            if maxdiff > 1e-5:
                raise ValueError(
                    "post_hash mismatch: "
                    f"guardat={r['post_hash'][:12]} "
                    f"actual={self.post_hash()[:12]} maxdiff={maxdiff:.2e}"
                    " — REJECTED")
        return {"update_id": update_id, "status": "APPLIED",
                "adapter_post_hash": r["post_hash"],
                "reconstruction_maxdiff": float(maxdiff) if r["post_hash"] and r["post_hash"] != self.post_hash() else 0.0}

    def journal_status(self, update_id: str) -> str | None:
        r = self._conn.execute(
            f"SELECT status FROM {self.journal_table} WHERE update_id=?",
            (update_id,)).fetchone()
        return r["status"] if r else None

    def delta_tensors(self, update_id: str) -> dict:
        r = self._conn.execute(
            f"SELECT delta_b64 FROM {self.journal_table} WHERE update_id=?",
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

    # ── cicle de vida ────────────────────────────────────────────────────
    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
        self.model = None
        self.peft_model = None
        self.tokenizer = None
        self.optimizer = None
        import gc
        gc.collect()
        # allibera el cache allocator de torch (els tensors f32 del model
        # 1B queden retinguts si no es fa explícit)
        try:
            import torch as _t
            _t.set_num_threads(1)
            _t._C._free_workspace() if hasattr(_t._C, "_free_workspace") else None
            gc.collect()
        except Exception:
            pass


def build(model_path: str = "/root/minicpm5_1b_snapshot",
          db_path: str = "minicpm5_journal.db", seq_len: int = 512,
          lora: dict | None = None, seed: int = 424242) -> MiniCPM5Backend:
    return MiniCPM5Backend(model_path, db_path=db_path, seq_len=seq_len,
                           lora=lora, seed=seed)
