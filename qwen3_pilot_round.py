"""QWEN PILOT ROUND — dos workers reals + FedAvg (RC5.3, sense modificar)
+ oracle monolític independent + Round 2.

MEMÒRIA: només UN model carregat a la vegada (el CT112 té 8GB; dos models
simultanis -> OOM). Cada worker s'executa com a instància seqüencial:
carrega -> entrena el seu shard -> guarda el delta a disc (.npz) -> allibera.
El FedAvg i l'oracle treballen amb els deltes de disc.

Workers:
  A: shard A (exemples train[0:10])   B: shard B (exemples train[10:20])
Cada worker carrega el MATEIX Qwen base i el MATEIX LoRA adapter_0 (mateixa
seed, hashes verificats) i produeix delta + ETT (derivat dels labels).

FedAvg: aggregation.FedAvgLoRA (importat, NO modificat) ponderat per
samples_processed = ETT. Oracle: càlcul independent (fedavg_qwen).

Ús: python3 qwen3_pilot_round.py
"""
import gc
import hashlib
import json
import os
import sys
import time

MODEL = os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")
DATA_DIR = os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")
OUT_DIR = os.environ.get("QWEN3_OUT", "/root/meshtrainer/qwen3_pilot_output")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import torch
from qwen3_training_backend import Qwen3TrainingBackend, fedavg_qwen
from qwen3_pilot_dataset import Qwen3PilotDataset
from aggregation import get_strategy

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def save_delta(path: str, delta: dict):
    np.savez(path, **{k: np.asarray(v, dtype=np.float32) for k, v in delta.items()})


def load_delta(path: str) -> dict:
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def train_worker(tag: str, shard: list, adapter_bytes: bytes | None,
                 base_expected_hash: str | None):
    """Carrega 1 model, opcionalment carrega adapter, entrena el shard,
    guarda delta+ETT a disc i retorna les mètriques. ALLIBERA el model."""
    t0 = time.time()
    bk = Qwen3TrainingBackend(MODEL, db_path=os.path.join(OUT_DIR, f"{tag}.db"),
                              seq_len=128)
    bk.load()
    print(f"worker {tag} carregat en {time.time()-t0:.0f}s", flush=True)
    if adapter_bytes is not None:
        bk.load_adapter_from_bundle(adapter_bytes)
    base_hash = bk.hash_adapter()  # hash complet (LoRA inclòs) per al check
    ett = 0
    for i, ex in enumerate(shard):
        _, _ = bk.train_step(f"{tag}-{i}", ex["instruction"], ex["response"])
        ett += bk.compute_ett(bk.tokenize_chat(ex["instruction"], ex["response"])[2])
    delta = {}
    for i in range(len(shard)):
        dt = bk.delta_tensors(f"{tag}-{i}")
        if not delta:
            delta = {k: np.asarray(dt[k], dtype=np.float64) for k in dt}
        else:
            for k in dt:
                delta[k] += np.asarray(dt[k], dtype=np.float64)
    delta = {k: v.astype(np.float32) for k, v in delta.items()}
    save_delta(os.path.join(OUT_DIR, f"delta_{tag}.npz"), delta)
    with open(os.path.join(OUT_DIR, f"ett_{tag}.json"), "w") as f:
        json.dump({"ett": ett}, f)
    l2 = float(np.sqrt(sum(float((v.astype("float64") ** 2).sum()) for v in delta.values())))
    del bk
    gc.collect()
    return {"base_hash": base_hash, "ett": ett, "l2": l2, "pre_hash": None}


