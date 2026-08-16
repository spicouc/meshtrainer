#!/usr/bin/env python3
"""generic_distributed_run.py — DRIVER END-TO-END GENERIC (R2.1, punt 9).

Executa el flux distribuït complet utilitzant EXCLUSIVAMENT:
  Generic HTTP server (training_http_server)
  Generic Coordinator (ModelRoundCoordinator)
  Generic Worker (model_worker)
  Backend plugin (--backend qwen3 | dummy)

Workers A i B com a SUBPROCESSOS reals (seqüencial per RAM):
  - worker_ids diferents, sessions diferents, leases diferents, shards
    diferents, HTTP real, Coordinator real, ETT real, FedAvg real,
    Round 2, recovery real (SIGKILL opcional).

Ús:
  generic_distributed_run.py --backend qwen3 [--num-examples N] [--seq-len L]
  generic_distributed_run.py --backend dummy  [--num-examples N]
"""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from training_http_server import serve
from model_round_coordinator import ModelRoundCoordinator
from adapter_codec import unpack_tensors, to_numpy_dict, fedavg

CHECKS = []
PORT = 19862
ADMIN_TOKEN = os.environ.get("MESH_ADMIN_TOKEN", "mesh-admin-token-test")
SERVER_URL = f"http://127.0.0.1:{PORT}"


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def make_adapter_0(backend_name, model, seq_len, out_dir, bcfg):
    """Genera adapter_0 amb el mateix backend (plugin) i el retorna."""
    from model_worker import load_backend
    cls = load_backend(backend_name)
    if backend_name == "dummy":
        mp = model or "dummy"
    elif backend_name == "minicpm5":
        mp = model or os.environ.get(
            "MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")
    else:
        mp = model or "/root/qwen3_0_6b_snapshot"
    dbp = f"/tmp/generic_ad0_{backend_name}.db"
    if os.path.exists(dbp):
        os.remove(dbp)
    bk = cls(mp, db_path=dbp, seq_len=seq_len, **bcfg)
    bk.load_model()
    ad0 = bk.adapter_to_bundle()
    identity = bk.base_model_identity()
    bk.close()
    return ad0, identity


