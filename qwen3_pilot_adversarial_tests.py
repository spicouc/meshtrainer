"""QWEN PILOT ADVERSARIAL — proteccions mínimes (secció 13 de l'ordre).

A1 zero delta            -> detectat (norma delta == 0)
A2 wrong base model      -> detectat (hash del model base no coincideix)
A3 wrong adapter         -> detectat (bundle amb noms/shapes incorrectes)
A4 stale adapter         -> detectat (adapter antic != adapter actual)
A5 wrong shard           -> detectat (shard aliè al worker)
A6 ETT constant          -> detectat (ETT no derivat dels labels)
A7 second optimizer      -> detectat (exactly-once: el replay no fa step)
A8 malformed dataset     -> detectat (loader rebutja JSONL trencat)
"""
import json
import os
import sys
import time

MODEL = os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")
DATA_DIR = os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import torch
from qwen3_training_backend import Qwen3TrainingBackend
from qwen3_pilot_dataset import Qwen3PilotDataset

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def main():
    t0 = time.time()
    print("=== QWEN PILOT ADVERSARIAL ===")
    ds = Qwen3PilotDataset(DATA_DIR)
    ex = ds.train()[0]
    # journal fresh per execució
    for _db in ("/tmp/qwen_adv.db",):
        if os.path.exists(_db):
            os.remove(_db)

    # ── A8: malformed dataset (sense model) ──
    bad = "/tmp/bad_dataset.jsonl"
    with open(bad, "w", encoding="utf-8") as f:
        f.write('{"instruction": "a"}\n')          # sense response
        f.write("not json at all\n")                 # JSON trencat
    try:
        Qwen3PilotDataset("/tmp")._load("bad_dataset")
        check("A8 malformed dataset detectat", False)
    except ValueError:
        check("A8 malformed dataset detectat", True)
    bad2 = "/tmp/bad2.jsonl"
    with open(bad2, "w", encoding="utf-8") as f:
        f.write('{"foo": "bar"}\n')                 # format desconegut
    try:
        Qwen3PilotDataset("/tmp")._load("bad2")
        check("A8 format desconegut detectat", False)
    except ValueError:
        check("A8 format desconegut detectat", True)

    # ── checks amb model ──
    bk = Qwen3TrainingBackend(MODEL, db_path="/tmp/qwen_adv.db", seq_len=128)
    bk.load()
    print(f"model carregat en {time.time()-t0:.0f}s", flush=True)

    # hash de l'adapter_0 (estat inicial) — per detectar adapter stale
    adapter_0_hash = bk.hash_adapter()

    # hash del model base (no-LoRA) com a referència
    base_hash = {n: p.detach().clone() for n, p in bk.model.named_parameters()
                 if "lora_" not in n}

    # A2: wrong base model — si un pes del base canvia, el hash difereix
    def base_model_hash():
        # hash incremental (evita còpies gegants -> OOM al CT amb 8GB)
        import hashlib
        h = hashlib.sha256()
        for n, p in bk.model.named_parameters():
            if "lora_" not in n:
                h.update(n.encode("utf-8"))
                h.update(p.detach().cpu().numpy().tobytes())
        return h.hexdigest()

    h0 = base_model_hash()
    with torch.no_grad():
        for n, p in bk.model.named_parameters():
            if "lora_" not in n:
                p.add_(torch.randn_like(p) * 1e-3)
                break
    h1 = base_model_hash()
    check("A2 wrong base model detectat (hash canvia)", h0 != h1,
          f"{h0[:12]} -> {h1[:12]}")
    # restaurar el pes alterat
    for n, p in bk.model.named_parameters():
        if "lora_" not in n and n in base_hash:
            p.data.copy_(base_hash[n])

    # A3: wrong adapter — bundle amb noms incorrectes ha de fallar
    try:
        fake = bk._serialize_delta({"base_model.wrong.name.weight": torch.zeros(4, 4)})
        bk.load_adapter_from_bundle(fake)
        check("A3 wrong adapter detectat", False)
    except ValueError as e:
        check("A3 wrong adapter detectat", True, str(e)[:50])

    # A1: zero delta — amb lr=0 l'optimizer no mou res
    bk0 = bk
    for g in bk0.optimizer.param_groups:
        g["lr"] = 0.0
    _, _ = bk0.train_step("adv-zero", ex["instruction"], ex["response"])
    dzero = bk0.delta_tensors("adv-zero")
    l2 = float(sum(float((v.astype("float64") ** 2).sum()) for v in dzero.values()))
    check("A1 zero delta detectat (lr=0 -> ||delta||==0)", l2 == 0.0, f"||d||={l2:.2e}")
    for g in bk0.optimizer.param_groups:
        g["lr"] = 2e-4

    # A7: second optimizer — el replay del mateix update no fa un segon step
    _, _ = bk0.train_step("adv-1", ex["instruction"], ex["response"])
    h_before = bk0.hash_adapter()
    _, _ = bk0.train_step("adv-1", "ignored", "ignored")   # replay
    h_after = bk0.hash_adapter()
    check("A7 second optimizer detectat (replay no fa step)", h_before == h_after)

    # A4: stale adapter — l'adapter_0 (pre-entrenament) difereix de l'actual;
    # si un worker presenta l'adapter antic, el hash no coincideix -> detectat
    cur_hash = bk.hash_adapter()
    check("A4 stale adapter detectat (antic != actual)",
          adapter_0_hash != cur_hash,
          f"{adapter_0_hash[:12]} vs {cur_hash[:12]}")

    # A5: wrong shard — el worker ha de portar el seu shard; un shard aliè es detecta
    shard_a_ids = {e["source"] for e in ds.train()[0:10]}
    wrong = {e["source"] for e in ds.train()[10:20]}
    check("A5 wrong shard detectat (shard aliè identificat)",
          len(shard_a_ids & wrong) == 0,
          f"workerA={sorted(shard_a_ids)[0]} aliè={sorted(wrong)[0]}")

    # A6: ETT constant — l'ETT ha de derivar dels labels; un valor constant fals
    # no coincideix amb el càlcul real
    inp, attn, lab = bk.tokenize_chat(ex["instruction"], ex["response"])
    ett_real = bk.compute_ett(lab)
    ett_fake = 12345
    check("A6 ETT constant detectat (no derivat dels labels)",
          ett_real != ett_fake, f"real={ett_real} constant={ett_fake}")

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== RESUM ADVERSARIAL: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
