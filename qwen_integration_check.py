"""qwen_integration_check.py — validació end-to-end del pilot distribuït R2:
servidor (serve54 Qwen) + 2 workers com a SUBPROCESSOS reals + contribucions
+ FedAvg Coordinator + oracle independent. (Mini-driver de validació.)

Flux:
  1. engega el servidor HTTP (thread)
  2. genera adapter_0 (backend)
  3. executa Worker A (subprocess, shard A, 2 exemples)
  4. executa Worker B (subprocess, shard B, 2 exemples) — seqüencial (RAM)
  5. FedAvg Coordinator -> adapter_1
  6. oracle independent (fedavg_qwen) -> comparar tensor per tensor
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, '/root/meshtrainer')
os.chdir('/root/meshtrainer')

from qwen_http_server import serve
from qwen3_training_backend import Qwen3TrainingBackend, fedavg_qwen
import numpy as np

MODEL = '/root/qwen3_0_6b_snapshot'
DATA = '/root/meshtrainer/qwen3_pilot_data'
OUT = '/tmp/qwen_int_out'
DB = '/tmp/qwen_int_server.db'

ok = 0
fail = 0

def check(name, cond, detail=""):
    global ok, fail
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if cond: ok += 1
    else: fail += 1

def main():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    for p in (DB,):
        if os.path.exists(p): os.remove(p)
    os.makedirs(OUT, exist_ok=True)

    # ── 1. servidor ──
    httpd, proto = serve(port=19862, db_path=DB)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.5)
    print("servidor en marxa a :19862", flush=True)

    # ── 2. adapter_0 ──
    bk = Qwen3TrainingBackend(MODEL, db_path='/tmp/qwen_int_ad0.db', seq_len=128)
    bk.load()
    ad0 = bk.adapter_to_bundle()
    ad0_path = os.path.join(OUT, 'adapter_0.bundle')
    with open(ad0_path, 'wb') as f:
        f.write(ad0)
    ad0_sha = hashlib.sha256(ad0).hexdigest()
    base_hash = bk.base_model_hash()
    print(f"adapter_0 sha={ad0_sha[:16]} base={base_hash[:16]}", flush=True)
    del bk

    # ── 3/4. workers A i B (subprocessos reals, seqüencials) ──
    for w, sess, shard, asg in (("qwen-A", "A1", "A", "asg-A"),
                                ("qwen-B", "B1", "B", "asg-B")):
        cmd = [sys.executable, 'qwen_worker.py', '--worker-id', w,
               '--session-id', sess, '--shard', shard,
               '--assignment-id', asg, '--server-url', 'http://127.0.0.1:19862',
               '--model', MODEL, '--data-dir', DATA,
               '--adapter-0', ad0_path, '--adapter-0-sha', ad0_sha,
               '--out-dir', OUT, '--num-examples', '2']
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        out = r.stdout + r.stderr
        print(f"worker {w} exit={r.returncode} ({time.time()-t0:.0f}s)", flush=True)
        if r.returncode != 0:
            print(out[-1500:], flush=True)
        check(f"worker {w} exit 0", r.returncode == 0)
        check(f"worker {w} WORKER_DONE", "WORKER_DONE" in out)
    httpd.shutdown()

    # ── evidències ──
    evA = json.load(open(os.path.join(OUT, 'evidence_qwen-A.json')))
    evB = json.load(open(os.path.join(OUT, 'evidence_qwen-B.json')))
    check("workers: mateix base_model_hash",
          evA["base_model_hash"] == evB["base_model_hash"] == base_hash,
          evA["base_model_hash"][:16])
    check("workers: shard_manifest diferents",
          evA["shard_manifest_sha"] != evB["shard_manifest_sha"])
    check("workers: ETT diferents", evA["ett_actual"] != evB["ett_actual"],
          f"{evA['ett_actual']} vs {evB['ett_actual']}")
    check("workers: delta != 0", evA["delta_sha"] != evB["delta_sha"])

    # ── 5. FedAvg Coordinator ──
    ad0_b64 = base64.b64encode(ad0).decode('ascii')
    fed = proto.handle('round_fedavg',
                       {'run_id': 'run1', 'round_id': 'r1',
                        'assignment_id': 'asg-A', 'worker_id': 'qwen-A',
                        'adapter_0_bundle_b64': ad0_b64})
    a1_bytes = base64.b64decode(fed['adapter_1_b64'])
    check("FedAvg Coordinator: adapter_1 sha", len(fed['adapter_1_sha']) == 64,
          fed['adapter_1_sha'][:16])
    check("FedAvg: 2 contribucions ACTIVE", fed['num_contributions'] == 2,
          str(fed['num_contributions']))

    # ── 6. oracle independent ──
    dA = np.load(os.path.join(OUT, 'delta_tmp.npz')) if False else None
    # deltes dels workers: els llegim del Coordinator
    rows = proto.coord.active_deltas('run1', 'r1')
    deltas, etts = [], []
    for r in rows:
        dt = Qwen3TrainingBackend._deserialize_delta(base64.b64decode(r["delta_bundle_b64"]))
        deltas.append({k: np.asarray(v, dtype=np.float32) for k, v in dt.items()})
        etts.append(r["ett"])
    adapter_0 = {k: np.asarray(v, dtype=np.float32)
                 for k, v in Qwen3TrainingBackend._deserialize_delta(ad0).items()}
    oracle = fedavg_qwen(adapter_0, list(zip(deltas, etts)))
    fed_a1 = Qwen3TrainingBackend._deserialize_delta(a1_bytes)
    maxdiff = max(float(np.abs(
        np.asarray(fed_a1[n], dtype='float64') - oracle[n].astype('float64')).max())
        for n in oracle)
    check("oracle == FedAvg Coordinator (tensor per tensor)", maxdiff <= 1e-6,
          f"maxdiff={maxdiff:.2e}")

    print(f"=== INTEGRATION CHECK: {ok}/{ok+fail} PASS ===")
    sys.exit(0 if fail == 0 else 1)

if __name__ == "__main__":
    main()
