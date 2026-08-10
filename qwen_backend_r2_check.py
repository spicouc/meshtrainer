"""Validació dels enduriments R2 del backend (seccions 1-4 de l'ordre).

Estructura: S1-S3 en el procés principal (2 càrregues seqüencials) i la
secció 4 (recovery post-APPLIED) en un SUBPROCÉS fresc (--s4): el PyTorch
reté memòria entre càrregues del mateix procés i el CT112 (8GB) amb el host
saturat fa OOM si s'acumulen models. Cada procés allibera la memòria en
acabar.
"""
import hashlib
import os
import subprocess
import sys

sys.path.insert(0, '/root/meshtrainer')
import torch
# execució DETERMINISTA: el no-determinisme BLAS multithread a CPU faria
# diferir els forwards en ~1e-7 entre execucions (els hashes S4 ho detecten)
torch.set_num_threads(1)
from qwen3_training_backend import Qwen3TrainingBackend

MODEL = '/root/qwen3_0_6b_snapshot'
EX = ('Què és la diabetis?', 'La diabetis és una malaltia crònica.')
AD0_PATH = '/tmp/qwen_r2_ad0.bundle'

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if cond:
        ok += 1
    else:
        fail += 1


def clean(*paths):
    for p in paths:
        if os.path.exists(p):
            os.remove(p)


def run_s4():
    """Secció 4 (procés fresc): recovery post-APPLIED amb 3 càrregues
    seqüencials (no-crash, procés C, procés D)."""
    print("=== S4 (subprocés fresc) ===", flush=True)
    with open(AD0_PATH, 'rb') as f:
        ad0 = f.read()
    sha0 = hashlib.sha256(ad0).hexdigest()
    s1_ref = ""
    if os.path.exists('/tmp/qwen_r2_u1_sha.txt'):
        with open('/tmp/qwen_r2_u1_sha.txt') as f:
            s1_ref = f.read().strip()
    clean('/tmp/qwen_r2_c.db', '/tmp/qwen_r2_d.db')

    # no-crash: u1 + u2 encadenats (mateixa instància)
    bkn = Qwen3TrainingBackend(MODEL, db_path='/tmp/qwen_r2_d.db', seq_len=32)
    bkn.load()
    d1n, s1n = bkn.train_step('u1', EX[0], EX[1], worker_id='wA', assignment_id='asg1',
                              shard_id='shardA', example_id='ex1')
    check("S4 no-crash: u1 == procés principal", s1n == s1_ref, f"{s1n[:16]}")
    d2, s2 = bkn.train_step('u2', EX[0], EX[1], worker_id='wA', assignment_id='asg1',
                            shard_id='shardA', example_id='ex2')
    post_nocrash = bkn.hash_adapter()
    del bkn
    import gc; gc.collect()

    # crash: procés C fa u1 (APPLIED) a una altra BD i "mor"
    bkc = Qwen3TrainingBackend(MODEL, db_path='/tmp/qwen_r2_c.db', seq_len=32)
    bkc.load()
    d1c, s1c = bkc.train_step('u1', EX[0], EX[1], worker_id='wA', assignment_id='asg1',
                              shard_id='shardA', example_id='ex1')
    check("S4 procés C: u1 APPLIED (delta sha == procés A)",
          s1c == s1_ref, f"{s1c[:16]} vs {s1_ref[:16]}")
    del bkc  # "mort" (el journal persisteix a /tmp/qwen_r2_c.db)
    gc.collect()

    # procés D (recovery): mateixa BD, adapter_0 + recover + u2
    bkd = Qwen3TrainingBackend(MODEL, db_path='/tmp/qwen_r2_c.db', seq_len=32)
    bkd.load()
    bkd.load_adapter_from_bundle(ad0, sha0)
    rec = bkd.recover_applied('u1')
    check("S4 recover_applied reconstruït", rec is not None,
          rec["adapter_post_hash"][:12] if rec else "")
    # el replay NO ha fet cap optimizer.step: un segon replay tampoc canvia el model
    h1 = bkd.hash_adapter()
    d3, s3 = bkd.train_step('u1', EX[0], EX[1], worker_id='wA', assignment_id='asg1',
                            shard_id='shardA', example_id='ex1')
    h2 = bkd.hash_adapter()
    check("S4 recovery sense segon optimizer", s3 == s1c and h1 == h2,
          f"{s3[:12]}")
    d2c, s2c = bkd.train_step('u2', EX[0], EX[1], worker_id='wA', assignment_id='asg1',
                              shard_id='shardA', example_id='ex2')
    post_crash = bkd.hash_adapter()
    check("S4 adapters finals idèntics (no-crash vs crash)",
          post_nocrash == post_crash, f"{post_nocrash[:12]} vs {post_crash[:12]}")
    check("S4 delta u2 idèntic (no-crash vs crash)", s2 == s2c, s2[:16])
    print(f"=== R2 BACKEND CHECK S4: {ok}/{ok + fail} PASS ===")
    sys.exit(0 if fail == 0 else 1)


