"""QWEN PILOT — entrenament real (single worker) sobre el dataset pilot.

Demostra: Qwen3-0.6B -> LoRA -> dataset -> forward -> loss -> backward ->
optimizer -> delta real. Guarda mètriques, adapters pre/post i les respostes
dels prompts holdout ABANS i DESPRÉS de l'entrenament (sense cherry-picking:
tot el conjunt test es guarda).

Ús: python3 qwen3_pilot_train.py [--max-steps N] [--epochs E] [--seq-len L]
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

MODEL = os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")
DATA_DIR = os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")
OUT_DIR = os.environ.get("QWEN3_OUT", "/root/meshtrainer/qwen3_pilot_output")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qwen3_training_backend import Qwen3TrainingBackend
from qwen3_pilot_dataset import Qwen3PilotDataset


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-steps", type=int, default=20)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--seq-len", type=int, default=256)
    ap.add_argument("--exemples", type=int, default=20,
                    help="exemples d'entrenament usats (subset pilot a CPU)")
    args = ap.parse_args()
    # output fresh per execució (adapters, journal, mètriques, respostes)
    import shutil
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    ds = Qwen3PilotDataset(DATA_DIR)
    train = ds.train()[:args.exemples]
    val = ds.validation()
    holdout = ds.test()

    t0 = time.time()
    bk = Qwen3TrainingBackend(MODEL, db_path=os.path.join(OUT_DIR, "train_journal.db"),
                              seq_len=args.seq_len)
    bk.load()
    print(f"model carregat en {time.time()-t0:.0f}s", flush=True)

    # adapter_0 (estat inicial) + respostes ABANS
    adapter_0 = bk.adapter_to_bundle()
    with open(os.path.join(OUT_DIR, "adapter_0.bundle"), "wb") as f:
        f.write(adapter_0)
    before = {}
    for ex in holdout:
        before[ex["instruction"]] = bk.generate(ex["instruction"], max_new_tokens=40)
    with open(os.path.join(OUT_DIR, "responses_before.json"), "w", encoding="utf-8") as f:
        json.dump(before, f, ensure_ascii=False, indent=2)

    # entrenament (1 epoch sobre el subset)
    losses = []
    etts = []
    t1 = time.time()
    for i, ex in enumerate(train):
        _, _ = bk.train_step(f"train-{i}", ex["instruction"], ex["response"])
        losses.append(bk._last_loss)
        etts.append(bk.compute_ett(bk.tokenize_chat(ex["instruction"], ex["response"])[2]))
        if (i + 1) % 5 == 0:
            print(f"  step {i+1}/{len(train)} loss={bk._last_loss:.4f}", flush=True)
    train_time = time.time() - t1

    # adapter_post + respostes DESPRÉS
    adapter_1 = bk.adapter_to_bundle()
    with open(os.path.join(OUT_DIR, "adapter_1.bundle"), "wb") as f:
        f.write(adapter_1)
    after = {}
    for ex in holdout:
        after[ex["instruction"]] = bk.generate(ex["instruction"], max_new_tokens=40)
    with open(os.path.join(OUT_DIR, "responses_after.json"), "w", encoding="utf-8") as f:
        json.dump(after, f, ensure_ascii=False, indent=2)

    # mètriques
    train_loss = sum(losses) / len(losses) if losses else float("nan")
    val_loss = sum(bk.eval_loss(v["instruction"], v["response"]) for v in val) / len(val)
    total_ett = sum(etts)
    metrics = {
        "model": MODEL,
        "exemples_train": len(train),
        "epochs": args.epochs,
        "seq_len": args.seq_len,
        "steps": len(losses),
        "loss_inicial": losses[0] if losses else None,
        "loss_final": losses[-1] if losses else None,
        "loss_mitjana_train": train_loss,
        "loss_validation": val_loss,
        "perplexity_validation": float(math.exp(val_loss)),
        "ett_total": total_ett,
        "ett_per_step": (total_ett / len(etts)) if etts else 0,
        "delta_norm": bk.delta_norm(),
        "adapter_pre_hash": bk.hash_adapter_pre(),
        "adapter_post_hash": bk.hash_adapter(),
        "temps_carrega_s": round(time.time() - t0, 1),
        "temps_entrenament_s": round(train_time, 1),
        "adapter_0_sha": hashlib.sha256(adapter_0).hexdigest(),
        "adapter_1_sha": hashlib.sha256(adapter_1).hexdigest(),
        "adapter_size_bytes": len(adapter_1),
    }
    with open(os.path.join(OUT_DIR, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    print("TRAIN OK")


if __name__ == "__main__":
    main()
