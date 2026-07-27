"""
aggregation.py — RC3.3 Aggregation interface and FedAvgLoRA implementation.

AggregationStrategy (interface):
  ├── FedAvgLoRA (RC3.3) — weighted averaging of full LoRA parameters
  └── reserved for future strategies

Strategy interface:
  - get_strategy_name() -> str
  - validate_batch_delta(delta_manifest, run_config) -> bool
  - aggregate_batches(batch_results, total_samples) -> dict

RC3.3.2 extension: FedAvgLoRA.aggregate() now handles real tensor data
(np.ndarray dicts) in addition to stub hash-based results. When batch_results
contain 'tensors' (dict of np.ndarray), real weighted averaging is performed
in float64, with final result in float32. Canonical SHA-256 computed via
compute_checkpoint_hash().
"""

import hashlib
import json
import threading
from typing import Dict, List, Optional, Any, Protocol

import numpy as np


class AggregationStrategy(Protocol):
    """Interface for aggregation strategies."""

    def get_strategy_name(self) -> str:
        ...

    def validate_delta_manifest(self, manifest: dict,
                                run_config: dict) -> Dict:
        """Validate a delta manifest against run configuration.

        Returns {'valid': bool, 'reason': str}
        """
        ...

    def aggregate(self, batch_results: List[Dict],
                  run_config: Dict) -> Dict:
        """Aggregate batch results into a single checkpoint.

        Args:
            batch_results: list of dicts with 'samples_processed' and
                          'weights' (placeholder values for RC3.3.1)
            run_config: run configuration dict

        Returns:
            {'aggregated_weights': dict, 'total_samples': int,
             'aggregation_generation': int, 'checkpoint_metadata': dict}
        """
        ...


def deterministic_seed(key: str) -> int:
    """SHA-256 based deterministic seed (cross-process stable, NOT hash())."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def canonical_serialize(tensors: Dict[str, np.ndarray]) -> bytes:
    """Serialize tensors to bytes in canonical order for stable hashing.

    Pipeline:
      1. Sort tensor names alphabetically
      2. Convert each to float32
      3. JSON header: {name, dtype, shape}
      4. Append raw bytes
      5. Null-byte separator between entries

    Args:
        tensors: Dict {name: np.ndarray}.

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


