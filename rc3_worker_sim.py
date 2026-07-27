"""
rc3_worker_sim.py — RC3.1 Simulated worker.

Worker que es connecta al coordinador, rep tasques, executa una operació
determinista simulada, i retorna el resultat.

Pot simular:
  - execució correcta
  - execució lenta (per timeout)
  - resultat corrupte
  - desconnexió (worker desapareix)

RC3.3.2 extensions:
  - SyntheticTensorGen: deterministic synthetic tensor generation
    with SHA-256 seed derivation and canonical serialization.
  - TrainingWorker: training-aware worker that submits real tensor
    deltas via SyntheticTensorStore.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import urllib.request
import uuid

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from rc3_state import RC3_PROTOCOL_VERSION, simulated_compute

logging.basicConfig(level=logging.INFO,
                    format="[WORKER %(name)s] %(message)s")
log = logging.getLogger("worker")


class SimulatedWorker:
    """Simulated RC3 worker that communicates with coordinator via JSON-RPC."""

    def __init__(self, coordinator_url: str,
                 worker_type: str = "python",
                 capabilities: dict = None,
                 fail_mode: str = None):
        self.url = coordinator_url.rstrip("/")
        self.worker_type = worker_type
        self.capabilities = capabilities or {
            "type": worker_type,
            "compute_units": 4,
            "memory_mb": 2048,
            "runtime": {"python_version": "3.11"},
        }
        self.fail_mode = fail_mode  # None, 'corrupt', 'slow', 'disappear'
        self.worker_id = None
        self.auth_token = None

    def _rpc(self, method: str, params: dict) -> dict:
        """Send JSON-RPC request to coordinator."""
        body = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": str(uuid.uuid4().hex[:8]),
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "X-Protocol-Version": RC3_PROTOCOL_VERSION,
        }
        if self.worker_id:
            headers["X-Worker-Id"] = self.worker_id
        if self.auth_token:
            headers["X-Worker-Token"] = self.auth_token

        req = urllib.request.Request(
            self.url, data=body, headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def register(self) -> bool:
        """Register with coordinator."""
        result = self._rpc("worker.register", {
            "capabilities": self.capabilities,
        })
        if "error" in result:
            log.error(f"Registration failed: {result['error']}")
            return False
        r = result["result"]
        self.worker_id = r["worker_id"]
        self.auth_token = r["auth_token"]
        log.info(f"Registered as {self.worker_id}")
        return True

    def heartbeat(self, status: str = "idle", progress: int = 0) -> dict:
        """Send heartbeat."""
        return self._rpc("worker.heartbeat", {
            "worker_id": self.worker_id,
            "status": status,
            "progress_pct": progress,
        })

    def request_task(self) -> dict:
        """Request a task from coordinator."""
        result = self._rpc("task.request", {
            "worker_id": self.worker_id,
        })
        if "error" in result:
            log.error(f"Task request error: {result['error']}")
            return None
        return result.get("result", {}).get("task")

    def submit_result(self, task_id: str, output_hash: str = None,
                      status: str = "completed",
                      execution_metadata: dict = None) -> dict:
        """Submit task result."""
        return self._rpc("task.submit", {
            "task_id": task_id,
            "worker_id": self.worker_id,
            "status": status,
            "output_hash": output_hash,
            "execution_metadata": execution_metadata or {
                "duration_seconds": 1.0,
                "samples_processed": 10,
                "steps_completed": 5,
            },
        })

    def run_once(self, simulate_duration: float = 0.1) -> bool:
        """Run a single task cycle: request → compute → submit."""
        task = self.request_task()
        if not task:
            return False

        task_id = task["task_id"]
        log.info(f"Got task {task_id} (strategy={task.get('strategy')})")

        # Simulate compute
        if self.fail_mode == "slow":
            time.sleep(simulate_duration * 5)
        else:
            time.sleep(simulate_duration)

        # Compute deterministic output
        input_hash = task.get("input_hash", "default_input")
        config_seed = int(task.get("config_hash", "2a")[:8], 16) if \
            task.get("config_hash") else 42
        output = simulated_compute(input_hash, config_seed)

        if self.fail_mode == "corrupt":
            output = "corrupt_" + output

        # Submit
        result = self.submit_result(task_id, output_hash=output)
        r = result.get("result", {})

        if r.get("status") == "accepted":
            log.info(f"Task {task_id}: accepted ✓")
            return True
        else:
            log.warning(f"Task {task_id}: {r.get('status')} "
                        f"({r.get('validation_details', {}).get('reason')})")
            return False

    def run_loop(self, max_tasks: int = 5, simulate_duration: float = 0.1):
        """Run multiple task cycles."""
        if not self.worker_id:
            if not self.register():
                return

        completed = 0
        for _ in range(max_tasks):
            # Heartbeat before requesting
            self.heartbeat(status="idle")

            if self.run_once(simulate_duration):
                completed += 1

            if self.fail_mode == "disappear" and completed >= 1:
                log.info("Simulating disconnect (fail_mode=disappear)")
                return

        log.info(f"Completed {completed}/{max_tasks} tasks")


def main():
    parser = argparse.ArgumentParser(description="RC3.1 Simulated Worker")
    parser.add_argument("--coordinator", default="http://localhost:8791",
                        help="Coordinator URL")
    parser.add_argument("--tasks", type=int, default=3,
                        help="Number of tasks to run")
    parser.add_argument("--type", default="python",
                        choices=["python", "webgpu", "wasm"])
    parser.add_argument("--fail", default=None,
                        choices=["corrupt", "slow", "disappear"],
                        help="Failure mode")
    parser.add_argument("--delay", type=float, default=0.1,
                        help="Simulated compute duration per task")
    args = parser.parse_args()

    worker = SimulatedWorker(
        args.coordinator,
        worker_type=args.type,
        fail_mode=args.fail,
    )
    worker.run_loop(max_tasks=args.tasks, simulate_duration=args.delay)


# ═════════════════════════════════════════════════════════════════════
# RC3.3.2 — Deterministic synthetic tensor generation
# ═════════════════════════════════════════════════════════════════════

def deterministic_seed(key: str) -> int:
    """SHA-256 based deterministic seed (NOT hash() — cross-process stable)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _canonical_key(simulation_seed: int, run_config_hash: str,
                    dataset_revision: str, batch_id: str,
                    base_checkpoint_id: str,
                    aggregation_generation: int) -> str:
    """Build canonical deterministic key (B2: no worker_id/assignment_id)."""
    return (f"{simulation_seed}:{run_config_hash}:{dataset_revision}:"
            f"{batch_id}:{base_checkpoint_id}:{aggregation_generation}")


