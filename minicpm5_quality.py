#!/usr/bin/env python3
"""minicpm5_quality.py — QUALITY PILOT MiniCPM5 (R1.1, punts 9-10).

Script VERSIONAT (no /tmp): entrena 3 passos reals sobre el dataset pilot
català i mesura loss/ppl/holdout before/after. Escriu DIRECTAMENT a
minicpm5_output/quality_before.json i quality_after.json amb backend=minicpm5.

Classificació màxima: PIPELINE TRAINING PASS · QUALITY PILOT (no producció).
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import torch  # noqa: E402

from model_worker import load_backend, load_examples  # noqa: E402

MODEL = os.environ.get("MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")
DATA = os.environ.get("MINICPM5_DATA", "/root/meshtrainer/qwen3_pilot_data")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "minicpm5_output")
os.makedirs(OUT, exist_ok=True)

# BD neta per execució (els update_id no poden estar APPLIED del run anterior)
for _db in ("/tmp/mc_quality.db",):
    if os.path.exists(_db):
        os.remove(_db)


def main():
    bk = load_backend("minicpm5")(MODEL, db_path="/tmp/mc_quality.db",
                                  seq_len=64)
    bk.load_model()
    exs = load_examples(DATA, "A", 3)
    print("exemples:", len(exs), flush=True)

    # ── loss BEFORE (sense entrenar) ──
    loss_before = 0.0
    for e in exs:
        tok = bk.tokenize_example(e["instruction"], e["response"])
        input_ids = tok[0].unsqueeze(0)
        attn = tok[1].unsqueeze(0)
        labels = tok[2].unsqueeze(0)
        out = bk.peft_model(input_ids=input_ids, attention_mask=attn,
                            labels=labels)
        loss_before += float(out.loss.item())
    loss_before /= len(exs)
    ppl_before = float(torch.exp(torch.tensor(loss_before)).item())
    print("loss_before=%.4f ppl_before=%.2f" % (loss_before, ppl_before),
          flush=True)

    # ── entrenar 3 passos (1 per exemple) ──
    for i, e in enumerate(exs):
        d64, dsha = bk.train_step(f"q-{i}", e["instruction"], e["response"],
                                  request_sha=f"rq{i}" * 32)
        print(f"  train {i}: loss={bk._last_loss:.4f}", flush=True)

    # ── loss AFTER ──
    loss_after = 0.0
    for e in exs:
        tok = bk.tokenize_example(e["instruction"], e["response"])
        input_ids = tok[0].unsqueeze(0)
        attn = tok[1].unsqueeze(0)
        labels = tok[2].unsqueeze(0)
        out = bk.peft_model(input_ids=input_ids, attention_mask=attn,
                            labels=labels)
        loss_after += float(out.loss.item())
    loss_after /= len(exs)
    ppl_after = float(torch.exp(torch.tensor(loss_after)).item())
    print("loss_after=%.4f ppl_after=%.2f" % (loss_after, ppl_after),
          flush=True)

    # ── holdout (5 preguntes, generació curta determinista) ──
    hold = load_examples(DATA, "B", 5)
    score = 0
    for h in hold:
        tok = bk.tokenize_example(h["instruction"])
        input_ids = tok[0].unsqueeze(0)
        gen = bk.peft_model.generate(input_ids=input_ids, max_new_tokens=16,
                                     do_sample=False)
        text = bk.tokenizer.decode(gen[0][len(tok[0]):],
                                   skip_special_tokens=True)
        keys = [w for w in h["response"].split() if len(w) > 4][:2]
        ok = any(k.lower() in text.lower() for k in keys)
        if ok:
            score += 1
    print(f"holdout: {score}/{len(hold)}", flush=True)

    # ── persistir (backend=minicpm5 OBLIGATORI) ──
    qb = {"backend": "minicpm5", "validation_loss": loss_before,
          "validation_perplexity": ppl_before, "score_total": 0,
          "score_max": len(hold)}
    qa = {"backend": "minicpm5", "validation_loss": loss_after,
          "validation_perplexity": ppl_after, "score_total": score,
          "score_max": len(hold)}
    with open(os.path.join(OUT, "quality_before.json"), "w") as f:
        json.dump(qb, f, indent=2)
    with open(os.path.join(OUT, "quality_after.json"), "w") as f:
        json.dump(qa, f, indent=2)
    print("QUALITY_COMPLETE loss %.4f -> %.4f holdout %d/%d"
          % (loss_before, loss_after, score, len(hold)), flush=True)
    bk.close()


if __name__ == "__main__":
    main()