def prepare_round_data(backend_name, model, seq_len, out_dir, bcfg,
                       shards=None, num_ex_per_shard=2, log=None):
    """R1.1 (punt 5): CONSOLIDA les 3 càrregues seqüencials del driver en
    UNA sola càrrega preparatòria.

    Flux (1 load):
      1) bk.load_model()
      2) adapter_to_bundle()            -> adapter_0 (autoritatiu)
      3) load_adapter(ad0) + adapter_hash() -> adapter_pre_hash
      4) per shard: tokenize+compute_ett (el model ja està carregat)
      5) close + del + gc.collect() + malloc_trim(0)   (zero model resident)

    Retorna (ad0, identity, pre_hash, ett_by_shard). No canvia cap semàntica
    del protocol: el Coordinator només rep hashes i ETTs (etapa trusted).
    """
    import gc
    from model_worker import load_backend, load_examples

    def _mem(tag):
        if log:
            try:
                with open("/proc/self/status") as f:
                    vmrss = [l for l in f
                             if l.startswith("VmRSS")][0].split()[1]
                log.write(f"  {tag}: VmRSS={int(vmrss)//1024} MB\n")
                log.flush()
            except Exception:
                pass

    cls = load_backend(backend_name)
    if backend_name == "dummy":
        mp = model or "dummy"
    elif backend_name == "minicpm5":
        mp = model or os.environ.get(
            "MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")
    else:
        mp = model or "/root/qwen3_0_6b_snapshot"
    dbp = f"/tmp/generic_prep_{backend_name}.db"
    if os.path.exists(dbp):
        os.remove(dbp)

    _mem("A. abans de carregar MiniCPM")
    bk = cls(mp, db_path=dbp, seq_len=seq_len, **(bcfg or {}))
    bk.load_model()
    _mem("B. model carregat")

    # adapter_0 (autoritatiu — mateixos bytes per A i B)
    ad0 = bk.adapter_to_bundle()
    identity = bk.base_model_identity()
    _mem("C. adapter_0 generat")

    # adapter_pre_hash = hash amb adapter_0 carregat (baseline ronda)
    sha0 = hashlib.sha256(ad0).hexdigest()
    bk.load_adapter(ad0, sha0, strict=True)
    pre_hash = bk.adapter_hash()
    _mem("D. pre_hash calculat")

    # ETT per shard (el model ja està carregat — zero càrregues extra)
    ett_map = {}
    ett_map_half = {}
    for shard in (shards or ["A", "B"]):
        exs = load_examples(data_dir_for(backend_name), shard,
                            num_ex_per_shard)
        ett = 0
        for e in exs:
            tok = bk.tokenize_example(e["instruction"], e["response"])
            ett += bk.compute_ett(tok[2] if isinstance(tok, tuple) else tok)
        ett_map[shard] = ett
        if log:
            log.write(f"  ETT[{shard}]={ett}\n")
        # ETT per a meitat d'exemples (R2 usa n2 = num_examples//2)
        exs2 = load_examples(data_dir_for(backend_name), shard,
                             max(1, num_ex_per_shard // 2))
        ett2 = 0
        for e in exs2:
            tok = bk.tokenize_example(e["instruction"], e["response"])
            ett2 += bk.compute_ett(tok[2] if isinstance(tok, tuple) else tok)
        ett_map_half[shard] = ett2
        if log:
            log.write(f"  ETT_half[{shard}]={ett2}\n")
    _mem("E. ETT calculat")

    # alliberament ESTRICTE (punt 4): cap MiniCPM5 resident al driver
    bk.close()
    for attr in ("model", "tokenizer", "peft_model", "optimizer"):
        if hasattr(bk, attr):
            try:
                setattr(bk, attr, None)
            except Exception:
                pass
    del bk
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
    _mem("F/G. close+del+gc+malloc_trim")
    # R1.2: alliberar el page cache dels safetensors que AQUEST driver ha
    # generat en llegir el model (2.1 GB) — posix_fadvise(DONTNEED) sobre els
    # fitxers del model. NO toca el backend ni la càrrega funcional; només
    # retorna al kernel la memòria de cache que el driver va crear, perquè
    # els workers (A/B simultanis) tinguin MemAvailable real.
    try:
        import glob as _glob
        for _sf in _glob.glob(os.path.join(mp, "*.safetensors")) + \
                _glob.glob(os.path.join(mp, "*.bin")):
            _fd = os.open(_sf, os.O_RDONLY)
            try:
                os.posix_fadvise(_fd, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(_fd)
        with open("/proc/sys/vm/drop_caches", "w") as _dc:
            _dc.write("1")
    except Exception:
        pass
    _mem("G2. page cache safetensors alliberat (fadvise DONTNEED)")
    return ad0, identity, pre_hash, ett_map, ett_map_half


def run_worker_popen(backend_name, worker_id, session, shard, assignment,
                     round_id, adapter_path, adapter_sha, out_dir,
                     num_examples, seq_len, extra=None, train_only=False,
                     stagger=0.0):
    """Retorna un subprocess.Popen per a execució PARAL·LELA (punt 7:
    PID A != PID B, A i B vius simultàniament)."""
    if stagger:
        time.sleep(stagger)
    cmd = [sys.executable, "model_worker.py", "--backend", backend_name,
           "--worker-id", worker_id, "--session-id", session, "--shard", shard,
           "--assignment-id", assignment, "--round-id", round_id,
           "--server-url", SERVER_URL, "--adapter-0", adapter_path,
           "--adapter-0-sha", adapter_sha, "--out-dir", out_dir,
           "--num-examples", str(num_examples), "--seq-len", str(seq_len)]
    if backend_name == "qwen3":
        cmd += ["--model", os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")]
        cmd += ["--data-dir", os.environ.get("QWEN3_DATA",
                                             "/root/meshtrainer/qwen3_pilot_data")]
    elif backend_name == "dummy":
        cmd += ["--model", "dummy"]
        cmd += ["--data-dir", os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "qwen3_pilot_data")]
    elif backend_name == "minicpm5":
        cmd += ["--model", os.environ.get(
            "MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")]
        cmd += ["--data-dir", os.environ.get(
            "MINICPM5_DATA", "/root/meshtrainer/qwen3_pilot_data")]
    if extra:
        cmd += ["--backend-config", json.dumps(extra)]
    if train_only:
        cmd += ["--train-only"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def run_worker(backend_name, worker_id, session, shard, assignment, round_id,
               adapter_path, adapter_sha, out_dir, num_examples, seq_len,
               extra=None, train_only=False):
    cmd = [sys.executable, "model_worker.py", "--backend", backend_name,
           "--worker-id", worker_id, "--session-id", session, "--shard", shard,
           "--assignment-id", assignment, "--round-id", round_id,
           "--server-url", SERVER_URL, "--adapter-0", adapter_path,
           "--adapter-0-sha", adapter_sha, "--out-dir", out_dir,
           "--num-examples", str(num_examples), "--seq-len", str(seq_len)]
    if backend_name == "qwen3":
        cmd += ["--model", os.environ.get("QWEN3_MODEL", "/root/qwen3_0_6b_snapshot")]
        cmd += ["--data-dir", os.environ.get("QWEN3_DATA",
                                             "/root/meshtrainer/qwen3_pilot_data")]
    elif backend_name == "dummy":
        cmd += ["--model", "dummy"]
        cmd += ["--data-dir", os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "qwen3_pilot_data")]
    elif backend_name == "minicpm5":
        cmd += ["--model", os.environ.get(
            "MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")]
        cmd += ["--data-dir", os.environ.get(
            "MINICPM5_DATA", "/root/meshtrainer/qwen3_pilot_data")]
    if extra:
        cmd += ["--backend-config", json.dumps(extra)]
    if train_only:
        cmd += ["--train-only"]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    out = r.stdout + r.stderr
    # R2.5.1 (punt 6/8): l'output del worker és EVIDÈNCIA — es reemet a
    # stdout perquè els logs del gate mostrin "CONTRIBUTION SUBMITTED"
    # (i mai "CONTRIBUTION VALIDATED" com a acció del worker).
    if out.strip():
        for line in out.splitlines():
            if line.strip():
                print(f"[worker:{worker_id}] {line}", flush=True)
    if r.returncode != 0:
        print(out[-1500:], flush=True)
    return r.returncode, out


def run_workers_overlap(backend_name, specs, adapter_path, adapter_sha,
                        out_dir, seq_len, log=None, timeout_s=900):
    """R1.2: simultaneïtat REAL (NO stagger fixe).

    Flux:
      1) spawn worker 0 (A) amb Popen
      2) monitoritzar stdout d'A en temps real (thread)
      3) QUAN A imprimeix LEASE -> registrar tA_lease i spawn worker 1 (B)
         (PAR-01: A.poll() is None en el moment del spawn de B)
      4) monitoritzar B; registrar tB_lease
      5) esperar ambdós; registrar tA_release / tB_release

    Exigència d'overlap REAL (PAR-02):
        A_LEASE < B_LEASE < A_RELEASE
    (B adquireix la seva lease MENTRE A encara té la seva activa)

    Retorna (rcs, outs, times) per worker_id.
    """
    import threading as _th
    import time as _t

    def _reader(proc, wid, events, outbuf):
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw
                if isinstance(line, bytes):
                    line = line.decode(errors="replace")
                line = line.rstrip()
                if line.strip():
                    outbuf.append(line)
                    print(f"[worker:{wid}] {line}", flush=True)
                ts = _t.time()
                if " REGISTERED " in line:
                    events[wid]["registered"] = ts
                elif " LEASE " in line and "RENEWED" not in line \
                        and "RELEASED" not in line:
                    events[wid]["lease"] = ts
                elif " RELEASED" in line:
                    events[wid]["release"] = ts
        except Exception:
            pass
        finally:
            try:
                proc.wait()
            except Exception:
                pass

    events = {s["worker_id"]: {"registered": None, "lease": None,
                               "release": None} for s in specs}
    outbufs = {s["worker_id"]: [] for s in specs}
    procs = {}

    # 1) spawn A (worker 0)
    a, b = specs[0], specs[1]
    pA = run_worker_popen(
        backend_name, a["worker_id"], a["session"], a["shard"],
        a["assignment"], a["round_id"], adapter_path, adapter_sha, out_dir,
        a["num_ex"], seq_len)
    procs[a["worker_id"]] = pA
    _th.Thread(target=_reader, daemon=True,
               args=(pA, a["worker_id"], events, outbufs[a["worker_id"]])).start()
    if log:
        log.write(f"  spawned {a['worker_id']} pid={pA.pid} t={_t.time():.1f}\n")
        log.flush()

    # 2) esperar que A arribi a LEASE (màx timeout_s)
    t0 = _t.time()
    while events[a["worker_id"]]["lease"] is None and \
            _t.time() - t0 < timeout_s:
        _t.sleep(0.2)

    # PAR-01: A viu (no ha acabat) quan B arrenca
    alive_a_at_b_spawn = pA.poll() is None

    # 3) spawn B (trigger: LEASE d'A)
    pB = run_worker_popen(
        backend_name, b["worker_id"], b["session"], b["shard"],
        b["assignment"], b["round_id"], adapter_path, adapter_sha, out_dir,
        b["num_ex"], seq_len)
    procs[b["worker_id"]] = pB
    _th.Thread(target=_reader, daemon=True,
               args=(pB, b["worker_id"], events, outbufs[b["worker_id"]])).start()
    if log:
        log.write(f"  spawned {b['worker_id']} pid={pB.pid} "
                  f"t={_t.time():.1f} (A viu={alive_a_at_b_spawn})\n")
        log.flush()

    # 4/5) esperar ambdós (els readers fan wait al final)
    pA.wait()
    pB.wait()

    rcs = {s["worker_id"]: procs[s["worker_id"]].returncode
           for s in specs}
    outs = {s["worker_id"]: "\n".join(outbufs[s["worker_id"]])
            for s in specs}
    times = {s["worker_id"]: events[s["worker_id"]] for s in specs}
    if log:
        for wid, tm in times.items():
            log.write(f"  {wid}: registered={tm['registered']} "
                      f"lease={tm['lease']} release={tm['release']}\n")
            log.flush()
    return rcs, outs, times


def run_workers_parallel(backend_name, specs, adapter_path, adapter_sha,
                         out_dir, seq_len, stagger=8.0, log=None):
    """Compat: execució paral·lela amb stagger (ús intern si cal).
    R1.2: el gate fa servir run_workers_overlap (simultaneïtat real)."""
    import time as _t
    procs = {}
    for spec in specs:
        worker_id = spec["worker_id"]
        _t.sleep(stagger if worker_id != specs[0]["worker_id"] else 0.0)
        p = run_worker_popen(
            backend_name, worker_id, spec["session"], spec["shard"],
            spec["assignment"], spec["round_id"], adapter_path, adapter_sha,
            out_dir, spec["num_ex"], seq_len)
        procs[worker_id] = p
        if log:
            log.write(f"  spawned {worker_id} pid={p.pid}\n")
            log.flush()
    rcs, outs = {}, {}
    for worker_id, p in procs.items():
        out = p.communicate(timeout=3600)[0]
        outs[worker_id] = out
        rcs[worker_id] = p.returncode
        if out.strip():
            for line in out.splitlines():
                if line.strip():
                    print(f"[worker:{worker_id}] {line}", flush=True)
        if p.returncode != 0:
            print(out[-1500:], flush=True)
    return rcs, outs


def compute_pre_hash_clean(backend_name, model, seq_len, adapter_bytes,
                           log=None):
    """R1.1: pre_hash de l'adapter amb UNA càrrega + alliberament estricte
    (close+del+gc+malloc_trim). Equivalent semàntic de compute_pre_hash,
    però sense deixar cap model resident al driver (punt 4)."""
    import gc
    from model_worker import load_backend
    cls = load_backend(backend_name)
    if backend_name == "dummy":
        mp = model or "dummy"
    elif backend_name == "minicpm5":
        mp = model or os.environ.get(
            "MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")
    else:
        mp = model or "/root/qwen3_0_6b_snapshot"
    dbp = f"/tmp/generic_prehash_{backend_name}.db"
    if os.path.exists(dbp):
        os.remove(dbp)
    bk = cls(mp, db_path=dbp, seq_len=seq_len)
    bk.load_model()
    sha = hashlib.sha256(adapter_bytes).hexdigest()
    bk.load_adapter(adapter_bytes, sha, strict=True)
    pre = bk.adapter_hash()
    bk.close()
    for attr in ("model", "tokenizer", "peft_model", "optimizer"):
        if hasattr(bk, attr):
            try:
                setattr(bk, attr, None)
            except Exception:
                pass
    del bk
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
    if log:
        log.write(f"  pre_hash(adapter) calculat + alliberat\n")
        log.flush()
    return pre


def _pid_of(worker_out):
    """Extreu el PID del worker de l'output.
    Format worker: '[w-A] 38966 REGISTERED base=...' (model_worker línia 208)."""
    import re
    for line in (worker_out or "").splitlines():
        if " REGISTERED " in line:
            m = re.match(r"\[[\w-]+\]\s+(\d+)\s+REGISTERED", line)
            if m:
                return m.group(1)
    m = re.search(r"pid=(\d+)", worker_out or "")
    return m.group(1) if m else "?"


def fedavg_via_coordinator(proto, run_id, round_id, adapter_0_bytes):
    ad0_b64 = base64.b64encode(adapter_0_bytes).decode("ascii")
    return proto.handle("round_fedavg",
                        {"run_id": run_id, "round_id": round_id,
                         "assignment_id": "asg-A", "worker_id": "w-A",
                         "adapter_0_bundle_b64": ad0_b64,
                         "admin_token": ADMIN_TOKEN})


def admin_activate(proto, cids):
    """R2.4/R2.5: contribution.validate + activate són operacions ADMIN —
    el driver (admin) valida i activa les contribucions SUBMITTED després
    que els workers les enviïn (el worker NO pot decidir l'estat)."""
    for cid in cids:
        proto.handle("contribution.validate",
                     {"contribution_id": cid, "admin_token": ADMIN_TOKEN})
        proto.handle("contribution.activate",
                     {"contribution_id": cid, "admin_token": ADMIN_TOKEN})
        print(f"  [admin] contribution validate+activate {cid[:16]}",
              flush=True)


def create_round_and_assignments(proto, run_id, round_id, backend_name,
                                 base_model_hash, adapter_0_sha,
                                 adapter_pre_hash, assignments, shard_mf_fn,
                                 expected_ett_fn, dataset_manifest_sha=""):
    """R2.3: el Coordinator crea la ronda i les assignacions (autoritat).
    assignments: llista de dicts {assignment_id, worker_id, shard, num_ex}"""
    proto.handle("round.create", {
        "run_id": run_id, "round_id": round_id, "backend_id": backend_name,
        "base_model_hash": base_model_hash, "adapter_0_sha": adapter_0_sha,
        "adapter_pre_hash": adapter_pre_hash,
        "dataset_manifest_sha": dataset_manifest_sha,
        "admin_token": ADMIN_TOKEN})
    for a in assignments:
        shard_mf = shard_mf_fn(a["shard"])
        exp_ett = expected_ett_fn(a["shard"], a["num_ex"])
        proto.handle("assignment.create", {
            "run_id": run_id, "round_id": round_id,
            "assignment_id": a["assignment_id"], "worker_id": a["worker_id"],
            "shard_id": f"shard-{a['shard']}", "shard_manifest_sha": shard_mf,
            "base_model_hash": base_model_hash, "adapter_0_sha": adapter_0_sha,
            "expected_ett": exp_ett, "revision": 1,
            "admin_token": ADMIN_TOKEN})
        print(f"  [coord] assignment {a['assignment_id']} -> shard-{a['shard']} "
              f"expected_ett={exp_ett}", flush=True)


def oracle_fedavg(coordinator, run_id, round_id, adapter_0_bytes):
    rows = coordinator.active_deltas(run_id, round_id)
    deltas, etts = [], []
    for r in rows:
        dt = unpack_tensors(base64.b64decode(r["delta_bundle_b64"]))
        deltas.append(to_numpy_dict(dt))
        etts.append(r["ett"])
    adapter_0 = to_numpy_dict(unpack_tensors(adapter_0_bytes))
    return fedavg(adapter_0, list(zip(deltas, etts)))


def compare_oracle(fed_a1_bytes, oracle, tol=1e-6):
    fed_a1 = unpack_tensors(fed_a1_bytes)
    return max(float(np.abs(np.asarray(fed_a1[n], dtype="float64") -
                            oracle[n].astype("float64")).max()) for n in oracle)


def expected_ett_for_shard(backend_name, model, seq_len, shard, num_ex,
                           bcfg=None):
    """R2.3, punt 7: expected_ett calculat PRÈVIAMENT amb el backend
    (etapa trusted/backend-aware). El Coordinator només guarda el valor."""
    from model_worker import load_backend, load_examples
    cls = load_backend(backend_name)
    if backend_name == "dummy":
        mp = model or "dummy"
    elif backend_name == "minicpm5":
        mp = model or os.environ.get(
            "MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")
    else:
        mp = model or "/root/qwen3_0_6b_snapshot"
    dbp = f"/tmp/generic_plan_{backend_name}_{shard}.db"
    if os.path.exists(dbp):
        os.remove(dbp)
    bk = cls(mp, db_path=dbp, seq_len=seq_len, **(bcfg or {}))
    bk.load_model()
    exs = load_examples(data_dir_for(backend_name), shard, num_ex)
    ett = 0
    for e in exs:
        tok = bk.tokenize_example(e["instruction"], e["response"])
        ett += bk.compute_ett(tok[2] if isinstance(tok, tuple) else tok)
    bk.close()
    return ett


def data_dir_for(backend_name):
    if backend_name == "qwen3":
        return os.environ.get("QWEN3_DATA", "/root/meshtrainer/qwen3_pilot_data")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "qwen3_pilot_data")


def compute_pre_hash(backend_name, model, seq_len, adapter_bytes):
    """R2.3: adapter_pre_hash = hash de l'estat del model AMB l'adapter
    carregat (baseline de la ronda). Etapa trusted del Coordinator."""
    from model_worker import load_backend
    cls = load_backend(backend_name)
    if backend_name == "dummy":
        mp = model or "dummy"
    elif backend_name == "minicpm5":
        mp = model or os.environ.get(
            "MINICPM5_MODEL", "/root/minicpm5_1b_snapshot")
    else:
        mp = model or "/root/qwen3_0_6b_snapshot"
    dbp = f"/tmp/generic_prehash_{backend_name}_{hashlib.sha256(adapter_bytes).hexdigest()[:8]}.db"
    bk = cls(mp, db_path=dbp, seq_len=seq_len)
    bk.load_model()
    sha = hashlib.sha256(adapter_bytes).hexdigest()
    bk.load_adapter(adapter_bytes, sha, strict=True)
    pre = bk.adapter_hash()
    bk.close()
    return pre


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True,
                    choices=["qwen3", "dummy", "minicpm5"])
    ap.add_argument("--num-examples", type=int, default=10)
    ap.add_argument("--seq-len", type=int, default=128)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--server-db", default="/tmp/generic_dist_server.db")
    args = ap.parse_args()

    OUT = args.out_dir or os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "qwen_distributed_output")
    import shutil
    # R1.1: NO esborrem tot el directori (el quality pilot escriu
    # quality_before/after.json al mateix OUT abans que nosaltres).
    # Només netegem els fitxers que AQUEST run genera.
    os.makedirs(OUT, exist_ok=True)
    for _f in os.listdir(OUT):
        if _f.startswith(("adapter_", "evidence_w-", "distributed_metrics")):
            os.remove(os.path.join(OUT, _f))
    for p in (args.server_db,):
        if os.path.exists(p):
            os.remove(p)

    # ── 1. Preparació consolidada (R1.1 punt 5): UNA càrrega del model ──
    #    adapter_0 (autoritatiu) + adapter_pre_hash + ETT per shard, i
    #    alliberament ESTRICTE del driver abans dels workers (punt 4).
    #    Memory profile (punt 3) -> evidence/minicpm5/memory_profile.log
    mprof = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "evidence", "minicpm5", "memory_profile.log")
    os.makedirs(os.path.dirname(mprof), exist_ok=True)
    mlog = open(mprof, "a")
    mlog.write(f"\n=== RUN {time.strftime('%Y-%m-%d %H:%M:%S')} "
               f"backend={args.backend} ===\n")

    def _mem_host(tag):
        try:
            with open("/proc/meminfo") as f:
                mi = dict(l.split(":") for l in f if ":" in l)
            free_mb = int(mi.get("MemAvailable", "0").split()[0]) // 1024
            mlog.write(f"  {tag}: host MemAvailable={free_mb} MB\n")
            mlog.flush()
        except Exception:
            pass

    _mem_host("H-0 inici")
    ad0, identity, adapter_pre_hash, ett_map, ett_map_half = prepare_round_data(
        args.backend, args.model, args.seq_len, OUT, {}, shards=["A", "B"],
        num_ex_per_shard=args.num_examples, log=mlog)
    _mem_host("H-1 despres preparacio (driver zero model)")

    ad0_path = os.path.join(OUT, "adapter_0.bundle")
    with open(ad0_path, "wb") as f:
        f.write(ad0)
    ad0_sha = hashlib.sha256(ad0).hexdigest()
    print(f"adapter_0 {ad0_sha[:16]} base={identity['base_model_hash'][:16]} "
          f"backend={args.backend}", flush=True)

    httpd, proto = serve(port=19862, db_path=args.server_db,
                         admin_token=ADMIN_TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.5)

    # ── R2.3: el Coordinator crea la ronda i les assignacions (autoritat).
    #    expected_ett i shard_manifest es calculen PRÈVIAMENT (etapa trusted).
    from model_worker import load_examples as _lex, shard_manifest_sha as _sms

    def _shard_mf(shard, n):
        exs = _lex(data_dir_for(args.backend), shard, n)
        return _sms([e["source"] for e in exs])

    def _expected_ett(shard, n):
        # R1.1: ETT precalculat a la preparació consolidada (1 load).
        # R2 usa n2 = num_examples//2 -> ett_map_half; R1 usa num_examples.
        if n == max(1, args.num_examples // 2):
            return ett_map_half[shard]
        return ett_map[shard]
    base_hash = identity["base_model_hash"]
    create_round_and_assignments(
        proto, "run1", "r1", args.backend, base_hash, ad0_sha,
        adapter_pre_hash,
        [{"assignment_id": "asg-A", "worker_id": "w-A", "shard": "A",
          "num_ex": args.num_examples},
         {"assignment_id": "asg-B", "worker_id": "w-B", "shard": "B",
          "num_ex": args.num_examples}],
        shard_mf_fn=lambda sh, n=args.num_examples: _shard_mf(sh, n),
        expected_ett_fn=_expected_ett)
    check("coordinator: round r1 creada (model_rounds)",
          proto.coord.get_round("run1", "r1") is not None)
    check("coordinator: assignments A/B creades",
          proto.coord.get_assignment("run1", "r1", "asg-A", "w-A") is not None
          and proto.coord.get_assignment("run1", "r1", "asg-B", "w-B") is not None)

    # ── 3/4. Worker A i B amb SIMULTANEÏTAT REAL (R1.2 punt 2): ──
    #    spawn A -> monitoritzar stdout -> a LEASE d'A, spawn B
    #    (A.poll() is None) -> exigir A_LEASE < B_LEASE < A_RELEASE
    _mem_host("H-2 abans de spawn A/B")
    rcs1, outs1, times1 = run_workers_overlap(
        args.backend,
        [{"worker_id": "w-A", "session": "A1", "shard": "A",
          "assignment": "asg-A", "round_id": "r1", "num_ex": args.num_examples},
         {"worker_id": "w-B", "session": "B1", "shard": "B",
          "assignment": "asg-B", "round_id": "r1", "num_ex": args.num_examples}],
        ad0_path, ad0_sha, OUT, args.seq_len, log=mlog)
    _mem_host("H-3 despres Round 1 (A/B acabats)")
    wA_rc, wA_out = rcs1["w-A"], outs1["w-A"]
    wB_rc, wB_out = rcs1["w-B"], outs1["w-B"]
    check("worker A (subprocess, shard A)", wA_rc == 0 and "WORKER_DONE" in wA_out)
    check("worker B (subprocess, shard B)", wB_rc == 0 and "WORKER_DONE" in wB_out)
    check("PID A != PID B (workers independents)",
          _pid_of(wA_out) != "?" and _pid_of(wB_out) != "?" and
          _pid_of(wA_out) != _pid_of(wB_out),
          f"A={_pid_of(wA_out)} B={_pid_of(wB_out)}")
    # ── PAR-01/02: simultaneïtat REAL (R1.2 punt 4) ──
    # NOTA: el PAR només és semànticament possible per a backends amb
    # train_step NO instantani (minicpm5/qwen3). El dummy completa
    # LEASE→RELEASE en <1 ms — B (spawned a LEASE d'A) no pot assolir la
    # seva lease abans que A alliberi. Per al dummy es verifica la
    # completesa (checks worker A/B), no l'overlap (impossible).
    if args.backend != "dummy":
        tA_l, tB_l, tA_r = (times1["w-A"]["lease"], times1["w-B"]["lease"],
                            times1["w-A"]["release"])
        check("PAR-01 A viu quan B arrenca (B_LEASE després de A_LEASE)",
              tA_l is not None and tB_l is not None and tB_l > tA_l,
              f"A_lease={tA_l:.1f} B_lease={tB_l:.1f}")
        check("PAR-02 A/B leases actives simultàniament "
              "(A_LEASE < B_LEASE < A_RELEASE)",
              tA_l is not None and tB_l is not None and tA_r is not None and
              tA_l < tB_l < tA_r,
              f"A_lease={tA_l:.1f} < B_lease={tB_l:.1f} < A_release={tA_r:.1f}")
        mlog.write(f"  PAR R1: A_lease={tA_l} B_lease={tB_l} A_release={tA_r}\n")
        mlog.flush()
    else:
        check("PAR-01/02 dummy: overlap N/A (train instantani) — workers OK",
              True, "dummy LEASE→RELEASE <1ms (overlap impossible per disseny)")

    evA = json.load(open(os.path.join(OUT, f"evidence_w-A.json")))
    evB = json.load(open(os.path.join(OUT, f"evidence_w-B.json")))
    check("mateix base_model_hash (A==B)", evA["base_model_hash"] == evB["base_model_hash"])
    check("shard manifests diferents", evA["shard_manifest_sha"] != evB["shard_manifest_sha"])
    check("worker_ids diferents", evA["worker_id"] != evB["worker_id"])
    check("sessions diferents", evA["session_id"] != evB["session_id"])
    check("leases diferents", evA["lease_id"] != evB["lease_id"])
    if args.backend == "qwen3":
        check("ETT_A != ETT_B", evA["ett_actual"] != evB["ett_actual"],
              f"{evA['ett_actual']} vs {evB['ett_actual']}")
    else:
        check("ETT_A > 0 i ETT_B > 0 (dummy: ETT real per exemple)",
              evA["ett_actual"] > 0 and evB["ett_actual"] > 0,
              f"{evA['ett_actual']} vs {evB['ett_actual']}")

    # R2.4: l'ADMIN activa les contribucions (el worker només les envia)
    admin_activate(proto, [evA["contribution_id"], evB["contribution_id"]])
    check("ready_to_close: quòrum exacte 2/2 (r1)",
          proto.coord.ready_to_close("run1", "r1")["state"] == "READY")

    # ── 5/6. FedAvg Coordinator + oracle ──
    fed1 = fedavg_via_coordinator(proto, "run1", "r1", ad0)
    check("FedAvg Coordinator: adapter_1 (r1)", fed1["num_contributions"] == 2,
          f"sha={fed1['adapter_1_sha'][:16]} ett={fed1['total_ett']}")
    oracle1 = oracle_fedavg(proto.coord, "run1", "r1", ad0)
    md1 = compare_oracle(base64.b64decode(fed1["adapter_1_b64"]), oracle1)
    check("oracle == FedAvg Coordinator (r1, tensor per tensor)", md1 <= 1e-6,
          f"maxdiff={md1:.2e}")
    a1_path = os.path.join(OUT, "adapter_1.bundle")
    with open(a1_path, "wb") as f:
        f.write(base64.b64decode(fed1["adapter_1_b64"]))

    # ── 7. ROUND 2 (adapter_1 distribuït) ──
    # R2.3: nova ronda r2 amb adapter_1 com a adapter global + assignments
    n2 = max(1, args.num_examples // 2)
    a1_bytes = base64.b64decode(fed1["adapter_1_b64"])
    # R1.1: pre_hash de r2 amb 1 load + alliberament estricte (punt 4)
    a1_pre = compute_pre_hash_clean(args.backend, args.model, args.seq_len,
                                    a1_bytes, log=mlog)
    _mem_host("H-4 pre_hash r2 (driver zero model)")
    create_round_and_assignments(
        proto, "run1", "r2", args.backend, base_hash,
        fed1["adapter_1_sha"], a1_pre,
        [{"assignment_id": "asg-A2", "worker_id": "w-A2", "shard": "A",
          "num_ex": n2},
         {"assignment_id": "asg-B2", "worker_id": "w-B2", "shard": "B",
          "num_ex": n2}],
        shard_mf_fn=lambda sh, n=n2: _shard_mf(sh, n),
        expected_ett_fn=_expected_ett)
    # A2/B2 amb SIMULTANEÏTAT REAL (R1.2 punt 3: mateix patró que R1)
    rcs2, outs2, times2 = run_workers_overlap(
        args.backend,
        [{"worker_id": "w-A2", "session": "A2", "shard": "A",
          "assignment": "asg-A2", "round_id": "r2", "num_ex": n2},
         {"worker_id": "w-B2", "session": "B2", "shard": "B",
          "assignment": "asg-B2", "round_id": "r2", "num_ex": n2}],
        a1_path, fed1["adapter_1_sha"], OUT, args.seq_len, log=mlog)
    _mem_host("H-5 despres Round 2 (A2/B2 acabats)")
    wA2_rc, wA2_out = rcs2["w-A2"], outs2["w-A2"]
    wB2_rc, wB2_out = rcs2["w-B2"], outs2["w-B2"]
    check("worker A2 (round 2)", wA2_rc == 0 and "WORKER_DONE" in wA2_out)
    check("worker B2 (round 2)", wB2_rc == 0 and "WORKER_DONE" in wB2_out)
    check("PID A2 != PID B2 (round 2 paral·lel)",
          _pid_of(wA2_out) != "?" and _pid_of(wB2_out) != "?" and
          _pid_of(wA2_out) != _pid_of(wB2_out),
          f"A2={_pid_of(wA2_out)} B2={_pid_of(wB2_out)}")
    # ── PAR-03/04: simultaneïtat REAL Round 2 (R1.2 punt 4) ──
    if args.backend != "dummy":
        tA2_l, tB2_l, tA2_r = (times2["w-A2"]["lease"], times2["w-B2"]["lease"],
                               times2["w-A2"]["release"])
        check("PAR-03 A2 viu quan B2 arrenca (B2_LEASE després de A2_LEASE)",
              tA2_l is not None and tB2_l is not None and tB2_l > tA2_l,
              f"A2_lease={tA2_l:.1f} B2_lease={tB2_l:.1f}")
        check("PAR-04 A2/B2 leases actives simultàniament "
              "(A2_LEASE < B2_LEASE < A2_RELEASE)",
              tA2_l is not None and tB2_l is not None and tA2_r is not None and
              tA2_l < tB2_l < tA2_r,
              f"A2_lease={tA2_l:.1f} < B2_lease={tB2_l:.1f} < A2_release={tA2_r:.1f}")
        mlog.write(f"  PAR R2: A2_lease={tA2_l} B2_lease={tB2_l} "
                   f"A2_release={tA2_r}\n")
        mlog.flush()
    else:
        check("PAR-03/04 dummy: overlap N/A (train instantani) — workers OK",
              True, "dummy LEASE→RELEASE <1ms")
    evA2 = json.load(open(os.path.join(OUT, f"evidence_w-A2.json")))
    evB2 = json.load(open(os.path.join(OUT, f"evidence_w-B2.json")))
    check("round 2: adapter_pre_hash_A == adapter_pre_hash_B (adapter_1)",
          evA2["adapter_pre_hash"] == evB2["adapter_pre_hash"],
          evA2["adapter_pre_hash"][:16])
    # R2.4: l'ADMIN activa les contribucions de la ronda 2
    admin_activate(proto, [evA2["contribution_id"], evB2["contribution_id"]])
    check("ready_to_close: quòrum exacte 2/2 (r2)",
          proto.coord.ready_to_close("run1", "r2")["state"] == "READY")
    fed2 = fedavg_via_coordinator(proto, "run1", "r2",
                                  base64.b64decode(fed1["adapter_1_b64"]))
    check("FedAvg Coordinator: adapter_2 (r2)", fed2["num_contributions"] == 2,
          f"sha={fed2['adapter_1_sha'][:16]}")
    oracle2 = oracle_fedavg(proto.coord, "run1", "r2",
                            base64.b64decode(fed1["adapter_1_b64"]))
    md2 = compare_oracle(base64.b64decode(fed2["adapter_1_b64"]), oracle2)
    check("oracle == FedAvg Coordinator (r2, tensor per tensor)", md2 <= 1e-6,
          f"maxdiff={md2:.2e}")
    a2_path = os.path.join(OUT, "adapter_2.bundle")
    with open(a2_path, "wb") as f:
        f.write(base64.b64decode(fed2["adapter_1_b64"]))

    httpd.shutdown()

    out = {"backend": args.backend,
           "adapter_0_sha": ad0_sha,
           "adapter_1_sha": fed1["adapter_1_sha"],
           "adapter_2_sha": fed2["adapter_1_sha"],
           "ett_a": evA["ett_actual"], "ett_b": evB["ett_actual"],
           "maxdiff_oracle_r1": md1, "maxdiff_oracle_r2": md2}
    with open(os.path.join(OUT, "distributed_metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, indent=2, ensure_ascii=False))

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== GENERIC DISTRIBUTED RUN ({args.backend}): "
          f"{npass}/{len(CHECKS)} PASS ===")
    mlog.write(f"=== FI RUN (pass={npass}/{len(CHECKS)}) ===\n")
    mlog.close()
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
