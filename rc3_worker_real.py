"""
rc3_worker_real.py — RC3.3.3 Real Qwen3 LoRA training worker.

Extends RC3 distributed training pipeline with real model training
using PyTorch + transformers + peft on Qwen3-0.6B.

Components:
  RealTrainingBackend   — Abstract interface for training backends (R2)
  Qwen3TrainingBackend  — Qwen3-0.6B implementation (PyTorch + peft)
  Qwen3RealWorker       — TrainingWorker subclass for RC3 JSON-RPC

Specification: RC3_3_3_SPEC_v3.md
SPDX-FileCopyrightText: 2026 Carles Valls (Vinegart)
SPDX-License-Identifier: AGPL-3.0-only
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from rc3_worker_sim import (
    SyntheticTensorGen, TrainingWorker, SimulatedWorker,
    DEFAULT_TENSOR_NAMES, deterministic_seed, _canonical_key,
)
from rc3_state import RC3_PROTOCOL_VERSION
from synthetic_tensor_store import SyntheticTensorStore

# ═══════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════

TRAINING_PROTOCOL_VERSION = "rc3-training-protocol-v1"   # R1
CACHE_DIR = os.path.expanduser("~/.hermes/rc3/worker_cache")
# Full list of all 224 expected LoRA tensor keys (28 layers × 4 modules × A/B)
LORA_TENSOR_NAMES = [
    f"{layer}.{mod}.{suffix}"
    for layer in range(28)
    for mod in ["q_proj", "k_proj", "v_proj", "o_proj"]
    for suffix in ["lora_A", "lora_B"]
]
ERROR_BASE_CHECKPOINT_MISMATCH = -32008  # B2

log = logging.getLogger("real_worker")


# ═══════════════════════════════════════════════════════════════════════
# RealTrainingBackend — Abstract interface (R2)
# ═══════════════════════════════════════════════════════════════════════

class RealTrainingBackend(ABC):
    """Abstract interface for real training backends.

    Methods:
        load_model(model_id, adapter_config)   — Load model + LoRA
        train_local(texts, run_config)         — Forward/backward/step
        compute_delta()                        — ΔLoRA = trained - initial
        get_adapter_snapshot()                 — Current LoRA weights
    """

    @abstractmethod
    def load_model(self, model_id: str, adapter_config: dict) -> None:
        """Load model from HuggingFace and apply LoRA.

        Args:
            model_id: HuggingFace model ID (e.g. 'Qwen/Qwen3-0.6B')
            adapter_config: LoRA config dict with keys:
                r, alpha, dropout, target_modules, dtype, bias, task_type
        """

    @abstractmethod
    def train_local(self, texts: List[str], run_config: dict) -> Dict[str, float]:
        """Run local training on a list of text samples.

        Args:
            texts: List of input text strings (one per microbatch).
            run_config: Dict with training hyperparameters.

        Returns:
            dict with keys 'loss', 'grad_norm'.
        """

    @abstractmethod
    def compute_delta(self) -> Dict[str, np.ndarray]:
        """Compute ΔLoRA = adapter_trained - adapter_initial.

        Must be called AFTER train_local() completes. Result is cached
        and never recalculated (B1).

        Returns:
            dict mapping tensor name (e.g. 'q_proj.lora_A') to np.ndarray.
        """

    @abstractmethod
    def get_adapter_snapshot(self) -> Dict[str, np.ndarray]:
        """Return current LoRA weights as dict of np.ndarray (float32)."""


# ═══════════════════════════════════════════════════════════════════════
# Qwen3TrainingBackend — Real Qwen3-0.6B with PyTorch + peft
# ═══════════════════════════════════════════════════════════════════════

class Qwen3TrainingBackend(RealTrainingBackend):
    """Qwen3-0.6B training backend using PyTorch + transformers + peft.

    Loads the actual Qwen3-0.6B model, applies LoRA, and performs real
    forward/backward/optimizer steps. Works on CPU (Pi4 Cortex-A72).

    Internal state:
        _adapter_initial  — Snapshot before training (B1)
        _adapter_trained  — Snapshot after training
        _adapter_delta    — ΔLoRA, computed once (B1)
        _delta_computed   — Flag to prevent recalculation
    """

    def __init__(self, device: str = "cpu", seed: int = 42):
        self.device = device
        self.seed = seed

        # Model (lazy load)
        self._model = None
        self._tokenizer = None
        self._optimizer = None
        self._adapter_config = None
        self._loaded_model_id = None

        # Adapter snapshots (B1, T11)
        self._adapter_initial: Dict[str, np.ndarray] = {}
        self._adapter_trained: Dict[str, np.ndarray] = {}
        self._adapter_delta: Dict[str, np.ndarray] = {}
        self._delta_computed = False

        # Training metrics
        self._last_loss: Optional[float] = None
        self._last_grad_norm: Optional[float] = None

    def _import_torch(self):
        """Lazy import torch and friends.

        Raises ImportError with clear message if not installed.
        """
        try:
            import torch
            import transformers
            import peft
            return torch, transformers, peft
        except ImportError as e:
            raise ImportError(
                f"PyTorch, transformers, or peft not installed: {e}. "
                f"Run: pip install torch --index-url "
                f"https://download.pytorch.org/whl/cpu"
            ) from e

    # ── Adapter config helpers ─────────────────────────────────────────

    @staticmethod
    def compute_adapter_config_hash(adapter_config: dict) -> str:
        """Compute SHA-256 of adapter configuration (T6).

        Uses canonical JSON serialisation with sorted keys.
        """
        canonical = json.dumps(adapter_config, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def make_default_adapter_config() -> dict:
        """Return the MVP adapter configuration."""
        return {
            "r": 8,
            "alpha": 16,
            "dropout": 0.1,
            "target_modules": sorted(["q_proj", "k_proj", "v_proj", "o_proj"]),
            "dtype": "float32",
            "bias": "none",
            "task_type": "CAUSAL_LM",
        }

    # ── Model loading ─────────────────────────────────────────────────

    def load_model(self, model_id: str, adapter_config: Optional[dict] = None) -> None:
        """Load Qwen3-0.6B model and apply LoRA.

        Args:
            model_id: HuggingFace model ID (e.g. 'Qwen/Qwen3-0.6B').
            adapter_config: LoRA config. Uses default if None.

        Raises:
            ImportError: If torch/transformers/peft not installed.
            RuntimeError: If model loading fails (OOM, network, etc.).
        """
        torch, transformers, peft = self._import_torch()

        if adapter_config is None:
            adapter_config = self.make_default_adapter_config()

        self._adapter_config = adapter_config
        log.info(f"Loading model {model_id} ... (CPU, float32)")

        try:
            tokenizer = transformers.AutoTokenizer.from_pretrained(
                model_id,
                trust_remote_code=True,
            )
            # Ensure padding token exists
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            model = transformers.AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=torch.float32,
                device_map="cpu",
                low_cpu_mem_usage=True,
                trust_remote_code=True,
            )
            model.train()

            # Apply LoRA
            lora_config = peft.LoraConfig(
                r=adapter_config["r"],
                lora_alpha=adapter_config["alpha"],
                target_modules=adapter_config["target_modules"],
                lora_dropout=adapter_config["dropout"],
                bias=adapter_config["bias"],
                task_type=adapter_config["task_type"],
            )
            model = peft.get_peft_model(model, lora_config)

            # Freeze base model, only LoRA params trainable
            model.print_trainable_parameters()

            # Optimiser: AdamW on LoRA params only (B3)
            optimizer = torch.optim.AdamW(
                model.parameters(),
                lr=2e-4,
                betas=(0.9, 0.999),
                eps=1e-8,
                weight_decay=0.01,
            )

            self._model = model
            self._tokenizer = tokenizer
            self._optimizer = optimizer
            self._loaded_model_id = model_id
            self._delta_computed = False

            # Snapshot initial LoRA weights (B1)
            self._adapter_initial = self._extract_lora_weights()
            self._adapter_trained = {}
            self._adapter_delta = {}

            log.info(f"Model loaded. LoRA params: "
                     f"{sum(p.numel() for p in model.parameters() if p.requires_grad):,}")
        except Exception as e:
            raise RuntimeError(f"Failed to load model {model_id}: {e}") from e

    def _extract_lora_weights(self) -> Dict[str, np.ndarray]:
        """Extract current LoRA weights as float32 np.ndarray dict.

        Key format: 'N.module.lora_X' (layer.module.type).

        Returns:
            dict mapping 'N.module.lora_X' → np.ndarray (224 entries).
        """
        weights: Dict[str, np.ndarray] = {}
        target_modules = set(
            self._adapter_config.get("target_modules",
                                     ["q_proj", "k_proj", "v_proj", "o_proj"])
        )
        for name, param in self._model.named_parameters():
            # peft: base_model.model.model.layers.N.self_attn.q_proj.lora_A.default.weight
            parts = name.split(".")
            try:
                layers_idx = parts.index("layers")
                layer_num = parts[layers_idx + 1]
            except ValueError:
                continue
            for tm in target_modules:
                if tm not in parts:
                    continue
                lora_parts = [p for p in parts if p.startswith("lora_")]
                if not lora_parts:
                    continue
                tm_idx = parts.index(tm)
                lora_suffix = parts[tm_idx + 1]
                key = f"{layer_num}.{tm}.{lora_suffix}"
                weights[key] = param.detach().cpu().numpy().astype(np.float32)
                break
        return weights

    # ── Training ──────────────────────────────────────────────────────

    def train_local(self, texts: List[str], run_config: dict) -> Dict[str, float]:
        """Run local training steps on the model.

        Args:
            texts: List of text samples to train on.
            run_config: Dict with training hyperparameters:
                - local_optimizer_steps: Number of optimizer steps (default 4)
                - gradient_accumulation_steps: Grad accum (default 8)
                - learning_rate: Override LR (optional)
                - max_length: Tokenization max length (default 256)

        Returns:
            dict with keys 'loss', 'grad_norm'.
        """
        torch = self._import_torch()[0]

        if self._model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        local_steps = run_config.get("local_optimizer_steps", 4)
        grad_accum = run_config.get("gradient_accumulation_steps", 8)
        max_length = run_config.get("max_length", 256)
        lr = run_config.get("learning_rate", 2e-4)

        # Override LR if specified
        for pg in self._optimizer.param_groups:
            pg["lr"] = lr

        self._model.train()
        total_loss = 0.0
        total_steps = 0

        # Reset delta computation flag (B1: new training → new delta)
        self._delta_computed = False

        # Split texts into microbatches
        microbatches = []
        for i in range(0, len(texts), 1):
            microbatches.append(texts[i:i + 1])

        for step in range(local_steps):
            self._optimizer.zero_grad()
            step_loss = 0.0

            for accum_idx in range(grad_accum):
                # Get microbatch text
                idx = (step * grad_accum + accum_idx) % len(microbatches)
                chunk = microbatches[idx]

                # Tokenize
                enc = self._tokenizer(
                    chunk,
                    truncation=True,
                    padding="max_length",
                    max_length=max_length,
                    return_tensors="pt",
                    add_special_tokens=True,
                )
                input_ids = enc["input_ids"].to(self.device)
                attention_mask = enc["attention_mask"].to(self.device)

                # Forward pass
                outputs = self._model(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    labels=input_ids,  # Language modelling: predict same input
                )
                loss = outputs.loss / grad_accum
                step_loss += loss.item()

                # Backward pass (accumulated)
                loss.backward()

            # Gradient clipping
            grad_norm = torch.nn.utils.clip_grad_norm_(
                self._model.parameters(), max_norm=1.0
            ).item()

            # Optimizer step
            self._optimizer.step()

            total_loss += step_loss
            total_steps += 1

            # Clear intermediate tensors to save memory (Pi4)
            del loss, outputs, input_ids, attention_mask
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        avg_loss = total_loss / max(total_steps, 1)
        self._last_loss = avg_loss
        self._last_grad_norm = grad_norm

        return {"loss": avg_loss, "grad_norm": grad_norm}

    # ── Delta computation ────────────────────────────────────────────

    def compute_delta(self) -> Dict[str, np.ndarray]:
        """Compute ΔLoRA = adapter_trained - adapter_initial.

        Called IMMEDIATELY after train_local() (B1). Result is cached
        and never recalculated. Subsequent calls return cached delta.
        """
        if self._delta_computed:
            log.warning("ΔLoRA already computed. Returning cached value (B1).")
            return self._adapter_delta

        self._adapter_trained = self._extract_lora_weights()

        # ΔLoRA = trained - initial
        self._adapter_delta = {}
        all_keys = set(self._adapter_initial.keys()) | set(self._adapter_trained.keys())
        for key in sorted(all_keys):
            initial = self._adapter_initial.get(key, np.zeros((1,), dtype=np.float32))
            trained = self._adapter_trained.get(key, np.zeros((1,), dtype=np.float32))
            if initial.shape != trained.shape:
                log.warning(f"Shape mismatch for {key}: {initial.shape} vs {trained.shape}")
                self._adapter_delta[key] = np.zeros_like(initial)
            else:
                self._adapter_delta[key] = (trained - initial).astype(np.float32)

        self._delta_computed = True
        return self._adapter_delta

    def get_adapter_snapshot(self) -> Dict[str, np.ndarray]:
        """Return current LoRA weights as np.ndarray dict."""
        return self._extract_lora_weights()

    # ── Utilities ─────────────────────────────────────────────────────

    def verify_base_checkpoint(self, expected_id: str,
                                expected_sha256: str) -> bool:
        """Verify base checkpoint ID and SHA-256 (B2).

        Args:
            expected_id: Expected checkpoint identifier.
            expected_sha256: Expected SHA-256 of the checkpoint.

        Returns:
            True if both match, False otherwise.

        The SHA-256 is computed over the model's state_dict keys + values
        in sorted order (canonical).
        """
        torch = self._import_torch()[0]
        # Empty SHA-256: skip check (B2 flexibility)
        if not expected_sha256:
            return True
        if self._model is None:
            return False

        # Compute SHA-256 of current model state (O(1) memory)
        state = self._model.state_dict()
        h = hashlib.sha256()
        for key in sorted(state.keys()):
            h.update(key.encode("utf-8"))
            h.update(state[key].detach().cpu().numpy().tobytes())
        actual_sha256 = h.hexdigest()

        # Check ID
        ckpt_id = getattr(self._model, "_checkpoint_id", "unknown")
        id_ok = (ckpt_id == expected_id)
        sha_ok = (actual_sha256 == expected_sha256)

        return id_ok and sha_ok

    def reset_optimizer(self):
        """Reset optimizer state (for crash recovery)."""
        torch = self._import_torch()[0]
        if self._model is not None:
            self._optimizer = torch.optim.AdamW(
                self._model.parameters(),
                lr=2e-4,
                betas=(0.9, 0.999),
                eps=1e-8,
                weight_decay=0.01,
            )
            self._delta_computed = False
            log.info("Optimizer reset (B3: local state lost on crash).")


# ═══════════════════════════════════════════════════════════════════════
# Qwen3RealWorker — RC3 training worker for real Qwen3 LoRA training
# ═══════════════════════════════════════════════════════════════════════

class Qwen3RealWorker(TrainingWorker):
    """Real Qwen3 LoRA training worker for RC3 distributed pipeline.

    Extends TrainingWorker with:
    - Real model loading (Qwen3-0.6B via transformers + peft)
    - Real forward/backward/optimizer training
    - ΔLoRA computation and canonical serialization
    - Disk cache for crash recovery (B4)
    - Base checkpoint SHA-256 verification (B2)
    - Local-only optimizer state (B3)

    Flow (per batch):
        1. request_batch() → batch_assignment
        2. Load model (once, on first batch)
        3. Take adapter_initial snapshot
        4. train_local(): forward/backward/step
        5. compute_delta(): ΔLoRA (IMMEDIATELY)
        6. Store & submit: serialize → store.put → submit_delta
    """

    def __init__(
        self,
        coordinator_url: str,
        worker_type: str = "python",
        fail_mode: str = None,
        tensor_gen: SyntheticTensorGen = None,
        tensor_store: SyntheticTensorStore = None,
        run_id: str = None,
        worker_session_id: str = None,
        run_config: dict = None,
        backend: Optional[RealTrainingBackend] = None,
        model_id: str = os.environ.get("QWEN_MODEL_PATH", "/root/qwen3_0_6b_snapshot"),
    ):
        # Default run_config matching spec
        if run_config is None:
            run_config = {
                "model_id": model_id,
                "adapter_config_hash": Qwen3TrainingBackend.compute_adapter_config_hash(
                    Qwen3TrainingBackend.make_default_adapter_config()
                ),
                "base_checkpoint_id": "ckpt-base-001",
                "base_checkpoint_sha256": "",  # Must be provided by caller
                "dataset_revision": "sha256:mvp-synthetic-v1",
                "tokenizer_revision": "sha256:qwen3-tokenizer-v1",
                "prompt_template_revision": "sha256:chatml-v2",
                "run_config_hash": "default_config",
                "learning_rate": 2e-4,
                "local_optimizer_steps": 4,
                "gradient_accumulation_steps": 8,
                "max_length": 256,
                "local_epochs": 1,
                "simulation_seed": 42,
            }

        # Create default backend
        if backend is None:
            seed = run_config.get("simulation_seed", 42)
            backend = Qwen3TrainingBackend(device="cpu", seed=seed)

        # Need a tensor_gen for SyntheticTensorGen.serialize_canonical
        if tensor_gen is None:
            tensor_gen = SyntheticTensorGen(
                d_model=64, d_out=64, lora_r=8,
                seed=run_config.get("simulation_seed", 42),
            )

        super().__init__(
            coordinator_url=coordinator_url,
            worker_type=worker_type,
            fail_mode=fail_mode,
            tensor_gen=tensor_gen,
            tensor_store=tensor_store,
            run_id=run_id,
            worker_session_id=worker_session_id,
            run_config=run_config,
        )

        self.backend = backend
        self.model_id = model_id
        self._model_loaded = False
        self._cache_dir = CACHE_DIR
        self._delta_serialized: Optional[bytes] = None
        self._delta_sha256: Optional[str] = None

        # Create cache directory if needed
        os.makedirs(self._cache_dir, exist_ok=True)

    # ── Model lifecycle ───────────────────────────────────────────────

    def _ensure_model_loaded(self):
        """Lazy-load the model on first batch."""
        if not self._model_loaded:
            adapter_config = Qwen3TrainingBackend.make_default_adapter_config()
            # Allow override from run_config
            if self.run_config and "adapter_config" in self.run_config:
                adapter_config = self.run_config["adapter_config"]
            self.backend.load_model(self.model_id, adapter_config)
            self._model_loaded = True
            log.info(f"Model {self.model_id} loaded for training")

    # ── Synthetic text generation for MVP ─────────────────────────────

    def _generate_synthetic_texts(self, batch_result: dict,
                                   run_config: dict) -> List[str]:
        """Generate synthetic training texts for MVP.

        Uses deterministic seed derived from batch_id + dataset_revision.

        Args:
            batch_result: Batch assignment from coordinator.
            run_config: Run configuration.

        Returns:
            List of text strings (one per microbatch).
        """
        seed_key = deterministic_seed(
            f"{batch_result.get('batch_id', 'batch')}:"
            f"{run_config.get('dataset_revision', 'dataset')}:"
            f"{run_config.get('simulation_seed', 42)}"
        )
        rng = np.random.default_rng(seed_key)
        n_steps = run_config.get("local_optimizer_steps", 4)
        n_accum = run_config.get("gradient_accumulation_steps", 8)
        n_texts = n_steps * n_accum

        templates = [
            "Aquest es un text de prova per entrenar el model en catala.",
            "El proces d'entrenament distribuït utilitza LoRA per eficiencia.",
            "Qwen3 es un model de llenguatge petit pero potent per CPU.",
            "La Raspberry Pi 4 pot executar training basic amb LoRA.",
            "El FedAvg combina els deltes de tots els workers.",
            "El protocol JSON-RPC connecta workers amb el coordinador.",
            "Cada worker procesa un subconjunt diferent del dataset.",
            "El checkpoint sha256 garanteix la reproducibilitat.",
        ]

        texts = []
        for i in range(n_texts):
            idx = seed_key % len(templates) + i
            template = templates[idx % len(templates)]
            text = f"{template} Batch {batch_result.get('batch_id', '?')} Sample {i}"
            texts.append(text)

        return texts

    # ── Cache management (B4) ─────────────────────────────────────────

    def _cache_path(self, batch_id: str) -> str:
        return os.path.join(self._cache_dir, str(batch_id))

    def _save_delta_cache(self, batch_id: str, serialized: bytes,
                          delta_sha256: str, lease_token: str):
        """Save ΔLoRA cache to disk for crash recovery (B4)."""
        cache_path = self._cache_path(batch_id)
        os.makedirs(cache_path, exist_ok=True)
        meta = {
            "delta_sha256": delta_sha256,
            "lease_token": lease_token,
            "run_id": self.run_id,
            "batch_id": batch_id,
            "worker_session_id": self._worker_session_id,
            "training_protocol_version": TRAINING_PROTOCOL_VERSION,
        }
        with open(os.path.join(cache_path, "metadata.json"), "w") as f:
            json.dump(meta, f, sort_keys=True)
        with open(os.path.join(cache_path, "delta.bin"), "wb") as f:
            f.write(serialized)
        log.info(f"ΔLoRA cached to {cache_path}")

    def _load_delta_cache(self, batch_id: str) -> Optional[Tuple[bytes, str, str]]:
        """Load cached ΔLoRA if available.

        Returns:
            (serialized, delta_sha256, lease_token) or None.
        """
        cache_path = self._cache_path(batch_id)
        meta_path = os.path.join(cache_path, "metadata.json")
        delta_path = os.path.join(cache_path, "delta.bin")
        if not (os.path.isfile(meta_path) and os.path.isfile(delta_path)):
            return None
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            with open(delta_path, "rb") as f:
                serialized = f.read()
            return serialized, meta["delta_sha256"], meta["lease_token"]
        except (json.JSONDecodeError, KeyError, OSError) as e:
            log.warning(f"Corrupt cache for {batch_id}: {e}")
            return None

    def _clear_cache(self, batch_id: str):
        """Remove cached data for a batch."""
        cache_path = self._cache_path(batch_id)
        if os.path.isdir(cache_path):
            import shutil
            shutil.rmtree(cache_path, ignore_errors=True)

    # ── Batch training cycle ─────────────────────────────────────────

    def do_training_batch(self, request_kwargs: dict = None) -> bool:
        """Full real training batch cycle.

        Returns True on success, False on failure.
        """
        # 1. Request batch from coordinator
        batch_result = self.request_batch(**(request_kwargs or {}))
        if batch_result is None:
            return False

        batch_id = batch_result.get("batch_id", "unknown")
        lease_token = batch_result.get("lease_token", "")

        # 2. Check for cached ΔLoRA (B4 crash recovery)
        cache = self._load_delta_cache(batch_id)
        if cache is not None:
            serialized, delta_sha256, cached_lease = cache
            # Check if lease is still valid (best effort)
            # If we have a cached delta, attempt to resubmit directly
            log.info(f"Found cached ΔLoRA for batch {batch_id}, attempting resubmit (B4)")
            return self._submit_cached(batch_result, serialized, delta_sha256, cached_lease)

        # 3. Load model (lazy, once)
        try:
            self._ensure_model_loaded()
        except (ImportError, RuntimeError) as e:
            log.error(f"Model loading failed: {e}")
            return False

        # 4. Verify base checkpoint (B2)
        expected_ckpt_id = self.run_config.get("base_checkpoint_id")
        expected_sha256 = self.run_config.get("base_checkpoint_sha256", "")
        if expected_sha256 and not self.backend.verify_base_checkpoint(
            expected_ckpt_id, expected_sha256
        ):
            log.error(f"Base checkpoint mismatch (B2): "
                      f"expected={expected_ckpt_id}/{expected_sha256[:16]}...")
            self._report_error(ERROR_BASE_CHECKPOINT_MISMATCH,
                               "base_checkpoint_mismatch",
                               f"Checkpoint {expected_ckpt_id} SHA-256 mismatch")
            return False

        # 5. Generate synthetic training texts (MVP)
        texts = self._generate_synthetic_texts(batch_result, self.run_config)

        # 6. Report progress (start training)
        self.report_progress(batch_id,
                             batch_result.get("assignment_id", ""),
                             lease_token, progress_pct=10)

        # 7. Train locally
        try:
            metrics = self.backend.train_local(texts, self.run_config)
            log.info(f"Training complete: loss={metrics['loss']:.6f}, "
                     f"grad_norm={metrics['grad_norm']:.6f}")
        except Exception as e:
            log.error(f"Training failed: {e}")
            return False

        self.report_progress(batch_id,
                             batch_result.get("assignment_id", ""),
                             lease_token, progress_pct=60)

        # 8. Compute ΔLoRA IMMEDIATELY after training (B1)
        try:
            delta = self.backend.compute_delta()
            log.info(f"ΔLoRA computed: {len(delta)} tensors")
        except Exception as e:
            log.error(f"ΔLoRA computation failed: {e}")
            return False

        self.report_progress(batch_id,
                             batch_result.get("assignment_id", ""),
                             lease_token, progress_pct=80)

        # 9. Serialize
        try:
            serialized = SyntheticTensorGen.serialize_canonical(delta)
            delta_sha256 = hashlib.sha256(serialized).hexdigest()
        except Exception as e:
            log.error(f"Serialization failed: {e}")
            return False

        # 10. Store in SyntheticTensorStore
        ref = self.tensor_store.put(
            self.run_id, batch_id, serialized, delta_sha256
        )

        # 11. Cache ΔLoRA on disk (B4) BEFORE submitting
        self._save_delta_cache(batch_id, serialized, delta_sha256, lease_token)

        # 12. Submit delta
        manifest = {
            "tensor_names": list(delta.keys()),
            "lora_r": self.backend._adapter_config.get("r", 8) if hasattr(self.backend, "_adapter_config") else 8,
            "lora_alpha": 16,
            "dtype": "float32",
            "shapes": {k: list(v.shape) for k, v in delta.items()},
            "delta_sha256": delta_sha256,
            "delta_size_bytes": len(serialized),
            "serialization_version": "v1",
            "compression": None,
        }

        submit_params = {
            "run_id": self.run_id,
            "batch_id": batch_id,
            "lease_token": lease_token,
            "worker_session_id": self._worker_session_id,
            "assignment_id": batch_result.get("assignment_id", ""),
            "aggregation_generation": batch_result.get("aggregation_generation", 0),
            "model_revision": self.run_config.get("model_revision", "sha256:base"),
            "adapter_config_hash": self.run_config.get("adapter_config_hash", ""),
            "base_checkpoint_id": self.run_config.get("base_checkpoint_id", "ckpt-base-001"),
            "dataset_revision": self.run_config.get("dataset_revision", "sha256:dataset"),
            "delta_sha256": delta_sha256,
            "samples_processed": batch_result.get("batch", {}).get("num_samples", 4),
            "loss": metrics["loss"],
            "grad_norm": metrics["grad_norm"],
            "training_protocol_version": TRAINING_PROTOCOL_VERSION,
            "optimizer_state_local": True,  # B3: explicit
            "upload_reference": ref,
            "delta_manifest": manifest,
        }

        submit_result = self.submit_delta(submit_params)
        if submit_result:
            log.info(f"Batch {batch_id}: accepted ✓")
            self._clear_cache(batch_id)  # Clean up on success
            return True
        else:
            log.warning(f"Batch {batch_id}: rejected")
            return False

    def _submit_cached(self, batch_result: dict,
                        serialized: bytes,
                        delta_sha256: str,
                        cached_lease: str) -> bool:
        """Resubmit cached ΔLoRA (B4 crash recovery path).

        Called when a cached delta is found on disk. Attempts to submit
        directly without retraining.
        """
        batch_id = batch_result.get("batch_id", "unknown")
        lease_token = batch_result.get("lease_token", cached_lease)
        assignment_id = batch_result.get("assignment_id", "")

        # Re-store the serialized data
        ref = self.tensor_store.put(
            self.run_id, batch_id, serialized, delta_sha256
        )

        manifest = {
            "tensor_names": LORA_TENSOR_NAMES,
            "delta_sha256": delta_sha256,
            "delta_size_bytes": len(serialized),
            "serialization_version": "v1",
            "compression": None,
        }

        submit_params = {
            "run_id": self.run_id,
            "batch_id": batch_id,
            "lease_token": lease_token,
            "worker_session_id": self._worker_session_id,
            "assignment_id": assignment_id,
            "aggregation_generation": batch_result.get("aggregation_generation", 0),
            "delta_sha256": delta_sha256,
            "loss": 0.0,
            "grad_norm": 0.0,
            "training_protocol_version": TRAINING_PROTOCOL_VERSION,
            "optimizer_state_local": True,
            "upload_reference": ref,
            "delta_manifest": manifest,
            "recovered_from_cache": True,  # B4 marker
        }

        submit_result = self.submit_delta(submit_params)
        if submit_result:
            log.info(f"Batch {batch_id}: cached ΔLoRA accepted ✓ (B4 recovery)")
            self._clear_cache(batch_id)
            return True
        else:
            log.warning(f"Batch {batch_id}: cached ΔLoRA rejected, "
                        f"lease may have expired (B4)")
            # Lease expired: clear cache and return False
            # Coordinator will reassign batch
            self._clear_cache(batch_id)
            return False

    def _report_error(self, code: int, message: str, detail: str):
        """Log an error (JSON-RPC error format for consistency)."""
        log.error(f"[{code}] {message}: {detail}")

    def run_training_loop(self, max_batches: int = 10) -> int:
        """Run multiple training batch cycles.

        Returns number of successfully completed batches.
        """
        completed = 0
        for i in range(max_batches):
            self.heartbeat(status="training")
            if self.do_training_batch():
                completed += 1
            else:
                log.info(f"No more batches or error at batch {i + 1}")
                break
        log.info(f"Training complete: {completed}/{max_batches} batches")
        return completed

    def cleanup(self):
        """Clean up disk cache for stale batches."""
        try:
            if os.path.isdir(self._cache_dir):
                for entry in os.listdir(self._cache_dir):
                    entry_path = os.path.join(self._cache_dir, entry)
                    if os.path.isdir(entry_path):
                        import shutil
                        shutil.rmtree(entry_path, ignore_errors=True)
                log.info("Cache cleaned")
        except Exception as e:
            log.warning(f"Cache cleanup failed: {e}")


# ═══════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Qwen3RealWorker — RC3.3.3 real LoRA training worker"
    )
    parser.add_argument("--coordinator", default="http://localhost:8791",
                        help="Coordinator URL")
    parser.add_argument("--run-id", default=None,
                        help="Pre-assigned run ID")
    parser.add_argument("--model-id", default=os.environ.get("QWEN_MODEL_PATH", "/root/qwen3_0_6b_snapshot"),
                        help="HuggingFace model ID")
    parser.add_argument("--max-batches", type=int, default=10,
                        help="Maximum batches to process")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    parser.add_argument("--lr", type=float, default=2e-4,
                        help="Learning rate")
    parser.add_argument("--checkpoint-dir", default=None,
                        help="Checkpoint directory")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of workers (for multi-worker)")
    parser.add_argument("--list-models", action="store_true",
                        help="List available/supported models and exit")
    args = parser.parse_args()

    if args.list_models:
        print("Supported models for RealTrainingBackend:")
        print("  - Qwen/Qwen3-0.6B (default, MVP)")
        print("  - Qwen/Qwen3-0.6B-GGUF (via llama-cpp-python, not MVP)")
        sys.exit(0)

    logging.basicConfig(
        level=logging.INFO,
        format="[REAL-WORKER %(name)s] %(message)s",
    )

    # Create backend
    backend = Qwen3TrainingBackend(device="cpu", seed=args.seed)

    # Create run_config
    run_config = {
        "model_id": args.model_id,
        "simulation_seed": args.seed,
        "learning_rate": args.lr,
        "adapter_config_hash": Qwen3TrainingBackend.compute_adapter_config_hash(
            Qwen3TrainingBackend.make_default_adapter_config()
        ),
        "base_checkpoint_id": "ckpt-base-001",
        "base_checkpoint_sha256": "",
        "dataset_revision": "sha256:mvp-synthetic-v1",
        "tokenizer_revision": "sha256:qwen3-tokenizer-v1",
        "prompt_template_revision": "sha256:chatml-v2",
        "run_config_hash": "default_config",
    }

    # Create worker
    worker = Qwen3RealWorker(
        coordinator_url=args.coordinator,
        run_id=args.run_id,
        run_config=run_config,
        backend=backend,
        model_id=args.model_id,
    )

    # Register with coordinator
    log.info(f"Registering with coordinator at {args.coordinator}")
    if not worker.register():
        log.error("Registration failed")
        sys.exit(1)

    # Run training loop
    try:
        completed = worker.run_training_loop(max_batches=args.max_batches)
        log.info(f"Worker finished: {completed} batches completed")
    except KeyboardInterrupt:
        log.info("Worker interrupted by user")
    except Exception as e:
        log.error(f"Worker error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        worker.cleanup()


if __name__ == "__main__":
    main()
