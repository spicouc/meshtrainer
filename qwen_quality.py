#!/usr/bin/env python3
"""qwen_quality.py — qualitat BEFORE/AFTER (secció 13 de l'ordre).

- validation loss + perplexity (mai només train loss)
- 10 prompts holdout: prompt, resposta generada, resposta esperada, puntuació
- puntuació determinista 0/1/2 amb criteris explícits (paraules clau de la
  resposta esperada): 2 = >=2 claus, 1 = >=1 clau, 0 = cap.

Ús: qwen_quality.py --model M --adapter PATH --adapter-sha SHA \
        --data-dir D --out metrics.json
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

MODEL = os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qwen3_training_backend import Qwen3TrainingBackend
from qwen3_pilot_dataset import Qwen3PilotDataset

# Criteris explícits de puntuació per als 10 holdouts (paraules clau de la
# resposta esperada al dataset pilot).
KEYWORDS = {
    "Què és la hipertensió arterial?":
        ["pressió", "artèries", "elevada", "sang"],
    "Què és un ecosistema?":
        ["organismes", "medi", "interaccions", "comunitat"],
    "Qui va ser Leonardo da Vinci?":
        ["Renaixement", "Gioconda", "artista", "científic"],
    "Quin és el continent més poblat?":
        ["Àsia", "milions", "població", "habitants"],
    "Què és una antònim?":
        ["oposat", "contrari", "significat"],
    "Què és la derivada?":
        ["variació", "instantània", "funció", "càlcul"],
    "Què és la biosfera?":
        ["vida", "capa", "Terra", "organismes"],
    "Què és un sistema operatiu?":
        ["programari", "recursos", "maquinari", "Linux"],
    "Què és la UNESCO?":
        ["educació", "ciència", "cultura", "Nacions Unides"],
    "Què és l'arquitectura gòtica?":
        ["apuntades", "vitralls", "creueria", "catedrals"],
}


def score_response(prompt: str, response: str) -> tuple[int, list[str]]:
    kws = KEYWORDS.get(prompt, [])
    found = [k for k in kws if k.lower() in response.lower()]
    score = 2 if len(found) >= 2 else (1 if len(found) >= 1 else 0)
    return score, found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--adapter-sha", default=None)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seq-len", type=int, default=128)
    args = ap.parse_args()

    t0 = time.time()
    bk = Qwen3TrainingBackend(args.model, db_path="/tmp/qwen_quality.db",
                              seq_len=args.seq_len)
    bk.load()
    with open(args.adapter, "rb") as f:
        ad = f.read()
    bk.load_adapter_from_bundle(ad, args.adapter_sha or hashlib.sha256(ad).hexdigest(),
                                strict=True)

    ds = Qwen3PilotDataset(args.data_dir)
    val = ds.validation()
    losses = [bk.eval_loss(v["instruction"], v["response"]) for v in val]
    val_loss = sum(losses) / len(losses)
    ppl = math.exp(val_loss)

    holdouts = []
    total = 0
    for ex in ds.test():
        resp = bk.generate(ex["instruction"], max_new_tokens=60)
        score, found = score_response(ex["instruction"], resp)
        total += score
        holdouts.append({"prompt": ex["instruction"], "response": resp,
                         "expected": ex["response"], "score": score,
                         "keywords_found": found})
    out = {
        "adapter_sha": args.adapter_sha or hashlib.sha256(ad).hexdigest(),
        "validation_loss": val_loss,
        "validation_perplexity": ppl,
        "holdout_prompts": len(holdouts),
        "score_total": total,
        "score_max": 2 * len(holdouts),
        "holdouts": holdouts,
        "temps_s": round(time.time() - t0, 1),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"val_loss={val_loss:.4f} ppl={ppl:.4f} score={total}/{2*len(holdouts)} "
          f"({time.time()-t0:.0f}s)")
    for h in holdouts:
        print(f"  [{h['score']}] {h['prompt'][:40]} -> {h['response'][:60]!r}")
    print("QUALITY OK")


if __name__ == "__main__":
    main()