def main():
    # NETEJA SELECTIVA: només les BDs/artefactes propis del round. NO es fa
    # rmtree(OUT_DIR): el directori és compartit amb el train (pas 5).
    for _f in os.listdir(OUT_DIR):
        if _f.startswith(("worker_a", "worker_b", "delta_", "ett_")):
            os.remove(os.path.join(OUT_DIR, _f))
    ds = Qwen3PilotDataset(DATA_DIR)
    train = ds.train()
    shard_a, shard_b = train[0:10], train[10:20]
    print(f"shard A: {len(shard_a)} exemples | shard B: {len(shard_b)}")

    # ── worker A (guarda adapter_0 + delta_A) ──
    wa = Qwen3TrainingBackend(MODEL, db_path=os.path.join(OUT_DIR, "worker_a.db"),
                              seq_len=128)
    wa.load()
    adapter_0_bytes = wa.adapter_to_bundle()
    with open(os.path.join(OUT_DIR, "round_adapter_0.bundle"), "wb") as f:
        f.write(adapter_0_bytes)
    hash_a0 = wa.hash_adapter()
    ett_a = 0
    for i, ex in enumerate(shard_a):
        _, _ = wa.train_step(f"r1-a-{i}", ex["instruction"], ex["response"])
        ett_a += wa.compute_ett(wa.tokenize_chat(ex["instruction"], ex["response"])[2])
    delta_a = {}
    for i in range(len(shard_a)):
        dt = wa.delta_tensors(f"r1-a-{i}")
        if not delta_a:
            delta_a = {k: np.asarray(dt[k], dtype=np.float64) for k in dt}
        else:
            for k in dt:
                delta_a[k] += np.asarray(dt[k], dtype=np.float64)
    delta_a = {k: v.astype(np.float32) for k, v in delta_a.items()}
    save_delta(os.path.join(OUT_DIR, "delta_A.npz"), delta_a)
    l2_a = float(np.sqrt(sum(float((v.astype("float64") ** 2).sum()) for v in delta_a.values())))
    del wa
    gc.collect()

    # ── worker B (mateix base + mateix adapter_0) ──
    wb = Qwen3TrainingBackend(MODEL, db_path=os.path.join(OUT_DIR, "worker_b.db"),
                              seq_len=128)
    wb.load()
    hash_b0 = wb.hash_adapter()
    check("mateix Qwen base (adapter_0 hash A == B)", hash_a0 == hash_b0,
          f"{hash_a0[:12]} vs {hash_b0[:12]}")
    ett_b = 0
    for i, ex in enumerate(shard_b):
        _, _ = wb.train_step(f"r1-b-{i}", ex["instruction"], ex["response"])
        ett_b += wb.compute_ett(wb.tokenize_chat(ex["instruction"], ex["response"])[2])
    delta_b = {}
    for i in range(len(shard_b)):
        dt = wb.delta_tensors(f"r1-b-{i}")
        if not delta_b:
            delta_b = {k: np.asarray(dt[k], dtype=np.float64) for k in dt}
        else:
            for k in dt:
                delta_b[k] += np.asarray(dt[k], dtype=np.float64)
    delta_b = {k: v.astype(np.float32) for k, v in delta_b.items()}
    save_delta(os.path.join(OUT_DIR, "delta_B.npz"), delta_b)
    del wb
    gc.collect()

    check("ETT_A > 0 i ETT_B > 0", ett_a > 0 and ett_b > 0, f"ETT_A={ett_a} ETT_B={ett_b}")
    check("ETT_A != ETT_B (preferible)", ett_a != ett_b, f"{ett_a} vs {ett_b}")
    check("delta_A != 0", l2_a > 0, f"||delta_A||={l2_a:.6f}")

    # ── FedAvg real (aggregation.FedAvgLoRA, sense modificar) ──
    fed = get_strategy("fedavg_lora")
    agg = fed.aggregate([
        {"batch_id": "a", "batch_index": 0, "tensors": delta_a,
         "samples_processed": ett_a},
        {"batch_id": "b", "batch_index": 1, "tensors": delta_b,
         "samples_processed": ett_b},
    ], {"aggregation_generation": 1})
    wdelta = agg["aggregated_weights"]
    check("FedAvg retorna tensors ponderats", len(wdelta) > 0,
          f"{agg['total_samples']} samples")

    # ── oracle monolític independent ──
    adapter_0 = {n: np.asarray(t, dtype=np.float32) for n, t in
                 Qwen3TrainingBackend._deserialize_delta(adapter_0_bytes).items()}
    oracle = fedavg_qwen(adapter_0, [(delta_a, ett_a), (delta_b, ett_b)])
    fed_adapter_1 = {n: (adapter_0[n].astype("float64") + wdelta[n].astype("float64")).astype("float32")
                     for n in oracle}
    maxdiff = max(float(np.abs(fed_adapter_1[n].astype("float64") - oracle[n]).max())
                  for n in oracle)
    check("oracle == FedAvg (tensor per tensor, tolerància estricta)",
          maxdiff <= 1e-6, f"maxdiff={maxdiff:.2e}")
    w1_bytes = Qwen3TrainingBackend._serialize_delta(
        {n: torch.as_tensor(fed_adapter_1[n]) for n in fed_adapter_1})
    with open(os.path.join(OUT_DIR, "round_adapter_1.bundle"), "wb") as f:
        f.write(w1_bytes)

    # ── round 2 (1 model a la vegada, adapter_1 distribuit) ──
    def round2_worker(tag: str, shard: list):
        bk = Qwen3TrainingBackend(MODEL, db_path=os.path.join(OUT_DIR, f"r2_{tag}.db"),
                                  seq_len=128)
        bk.load()
        bk.load_adapter_from_bundle(w1_bytes)
        ett = 0
        for i, ex in enumerate(shard):
            _, _ = bk.train_step(f"r2-{tag}-{i}", ex["instruction"], ex["response"])
            ett += bk.compute_ett(bk.tokenize_chat(ex["instruction"], ex["response"])[2])
        delta = {}
        for i in range(len(shard)):
            dt = bk.delta_tensors(f"r2-{tag}-{i}")
            if not delta:
                delta = {k: np.asarray(dt[k], dtype=np.float64) for k in dt}
            else:
                for k in dt:
                    delta[k] += np.asarray(dt[k], dtype=np.float64)
        delta = {k: v.astype(np.float32) for k, v in delta.items()}
        save_delta(os.path.join(OUT_DIR, f"delta_2{tag}.npz"), delta)
        del bk
        gc.collect()
        return ett

    ett_a2 = round2_worker("A", shard_a[:5])
    ett_b2 = round2_worker("B", shard_b[:5])
    d2a = load_delta(os.path.join(OUT_DIR, "delta_2A.npz"))
    d2b = load_delta(os.path.join(OUT_DIR, "delta_2B.npz"))
    agg2 = fed.aggregate([
        {"batch_id": "a2", "batch_index": 0, "tensors": d2a,
         "samples_processed": ett_a2},
        {"batch_id": "b2", "batch_index": 1, "tensors": d2b,
         "samples_processed": ett_b2},
    ], {"aggregation_generation": 2})
    wdelta2 = agg2["aggregated_weights"]
    adapter_2 = {n: (fed_adapter_1[n].astype("float64") + wdelta2[n].astype("float64")).astype("float32")
                 for n in fed_adapter_1}
    oracle2 = fedavg_qwen(fed_adapter_1, [(d2a, ett_a2), (d2b, ett_b2)])
    maxdiff2 = max(float(np.abs(adapter_2[n].astype("float64") - oracle2[n]).max())
                   for n in adapter_2)
    check("round 2: adapter_2 = adapter_1 + weighted_delta_round2",
          maxdiff2 <= 1e-6, f"maxdiff={maxdiff2:.2e}")
    w2_bytes = Qwen3TrainingBackend._serialize_delta(
        {n: torch.as_tensor(adapter_2[n]) for n in adapter_2})
    with open(os.path.join(OUT_DIR, "round_adapter_2.bundle"), "wb") as f:
        f.write(w2_bytes)

    # ── sortida ──
    out = {
        "ett_a": ett_a, "ett_b": ett_b,
        "adapter_0_sha": hashlib.sha256(adapter_0_bytes).hexdigest(),
        "adapter_1_sha": hashlib.sha256(w1_bytes).hexdigest(),
        "adapter_2_sha": hashlib.sha256(w2_bytes).hexdigest(),
        "maxdiff_oracle_r1": maxdiff,
        "maxdiff_oracle_r2": maxdiff2,
    }
    with open(os.path.join(OUT_DIR, "round_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, indent=2, ensure_ascii=False))

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== RESUM ROUND: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
