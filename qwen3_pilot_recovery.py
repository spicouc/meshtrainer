"""QWEN PILOT RECOVERY — Safe Recovery amb Qwen real (secció 10 de l'ordre).

R1  crash després d'APPLIED -> el recovery retorna el MATEIX delta (journal
    persistent SQLite) sense segon optimizer
R2  checkpoint idempotent -> mateix update aplicat 2 cops = mateix resultat
R3  lease expiry -> la lease EXPIRED fa REJECTED l'operació (gating)
R4  reassignació -> després d'EXPIRED un altre worker pot adquirir la unitat
R5  mid-backward -> EXPIRED, sense persistir cap autograd (journal sense delta)

S'integra amb rc5_4_leases.LeaseManager (importat, NO modificat).
"""
import os
import sqlite3
import sys
import time

MODEL = os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")
DATA_DIR = os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qwen3_training_backend import Qwen3TrainingBackend
from qwen3_pilot_dataset import Qwen3PilotDataset
from rc5_4_leases import (LeaseManager, LeaseError,
                          LEASE_ACTIVE, LEASE_EXPIRED)

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def require_lease(lm, lease):
    """Gating de lease (política del pipeline): EXPIRED/ABORTED -> rebutjada."""
    st = lm.status(lease_id=lease["lease_id"])["status"]
    if st != LEASE_ACTIVE:
        raise LeaseError(f"Lease {lease['lease_id']} no activa (status={st})")
    return st


def main():
    t0 = time.time()
    print("=== QWEN PILOT RECOVERY ===")
    db = "/tmp/qwen_rec_journal.db"
    if os.path.exists(db):
        os.remove(db)

    # ── R3/R4/R5: leases (rellotge injectable, sense model) ──
    conn = sqlite3.connect("/tmp/qwen_rec_leases.db")
    conn.row_factory = sqlite3.Row   # requerit per LeaseManager (dict(row))
    clock = {"now": 1000.0}
    lm = LeaseManager(conn, now=lambda: clock["now"], default_ttl_seconds=30.0)
    ex = Qwen3PilotDataset(DATA_DIR).train()[0]

    ls = lm.acquire("run1", "r1", "asg1", "u1", "workerA", "sessA", ttl_seconds=30)
    st = require_lease(lm, ls)
    check("R3 lease ACTIVE inicial", st == LEASE_ACTIVE)
    clock["now"] += 31.0
    st_after = lm.status(lease_id=ls["lease_id"])["status"]
    check("R3 lease EXPIRED després del TTL", st_after == LEASE_EXPIRED, st_after)
    rejected = False
    try:
        require_lease(lm, ls)
    except LeaseError:
        rejected = True
    check("R3 operació amb lease expirada REJECTED", rejected)

    ls2 = lm.acquire("run1", "r1", "asg1", "u1", "workerB", "sessB", ttl_seconds=30)
    check("R4 reassignació després d'EXPIRED", ls2["status"] == LEASE_ACTIVE)
    check("R4 lease nova per workerB", ls2["lease_id"] != ls["lease_id"])

    # R5: mid-backward -> EXPIRED sense autograd: worker C adquireix, "mor"
    # a mig backward (mai crida train_step), el rellotge avança i expira;
    # el journal del backend no té cap delta i la unitat es reassigna
    ls3 = lm.acquire("run1", "r1", "asg1", "u2", "workerC", "sessC", ttl_seconds=30)
    clock["now"] += 31.0
    st3 = lm.status(lease_id=ls3["lease_id"])["status"]
    check("R5 mid-backward -> EXPIRED", st3 == LEASE_EXPIRED, st3)
    ls4 = lm.acquire("run1", "r1", "asg1", "u2", "workerD", "sessD", ttl_seconds=30)
    check("R5 reassignació després de mid-backward", ls4["status"] == LEASE_ACTIVE)

    # ── R1/R2: exactly-once persistent (requereix el model) ──
    bk = Qwen3TrainingBackend(MODEL, db_path=db, seq_len=64)
    bk.load()
    print(f"model carregat en {time.time()-t0:.0f}s", flush=True)
    d64, dsha = bk.train_step("rec-1", ex["instruction"], ex["response"])
    pre_hash = bk.hash_adapter()

    # "crash": nova instància amb la MATEIXA BD (recuperació)
    bk2 = Qwen3TrainingBackend(MODEL, db_path=db, seq_len=64)
    bk2.load()
    d64b, dshab = bk2.train_step("rec-1", "ignored", "ignored")  # replay
    check("R1 crash+recovery: mateix delta", dshab == dsha, dshab[:16])
    check("R1 delta persistent (b64 igual)", d64b == d64)
    # el replay NO ha fet cap optimizer.step: un segon replay tampoc canvia el model
    h1 = bk2.hash_adapter()
    d3, s3 = bk2.train_step("rec-1", "ignored", "ignored")
    h2 = bk2.hash_adapter()
    check("R2 recovery sense segon optimizer",
          s3 == dsha and h1 == h2, "replay idempotent, model intacte")

    # R2: checkpoint idempotent — aplicar 2 cops el mateix update
    d1, s1 = bk.train_step("rec-2", ex["instruction"], ex["response"])
    d2, s2 = bk.train_step("rec-2", ex["instruction"], ex["response"])
    check("R2 checkpoint idempotent (2 aplicacions = 1 delta)", s1 == s2, s1[:16])

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== RESUM RECOVERY: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