def main():
    if "--s4" in sys.argv:
        run_s4()
        return

    # ── secció 2: base model identity (1 model a la vegada) ──
    clean('/tmp/qwen_r2_a.db', '/tmp/qwen_r2_b.db', '/tmp/qwen_r2_c.db',
          '/tmp/qwen_r2_d.db', AD0_PATH)
    bk = Qwen3TrainingBackend(MODEL, db_path='/tmp/qwen_r2_a.db', seq_len=32)
    bk.load()
    h1 = bk.base_model_hash()
    id1 = bk._base_identity
    check("S2 base_model_hash present", len(h1) == 64, h1[:16])
    check("S2 config_sha present", bool(id1["config_sha"]), id1["config_sha"][:16])
    check("S2 weight_manifest_sha present", len(id1["weight_manifest_sha"]) == 64)
    del bk
    import gc as _gc; _gc.collect()

    bk2 = Qwen3TrainingBackend(MODEL, db_path='/tmp/qwen_r2_b.db', seq_len=32)
    bk2.load()
    h2 = bk2.base_model_hash()
    check("S2 mateix base_model_hash en 2 càrregues", h1 == h2, f"{h1[:12]} vs {h2[:12]}")

    # ── secció 1: load_adapter endurit (amb bk2) ──
    ad0 = bk2.adapter_to_bundle()
    sha0 = hashlib.sha256(ad0).hexdigest()
    bk2.load_adapter_from_bundle(ad0, sha0)
    check("S1 load_adapter expected_sha correcte", True)
    try:
        bk2.load_adapter_from_bundle(ad0, '0' * 64)
        check("S1 wrong adapter SHA REJECTED", False)
    except ValueError:
        check("S1 wrong adapter SHA REJECTED", True)
    try:
        bk2.load_adapter_from_bundle(ad0, None)
        check("S1 expected_sha obligatori (strict)", False)
    except ValueError:
        check("S1 expected_sha obligatori (strict)", True)
    t = bk2._deserialize_delta(ad0)
    t.pop(next(iter(t)))
    partial = bk2._serialize_delta(t)
    try:
        bk2.load_adapter_from_bundle(partial, hashlib.sha256(partial).hexdigest())
        check("S1 partial adapter REJECTED", False)
    except ValueError:
        check("S1 partial adapter REJECTED", True)
    extra_t = bk2._deserialize_delta(ad0)
    extra_t["base_model.extra.weight"] = bk2._deserialize_delta(ad0)[next(iter(extra_t))]
    extra_b = bk2._serialize_delta(extra_t)
    try:
        bk2.load_adapter_from_bundle(extra_b, hashlib.sha256(extra_b).hexdigest())
        check("S1 adapter amb tensor extra REJECTED", False)
    except ValueError:
        check("S1 adapter amb tensor extra REJECTED", True)

    # ── secció 3: request_sha exactly-once ──
    rs = bk2._make_request_sha('u1', 'wA', 'asg1', 'shardA', bk2.hash_adapter(),
                               'ex1', EX[0], EX[1])
    d1, s1 = bk2.train_step('u1', EX[0], EX[1], worker_id='wA', assignment_id='asg1',
                            shard_id='shardA', example_id='ex1', request_sha=rs)
    d1b, s1b = bk2.train_step('u1', EX[0], EX[1], worker_id='wA', assignment_id='asg1',
                              shard_id='shardA', example_id='ex1', request_sha=rs)
    check("S3 replay mateix request_sha -> mateix delta", s1 == s1b, s1[:16])
    try:
        bk2.train_step('u1', 'ALTRES DADES COMPLETAMENT DIFERENTS', 'RESPOSTA ALTRES',
                       worker_id='wA', assignment_id='asg1', shard_id='shardA',
                       example_id='ex1')
        check("S3 mateix update_id payload diferent REJECTED", False)
    except ValueError:
        check("S3 mateix update_id payload diferent REJECTED", True)
    del bk2
    _gc.collect()

    # desar ad0 + sha d'u1 per a la secció 4 (subprocés fresc)
    with open(AD0_PATH, 'wb') as f:
        f.write(ad0)
    with open('/tmp/qwen_r2_u1_sha.txt', 'w') as f:
        f.write(s1)

    print(f"=== R2 BACKEND CHECK S1-S3: {ok}/{ok + fail} PASS ===")
    if fail:
        sys.exit(1)
    print("=== S4 en subprocés fresc (memòria independent) ===", flush=True)
    r = subprocess.run([sys.executable, __file__, "--s4"])
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