def compute_checkpoint_hash(
    batch_results: List[Dict],
    total_samples: int,
) -> str:
    """Compute canonical SHA-256 over aggregated FedAvg result.

    Canonical SHA-256 pipeline (B4):
      1. Sort batches by (batch_index, batch_id)
      2. Sort tensor names alphabetically
      3. Convert operands to float64
      4. Accumulate in float64
      5. Divide by total samples
      6. Convert final to float32
      7. Serialize dtype+shape+bytes in canonical order
      8. SHA-256 over serialization

    Falls back to stub hash if no real tensors present.

    Args:
        batch_results: List of batch result dicts.
        total_samples: Total samples across all batches.

    Returns:
        Hex SHA-256 digest.
    """
    # Check if we have real tensor data
    has_tensors = any(
        "tensors" in r and isinstance(r["tensors"], dict)
        for r in batch_results
    )

    if not has_tensors:
        # Stub hash (backward compat with RC3.3.1)
        canonical = json.dumps(
            sorted([
                (r.get("batch_id", ""), r.get("delta_hash", ""),
                 r.get("samples_processed", 0))
                for r in batch_results
            ]),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    if total_samples == 0:
        return hashlib.sha256(b"empty_aggregation").hexdigest()

    # Sort batches by (batch_index, batch_id) - canonical order
    sorted_batches = sorted(
        batch_results,
        key=lambda r: (r.get("batch_index", 9999), r.get("batch_id", "")),
    )

    # Get sorted tensor names
    all_names = set()
    for r in sorted_batches:
        tensors = r.get("tensors", {})
        all_names.update(tensors.keys())
    sorted_names = sorted(all_names)

    # Real aggregation in float64
    accumulated = {}
    for name in sorted_names:
        accumulated[name] = None

    for r in sorted_batches:
        tensors = r.get("tensors", {})
        samples = r.get("samples_processed", 0)
        for name in sorted_names:
            t = tensors.get(name)
            if t is None:
                continue
            t_f64 = np.asarray(t).astype(np.float64) * samples
            if accumulated[name] is None:
                accumulated[name] = t_f64
            else:
                accumulated[name] += t_f64

    # Divide by total samples, convert to float32
    aggregated = {}
    for name in sorted_names:
        if accumulated[name] is not None:
            aggregated[name] = (accumulated[name] / total_samples).astype(
                np.float32
            )
        else:
            aggregated[name] = np.zeros(1, dtype=np.float32)

    # Canonical serialization for hash
    return compute_checkpoint_hash_aggregated(aggregated)


def compute_checkpoint_hash_aggregated(
    aggregated: Dict[str, np.ndarray],
) -> str:
    """SHA-256 over already-aggregated tensors.

    Used by reference implementation comparison and for final checkpoint hash.

    Args:
        aggregated: Dict {name: np.ndarray} of aggregated tensors (float32).

    Returns:
        Hex SHA-256 digest.
    """
    serialized = canonical_serialize(aggregated)
    return hashlib.sha256(serialized).hexdigest()


# ── FedAvgLoRA — RC3.3 default strategy ────────────────────────────────

class FedAvgLoRA:
    """Weighted averaging of LoRA parameters across workers.

    Formula: theta_global = sum(samples_k * theta_k) / sum(samples_k)

    RC3.3.1: stub mode (hash-based placeholders).
    RC3.3.2: real tensor aggregation when batch_results contain 'tensors'.
    """

    def __init__(self):
        self._name = "fedavg_lora"
        self._lock = threading.Lock()

    def get_strategy_name(self) -> str:
        return self._name

    def validate_delta_manifest(self, manifest: dict,
                                run_config: dict) -> Dict:
        """Validate delta manifest fields."""
        errors = []

        # Check required manifest fields
        required = ["tensor_names", "lora_r", "lora_alpha", "dtype"]
        for field in required:
            if field not in manifest:
                errors.append(f"missing_manifest_field_{field}")

        if errors:
            return {"valid": False, "reason": "; ".join(errors)}

        # Validate LoRA config matches run
        if manifest.get("lora_r") != run_config.get("lora_r"):
            return {"valid": False,
                    "reason": f"lora_r mismatch: manifest={manifest.get('lora_r')}, "
                              f"config={run_config.get('lora_r')}"}

        if manifest.get("lora_alpha") != run_config.get("lora_alpha"):
            return {"valid": False,
                    "reason": f"lora_alpha mismatch: manifest={manifest.get('lora_alpha')}, "
                              f"config={run_config.get('lora_alpha')}"}

        # Validate tensor shapes if provided
        shapes = manifest.get("shapes", {})
        for tname in manifest.get("tensor_names", []):
            if tname not in shapes:
                errors.append(f"tensor_{tname}_missing_shape")

        if errors:
            return {"valid": False, "reason": "; ".join(errors)}

        return {"valid": True, "reason": "ok"}

    def aggregate(self, batch_results: List[Dict],
                  run_config: Dict) -> Dict:
        """Aggregate batch results via weighted averaging.

        Two modes:
        - Stub mode (RC3.3.1 backward compat): batch_results contains
          dicts with 'delta_hash', 'samples_processed'. Returns hash-based
          aggregated_weights with is_stub=True.
        - Real mode (RC3.3.2): batch_results contains dicts with 'tensors'
          (dict of np.ndarray). Performs weighted FedAvg in float64, returns
          actual aggregated tensors in float32 with canonical SHA-256.
        """
        if not batch_results:
            return {
                "aggregated_weights": {},
                "total_samples": 0,
                "aggregation_generation": run_config.get("aggregation_generation", 0),
                "checkpoint_metadata": {"strategy": self._name,
                                        "num_batches": 0,
                                        "is_stub": True},
            }

        total_samples = sum(
            r.get("samples_processed", 0) for r in batch_results
        )

        # Detect real tensor mode
        has_tensors = any(
            "tensors" in r and isinstance(r["tensors"], dict)
            for r in batch_results
        )

        if has_tensors:
            return self._aggregate_real(batch_results, total_samples, run_config)

        # ── Stub mode (RC3.3.1 backward compat) ──
        canonical = json.dumps(
            sorted([(r.get("batch_id", ""), r.get("delta_hash", ""),
                     r.get("samples_processed", 0))
                    for r in batch_results]),
            sort_keys=True,
        )
        aggregate_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        return {
            "aggregated_weights": {
                "aggregate_hash": aggregate_hash,
                "batch_count": len(batch_results),
                "is_stub": True,
            },
            "total_samples": total_samples,
            "aggregation_generation": run_config.get("aggregation_generation", 0),
            "checkpoint_metadata": {
                "strategy": self._name,
                "num_batches": len(batch_results),
                "total_samples": total_samples,
                "is_stub": True,
                "aggregate_hash": aggregate_hash,
            },
        }

    def _aggregate_real(self, batch_results: List[Dict],
                        total_samples: int,
                        run_config: Dict) -> Dict:
        """Real FedAvg aggregation with actual tensor data.

        Args:
            batch_results: List of dicts, each containing:
                - 'tensors': Dict[str, np.ndarray]
                - 'samples_processed': int
                - 'batch_id': str
                - 'batch_index': int (optional)
            total_samples: Sum of samples_processed.
            run_config: Run configuration dict.

        Returns:
            Dict with aggregated tensors and metadata.
        """
        if total_samples == 0:
            return {
                "aggregated_weights": {},
                "total_samples": 0,
                "aggregation_generation": run_config.get("aggregation_generation", 0),
                "checkpoint_metadata": {"strategy": self._name,
                                        "num_batches": len(batch_results),
                                        "is_stub": False,
                                        "error": "zero_total_samples"},
            }

        # Sort batches by (batch_index, batch_id) for deterministic order
        sorted_batches = sorted(
            batch_results,
            key=lambda r: (r.get("batch_index", 9999), r.get("batch_id", "")),
        )

        # Collect all tensor names
        all_names = set()
        for r in sorted_batches:
            all_names.update(r.get("tensors", {}).keys())
        sorted_names = sorted(all_names)

        # Real aggregation in float64
        accumulated = {}
        for name in sorted_names:
            accumulated[name] = None

        for r in sorted_batches:
            tensors = r.get("tensors", {})
            samples = r.get("samples_processed", 0)
            for name in sorted_names:
                t = tensors.get(name)
                if t is None:
                    continue
                t_f64 = np.asarray(t).astype(np.float64) * samples
                if accumulated[name] is None:
                    accumulated[name] = t_f64
                else:
                    accumulated[name] += t_f64

        # Finalize: divide by total, convert to float32
        aggregated = {}
        for name in sorted_names:
            if accumulated[name] is not None:
                aggregated[name] = (
                    accumulated[name] / total_samples
                ).astype(np.float32)

        # Compute canonical checkpoint hash
        ckpt_hash = compute_checkpoint_hash_aggregated(aggregated)

        return {
            "aggregated_weights": aggregated,
            "total_samples": total_samples,
            "aggregation_generation": run_config.get("aggregation_generation", 0),
            "checkpoint_hash": ckpt_hash,
            "checkpoint_metadata": {
                "strategy": self._name,
                "num_batches": len(batch_results),
                "total_samples": total_samples,
                "num_tensors": len(sorted_names),
                "tensor_names": sorted_names,
                "is_stub": False,
                "aggregate_hash": ckpt_hash,
            },
        }


# ── Factory ────────────────────────────────────────────────────────────

STRATEGIES = {
    "fedavg_lora": FedAvgLoRA,
}


def get_strategy(name: str = "fedavg_lora") -> AggregationStrategy:
    """Factory: get aggregation strategy by name."""
    cls = STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"Unknown aggregation strategy: {name}. "
                         f"Available: {list(STRATEGIES.keys())}")
    return cls()
