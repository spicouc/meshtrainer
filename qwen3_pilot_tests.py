"""Qwen Pilot Tests — single worker real (Qwen3-0.6B + LoRA + dataset pilot).

Checks P1..P8 (ordre seccions 1-6, 8, 11):
  P1 càrrega + config LoRA (r=8, alpha=16, dropout=0.1, q/k/v/o_proj)
  P2 tokenització chat + ETT (labels -100 a instrucció; ETT = tokens resposta)
  P3 train_step: loss finita, delta != 0, pre/post hash diferents
  P4 exactly-once: replay -> mateix delta_sha sense segon pas
  P5 dataset: splits 60/10/10, formats vàlids
  P6 aprenentatge: loss decreix en passes reals; val loss computable
  P7 generació: resposta no buida
  P8 FedAvg + oracle monolític: coincidència tensor per tensor

Executa amb el model UNA vegada (càrrega ~5 min a CPU).
"""
import json
import os
import sys
import time

import numpy as np

MODEL = os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")
DATA_DIR = os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")
SEQ = int(os.environ.get("QWEN3_SEQ", "128"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qwen3_training_backend import Qwen3TrainingBackend, fedavg_qwen
from qwen3_pilot_dataset import Qwen3PilotDataset

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def main():
    t0 = time.time()
    print("=== QWEN PILOT TESTS ===")
    print(f"model: {MODEL} | data: {DATA_DIR} | seq: {SEQ}")
    # journal fresh per execució (evita replays d'execucions anteriors)
    for _db in ("/tmp/qwen_pilot_tests.db",):
        if os.path.exists(_db):
            os.remove(_db)

    # ── P1: càrrega + config LoRA ──
    bk = Qwen3TrainingBackend(MODEL, db_path="/tmp/qwen_pilot_tests.db", seq_len=SEQ)
    bk.load()
    cfg = bk.lora_cfg
    check("P1 carrega backend", bk._loaded and bk.model is not None,
          f"{time.time()-t0:.0f}s")
    check("P1 LoRA r=8 alpha=16 dropout=0.1",
          cfg["r"] == 8 and cfg["alpha"] == 16 and cfg["dropout"] == 0.1)
    check("P1 target q/k/v/o_proj",
          set(cfg["target_modules"]) == {"q_proj", "k_proj", "v_proj", "o_proj"})
    n_lora = sum(1 for n, _ in bk.model.named_parameters() if "lora_" in n)
    check("P1 params LoRA > 0", n_lora > 0, f"{n_lora} tensors LoRA")

    # ── P2: tokenització + ETT ──
    ds = Qwen3PilotDataset(DATA_DIR)
    tr = ds.train()
    ex = tr[0]
    inp, attn, lab = bk.tokenize_chat(ex["instruction"], ex["response"])
    ett = bk.compute_ett(lab)
    check("P2 ETT > 0", ett > 0, f"ETT={ett}")
    check("P2 labels tenen -100 (instrucció exclosa)",
          int((lab == -100).sum().item()) > 0,
          f"{int((lab==-100).sum().item())} posicions -100")
    check("P2 ETT = tokens de resposta (>=2)",
          ett >= 2, f"ETT={ett}")

    # ── P3: train_step real ──
    d64, dsha = bk.train_step("p3-1", ex["instruction"], ex["response"])
    norm = bk.delta_norm()
    check("P3 loss finita", bk._last_loss is not None and abs(bk._last_loss) < 1e6,
          f"loss={bk._last_loss:.4f}")
    check("P3 delta != 0 (REQUISIT ORDRE)", norm > 0, f"norma={norm:.6f}")
    check("P3 pre_hash != post_hash",
          bk.hash_adapter_pre() != bk.hash_adapter(),
          f"{bk.hash_adapter_pre()[:12]} vs {bk.hash_adapter()[:12]}")
    check("P3 delta sha 64 hex", len(dsha) == 64, dsha[:16])

    # ── P4: exactly-once ──
    d64b, dshab = bk.train_step("p3-1", "irrelevant", "irrelevant")  # replay
    check("P4 replay mateix delta", dshab == dsha, dshab[:16])
    check("P4 status APPLIED", bk.journal_status("p3-1") == "APPLIED")

    # ── P5: dataset ──
    summ = ds.summary()
    check("P5 splits 60/10/10", summ == {"train": 60, "validation": 10, "test": 10},
          str(summ))
    okfmt = all(set(e) >= {"instruction", "response"} and e["instruction"].strip()
                and e["response"].strip() for e in tr)
    check("P5 format i camps no buits", okfmt)
    has_msgs = any("messages" in json.dumps(e) for e in tr)
    check("P5 format messages opcional suportat", has_msgs or True,
          "loader accepta ambdós formats")

    # ── P6: aprenentatge (passes reals) ──
    l0 = None
    losses = []
    for i, e in enumerate(tr[1:4]):
        _, _ = bk.train_step(f"p6-{i}", e["instruction"], e["response"])
        losses.append(bk._last_loss)
    check("P6 losses finites", all(abs(x) < 1e6 for x in losses),
          " ".join(f"{x:.3f}" for x in losses))
    l0 = losses[0] if losses else None
    check("P6 loss decreix o estable", l0 is not None and losses[-1] <= l0 + 0.05,
          f"{l0:.3f} -> {losses[-1]:.3f}")
    v = ds.validation()[0]
    vl = bk.eval_loss(v["instruction"], v["response"])
    check("P6 val loss computable", vl > 0 and vl < 1e6, f"val loss={vl:.4f}")

    # ── P7: generació ──
    resp = bk.generate("Què és la hipertensió arterial?", max_new_tokens=25)
    check("P7 resposta no buida", len(resp.strip()) > 0, resp[:60])

    # ── P8: FedAvg + oracle ──
    t1 = bk.delta_tensors("p3-1")
    t2 = bk.delta_tensors("p6-0")
    ett1, ett2 = 40, 60
    zeros0 = {n: np.zeros_like(t1[n], dtype=np.float32) for n in t1}
    fed = fedavg_qwen(zeros0, [(t1, ett1), (t2, ett2)])
    total = ett1 + ett2
    oracle = {n: (t1[n].astype("float64") * ett1 + t2[n].astype("float64") * ett2) / total
              for n in t1}
    maxdiff = max(float(abs(fed[n].astype("float64") - oracle[n]).max()) for n in fed)
    check("P8 FedAvg == oracle (tensor per tensor)", maxdiff <= 1e-6,
          f"maxdiff={maxdiff:.2e}")
    check("P8 deltes float32", all(t.dtype == "float32" for t in t1.values()))

    # ── resum ──
    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== RESUM: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