DEFAULT_TENSOR_NAMES = [
    "q_proj.lora_A", "q_proj.lora_B",
    "v_proj.lora_A", "v_proj.lora_B",
    "o_proj.lora_A", "o_proj.lora_B",
]


class SyntheticTensorGen:
    """Deterministic synthetic tensor generation for LoRA simulation.

    Generates numpy tensors with deterministic content based on canonical
    key. Uses SHA-256 seed derivation (never hash()).

    Attributes:
        d_model: Base model dimension (default 64).
        d_out: Output dimension (default 64).
        lora_r: LoRA rank (default 8).
        seed: Base simulation seed.
        tensor_names: Ordered list of tensor names to generate.
    """

    def __init__(self, d_model: int = 64, d_out: int = 64,
                 lora_r: int = 8, seed: int = 42,
                 tensor_names: list = None):
        self.d_model = d_model
        self.d_out = d_out
        self.lora_r = lora_r
        self.seed = seed
        self.tensor_names = tensor_names or DEFAULT_TENSOR_NAMES
        self._shapes = self._build_shapes()

    def _build_shapes(self) -> dict:
        """Build shape dict for each tensor."""
        return {
            "q_proj.lora_A": (self.d_model, self.lora_r),
            "q_proj.lora_B": (self.lora_r, self.d_out),
            "v_proj.lora_A": (self.d_model, self.lora_r),
            "v_proj.lora_B": (self.lora_r, self.d_out),
            "o_proj.lora_A": (self.d_model, self.lora_r),
            "o_proj.lora_B": (self.lora_r, self.d_out),
        }

    def _make_deterministic_tensor(self, name: str,
                                    seed1: int, seed2: int,
                                    offset1: int, offset2: int) -> np.ndarray:
        """Generate a single deterministic tensor for testing.

        Args:
            name: Tensor name (e.g. 'q_proj.lora_A').
            seed1, seed2: Seed components for RNG.
            offset1, offset2: Additional derivation offsets.

        Returns:
            float32 numpy array with shape from _build_shapes().
        """
        shape = self._shapes.get(name, (self.d_model, self.lora_r))
        key = f"{name}:{seed1}:{seed2}:{offset1}:{offset2}"
        rng_seed = deterministic_seed(key)  # noqa
        rng = np.random.default_rng(rng_seed)
        return rng.standard_normal(shape, dtype=np.float32)

    def generate_batch_delta(self, key_params: dict) -> dict:
        """Generate deterministic synthetic tensor delta for a batch.

        Args:
            key_params: dict with keys:
                - simulation_seed: int (base seed for the simulation)
                - run_config_hash: str (run config identifier)
                - dataset_revision: str
                - batch_id: str
                - batch_index: int (optional)
                - base_checkpoint_id: str
                - aggregation_generation: int

        Returns:
            dict with:
                - 'tensors': dict of {name: np.ndarray(float32)}
                - 'loss': float (synthetic loss value)
                - 'samples_processed': int
                - 'delta_sha256': str (SHA-256 of canonical serialization)
        """
        sim_seed = key_params.get("simulation_seed", self.seed)
        run_cfg_hash = key_params.get("run_config_hash", "default_config")
        ds_revision = key_params.get("dataset_revision", "default_dataset")
        batch_id = key_params.get("batch_id", "b_default")
        base_ckpt = key_params.get("base_checkpoint_id", "ckpt-base")
        agg_gen = key_params.get("aggregation_generation", 0)
        batch_index = key_params.get("batch_index", 0)
        samples = key_params.get("samples_processed", 4)

        # Build canonical key (B2: NO worker_id/assignment_id/attempt)
        key = _canonical_key(sim_seed, run_cfg_hash, ds_revision,
                             batch_id, base_ckpt, agg_gen)

        rng_seed = deterministic_seed(key)
        rng = np.random.default_rng(rng_seed)

        # Generate tensors deterministically
        tensors = {}
        for name in self.tensor_names:
            shape = self._shapes.get(name, (self.d_model, self.lora_r))
            tensors[name] = rng.standard_normal(shape, dtype=np.float32)

        # Synthetic loss (deterministic from same RNG)
        loss = float(rng.uniform(0.1, 2.0))

        # Serialize and compute SHA-256
        serialized = self.serialize_canonical(tensors)
        delta_sha256 = hashlib.sha256(serialized).hexdigest()

        return {
            "tensors": tensors,
            "loss": loss,
            "samples_processed": samples,
            "delta_sha256": delta_sha256,
            "batch_index": batch_index,
            "batch_id": batch_id,
        }

    @staticmethod
    def serialize_canonical(tensors: dict) -> bytes:
        """Serialize tensors to bytes in canonical order.

        Same as canonical_serialize() in aggregation.py but self-contained
        for worker-side use (avoids import of aggregation.py).

        Returns:
            Bytes payload.
        """
        payload = b""
        for name in sorted(tensors.keys()):
            arr = np.asarray(tensors[name]).astype(np.float32)
            header = json.dumps(
                {
                    "name": name,
                    "dtype": str(arr.dtype),
                    "shape": list(arr.shape),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            payload += header + b"\x00" + arr.tobytes() + b"\x00"
        return payload

    @staticmethod
    def deserialize_canonical(payload: bytes) -> dict:
        """Deserialize bytes back to tensor dict.

        Uses size-based reading (not split by \\x00) to handle binary
        tensor data that may contain null bytes.
        """
        tensors = {}
        pos = 0
        while pos < len(payload):
            null_pos = payload.find(b"\x00", pos)
            if null_pos == -1:
                break
            header_bytes = payload[pos:null_pos]
            if not header_bytes:
                pos = null_pos + 1
                continue
            header = json.loads(header_bytes.decode("utf-8"))
            name = header["name"]
            dtype = np.dtype(header["dtype"])
            shape = header["shape"]
            num_elements = 1
            for dim in shape:
                num_elements *= dim
            data_size = dtype.itemsize * num_elements
            pos = null_pos + 1
            data_bytes = payload[pos:pos + data_size]
            arr = np.frombuffer(data_bytes, dtype=dtype).reshape(shape)
            tensors[name] = arr
            pos += data_size + 1
        return tensors


# ═════════════════════════════════════════════════════════════════════
# RC3.3.2 — TrainingWorker (training-aware simulated worker)
# ═════════════════════════════════════════════════════════════════════

class TrainingWorker(SimulatedWorker):
    """Training-aware worker that generates synthetic tensor deltas.

    Extends SimulatedWorker with:
    - train.request_batch integration
    - SyntheticTensorGen for deterministic tensor generation
    - SyntheticTensorStore for explicit tensor transport
    - train.report_progress for lease extension
    - train.submit_delta with upload_reference
    """

    def __init__(self, coordinator_url: str,
                 worker_type: str = "python",
                 fail_mode: str = None,
                 tensor_gen: SyntheticTensorGen = None,
                 tensor_store=None,
                 run_id: str = None,
                 worker_session_id: str = None,
                 run_config: dict = None):
        super().__init__(coordinator_url, worker_type,
                         fail_mode=fail_mode)
        self.tensor_gen = tensor_gen or SyntheticTensorGen()
        self.tensor_store = tensor_store
        self.run_id = run_id
        self._worker_session_id = worker_session_id or f"ws-{uuid.uuid4().hex[:8]}"
        self.run_config = run_config or {
            "model_id": "qwen3-0.6b",
            "dataset_id": "catalan-sft-v1",
            "model_revision": "sha256:base",
            "adapter_config_hash": "sha256:adapter",
            "dataset_revision": "sha256:dataset",
            "base_checkpoint_id": "ckpt-base-001",
            "run_config_hash": "default_config",
            "simulation_seed": 42,
        }

    @property
    def worker_session_id(self) -> str:
        return self._worker_session_id

    def do_training_batch(self, request_kwargs: dict = None) -> bool:
        """Full training batch cycle: request → generate → store → submit.

        Returns True on success, False on failure.
        """
        # 1. Request batch from coordinator
        batch_result = self.request_batch(**(request_kwargs or {}))
        if batch_result is None:
            return False

        # 2. Generate tensors deterministically
        key_params = {
            "simulation_seed": self.run_config.get("simulation_seed", 42),
            "run_config_hash": self.run_config.get("run_config_hash",
                                                    "default_config"),
            "dataset_revision": self.run_config.get("dataset_revision",
                                                     "sha256:dataset"),
            "batch_id": batch_result["batch_id"],
            "batch_index": batch_result.get("batch", {}).get("batch_index", 0),
            "base_checkpoint_id": batch_result.get("base_checkpoint_id",
                                                    "ckpt-base-001"),
            "aggregation_generation": batch_result.get(
                "aggregation_generation", 0),
            "samples_processed": batch_result.get("batch", {}).get(
                "num_samples", 4),
        }
        delta = self.tensor_gen.generate_batch_delta(key_params)

        # 3. Serialize and store
        serialized = SyntheticTensorGen.serialize_canonical(delta["tensors"])
        delta_sha256 = hashlib.sha256(serialized).hexdigest()

        upload_reference = self.tensor_store.put(
            self.run_id,
            batch_result["batch_id"],
            serialized,
            delta_sha256,
        )

        # 4. Optional: report_progress
        self.report_progress(
            batch_result["batch_id"],
            batch_result["assignment_id"],
            batch_result["lease_token"],
            progress_pct=50,
        )

        # 5. Submit delta with upload_reference and manifest
        manifest = {
            "tensor_names": list(delta["tensors"].keys()),
            "lora_r": self.tensor_gen.lora_r,
            "lora_alpha": 16,
            "dtype": "float32",
            "shapes": {k: list(v.shape)
                       for k, v in delta["tensors"].items()},
            "delta_sha256": delta_sha256,
            "delta_size_bytes": len(serialized),
            "serialization_version": "v1",
            "compression": None,
        }

        submit_params = {
            "run_id": self.run_id,
            "batch_id": batch_result["batch_id"],
            "lease_token": batch_result["lease_token"],
            "worker_session_id": self._worker_session_id,
            "assignment_id": batch_result["assignment_id"],
            "aggregation_generation": batch_result.get(
                "aggregation_generation", 0),
            "model_revision": self.run_config.get("model_revision",
                                                   "sha256:base"),
            "adapter_config_hash": self.run_config.get("adapter_config_hash",
                                                        "sha256:adapter"),
            "base_checkpoint_id": self.run_config.get("base_checkpoint_id",
                                                       "ckpt-base-001"),
            "dataset_revision": self.run_config.get("dataset_revision",
                                                     "sha256:dataset"),
            "delta_sha256": delta_sha256,
            "samples_processed": delta["samples_processed"],
            "loss": delta["loss"],
            "upload_reference": upload_reference,
            "delta_manifest": manifest,
        }

        submit_result = self.submit_delta(submit_params)
        if submit_result:
            log.info(f"Batch {batch_result['batch_id']}: accepted ✓")
            return True
        else:
            log.warning(f"Batch {batch_result['batch_id']}: rejected")
            return False

    def request_batch(self, **kwargs) -> dict:
        """Call train.request_batch on coordinator."""
        session_id = kwargs.get("worker_session_id", self._worker_session_id)
        run_id = kwargs.get("run_id", self.run_id)
        caps = kwargs.get("capabilities", {})

        params = {
            "run_id": run_id,
            "worker_session_id": session_id,
        }
        if caps:
            params["capabilities"] = caps

        result = self._rpc("train.request_batch", params)
        if "error" in result:
            log.error(f"train.request_batch failed: {result['error']}")
            return None
        return result.get("result")

    def report_progress(self, batch_id: str, assignment_id: str,
                        lease_token: str, progress_pct: int = 50) -> dict:
        """Call train.report_progress on coordinator."""
        result = self._rpc("train.report_progress", {
            "run_id": self.run_id,
            "batch_id": batch_id,
            "assignment_id": assignment_id,
            "lease_token": lease_token,
            "worker_session_id": self._worker_session_id,
            "progress_pct": progress_pct,
        })
        return result.get("result", {})

    def submit_delta(self, params: dict) -> bool:
        """Call train.submit_delta on coordinator."""
        result = self._rpc("train.submit_delta", params)
        if "error" in result:
            log.error(f"submit_delta failed: {result['error']}")
            return False
        res = result.get("result", {})
        return res.get("status") == "COMPLETED"

    def run_training_loop(self, max_batches: int = 10) -> int:
        """Run multiple training batch cycles.

        Returns:
            Number of successfully completed batches.
        """
        completed = 0
        for _ in range(max_batches):
            self.heartbeat(status="training")
            if self.do_training_batch():
                completed += 1
            else:
                # No more batches or error
                break
        log.info(f"Training complete: {completed}/{max_batches} batches")
        return completed


if __name__ == "__main__":
    main()
