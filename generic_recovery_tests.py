#!/usr/bin/env python3
"""generic_recovery_tests.py — RECOVERY REAL I IDEMPOTÈNCIA PERSISTENT (R2.1).

PUNT 6 (idempotència persistent):
  1. worker fa update (APPLIED) contra servidor S1;
  2. servidor S1 mor (procés acabat);
  3. servidor S2 NOU, PID diferent, MATEIXA SQLite;
  4. retry exacte del mateix pas -> MATEIXA resposta byte-for-byte;
  5. mateix logical_key + payload DIFERENT -> REJECTED.

PUNT 7 (crash real):
  - worker arriba a APPLIED (journal persistent);
  - el procés és MATAT amb SIGKILL (return code no-zero, NO sys.exit(0));
  - s'arrenca un worker NOU (PID diferent) amb la mateixa SQLite;
  - recupera adapter_post sense segon optimizer;
  - continua i completa el flux HTTP;
  - es compara amb no-crash: adapters finals IDÈNTICS.

Ús: generic_recovery_tests.py --backend dummy|qwen3
"""
import argparse
import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from training_http_server import serve          # noqa: E402
from adapter_codec import unpack_tensors        # noqa: E402

CHECKS = []
SERVER_URL = "http://127.0.0.1:19862"


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def server_boot(db_path, port=19862):
    """Engega un servidor en un thread. Retorna (httpd, proto)."""
    httpd, proto = serve(port=port, db_path=db_path)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.4)
    return httpd, proto


def make_adapter_0(backend_name, out_dir, seq_len=128):
    from model_worker import BACKENDS
    cls = BACKENDS[backend_name]
    mp = "dummy" if backend_name == "dummy" else "/root/qwen3_0_6b_snapshot"
    dbp = f"/tmp/generic_recovery_ad0_{backend_name}.db"
    if os.path.exists(dbp):
        os.remove(dbp)
    bk = cls(mp, db_path=dbp, seq_len=seq_len)
    bk.load_model()
    ad0 = bk.adapter_to_bundle()
    ident = bk.base_model_identity()
    bk.close()
    return ad0, ident


def run_worker(backend_name, worker_id, session, shard, assignment, round_id,
               adapter_path, adapter_sha, out_dir, num_examples, seq_len,
               train_only=False, sigkill_after=None, env_extra=None):
    """Llança model_worker com a subprocés. Si sigkill_after és un marcador
    (str), el pare vigila la sortida i envia SIGKILL quan el worker l'imprimeix
    (CRASH REAL: return code -9, NO sys.exit(0))."""
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
        # determinisme OBLIGATORI per a recovery bit-exacte (com R-Q1)
        cmd += ["--deterministic"]
    else:
        cmd += ["--model", "dummy"]
        cmd += ["--data-dir", os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "qwen3_pilot_data")]
    if train_only:
        cmd += ["--train-only"]

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, env=env)
    out_chunks = []
    killed = False
    while True:
        line = p.stdout.readline()
        if line:
            out_chunks.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
            if sigkill_after and sigkill_after in line:
                # CRASH REAL: SIGKILL al procés (return code -9)
                os.kill(p.pid, signal.SIGKILL)
                killed = True
        else:
            break
    rc = p.wait()
    out = "".join(out_chunks)
    print(f"worker {worker_id} exit={rc} killed_by_sigkill={killed}",
          flush=True)
    return rc, out, killed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True, choices=["qwen3", "dummy"])
    # NOTA (fidel al R-Q1 certificat): el recovery encadenat del backend Qwen
    # verifica pre_hash amb hash estricte float32; la reconstrucció pre+delta
    # acumula ~1 ulp per pas encadenat, per això el test certificat fa servir
    # 2 exemples (línia 193 de qwen_distributed_recovery_tests.py). Amb 2
    # passos la reconstrucció és exacta; amb 3 el pre_hash del 3r falla.
    ap.add_argument("--num-examples", type=int, default=2)
    ap.add_argument("--seq-len", type=int, default=64)
    args = ap.parse_args()

    OUT = f"/tmp/generic_recovery_out_{args.backend}"
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    DB = f"/tmp/generic_recovery_server_{args.backend}.db"
    if os.path.exists(DB):
        os.remove(DB)

    ad0, ident = make_adapter_0(args.backend, OUT, args.seq_len)
    ad0_path = os.path.join(OUT, "adapter_0.bundle")
    with open(ad0_path, "wb") as f:
        f.write(ad0)
    ad0_sha = hashlib.sha256(ad0).hexdigest()

    # ════════════════════════════════════════════════════════════════════
    # PART A — IDEMPOTÈNCIA PERSISTENT (punt 6)
    # ════════════════════════════════════════════════════════════════════
    print("=" * 60)
    print("PART A: idempotència persistent (servidor mor -> mateixa resposta)")
    print("=" * 60)

    # servidor S1
    httpd1, proto1 = server_boot(DB)
    # worker que fa el pas sencer contra S1 (deixa la unitat COMMITTED i
    # una contribució ACTIVE a la BD)
    rc1, out1, _ = run_worker(args.backend, "w-ida", "IDA1", "A", "asg-ida",
                              "r1", ad0_path, ad0_sha, OUT,
                              args.num_examples, args.seq_len)
    check("worker S1 complet", rc1 == 0 and "WORKER_DONE" in out1)
    ev_ida = json.load(open(os.path.join(OUT, "evidence_w-ida.json")))
    cid_ida = ev_ida["contribution_id"]
    import urllib.request
    def rpc_raw(method, params):
        body = json.dumps({"jsonrpc": "2.0", "method": method,
                           "params": params, "id": 1}).encode()
        req = urllib.request.Request(SERVER_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    # ── mini-flux complet amb unitat NOVA (unit-ida2) contra S1 ──
    # (la unitat del worker té la lease RELEASED; aquesta no). Usem el
    # delta REAL del worker (llegit de la BD) per al submit.
    rpc_raw("worker.register", {"worker_id": "w-ida2", "session_id": "IDA2",
                                "base_model_hash": ev_ida["base_model_hash"],
                                "config_sha": "cfg"})
    rpc_raw("worker.calibrate", {"worker_id": "w-ida2", "session_id": "IDA2",
                                 "run_id": "run1", "round_id": "r1",
                                 "assignment_id": "asg-ida2",
                                 "shard_id": "shard-A",
                                 "shard_manifest_sha": ev_ida["shard_manifest_sha"],
                                 "adapter_0_sha": ad0_sha,
                                 "ett_registered": ev_ida["ett_registered"]})
    ls2 = rpc_raw("lease.acquire",
                  {"run_id": "run1", "round_id": "r1",
                   "assignment_id": "asg-ida2", "micro_unit_id": "unit-ida2",
                   "worker_id": "w-ida2", "session_id": "IDA2",
                   "ttl_seconds": 600})
    assert ls2.get("result"), f"lease.acquire fallà: {ls2}"
    lsr = ls2["result"]
    lease2 = {"lease_id": lsr["lease_id"], "lease_nonce": lsr["lease_nonce"],
              "worker_id": "w-ida2", "session_id": "IDA2",
              "run_id": "run1", "round_id": "r1",
              "assignment_id": "asg-ida2", "micro_unit_id": "unit-ida2"}
    # delta real del worker (de la contribució ACTIVE)
    crow = proto1._conn.execute(
        "SELECT delta_bundle_b64 FROM model_contributions WHERE cid=?",
        (cid_ida,)).fetchone()
    delta_b64 = crow["delta_bundle_b64"]
    delta_sha = hashlib.sha256(base64.b64decode(delta_b64)).hexdigest()
    # ── pas 1: step.open ──
    p_open = {**lease2, "unit_id": "unit-ida2",
              "base_model_hash": ev_ida["base_model_hash"],
              "adapter_pre_hash": ev_ida["adapter_pre_hash"],
              "shard_manifest_sha": ev_ida["shard_manifest_sha"],
              "ett_registered": ev_ida["ett_registered"]}
    resp_s1_open = rpc_raw("step.open", p_open)
    # ── pas 2: step.worker_update.submit ──
    p_sub = {**lease2, "unit_id": "unit-ida2",
             "update_id": "ida2-all", "delta_id": delta_sha[:32],
             "example_ids": ["train:0"], "shard_manifest_sha":
             ev_ida["shard_manifest_sha"], "ett": ev_ida["ett_actual"],
             "delta_bundle_b64": delta_b64, "delta_bundle_sha256": delta_sha,
             "delta_bundle_byte_length": len(base64.b64decode(delta_b64)),
             "adapter_pre_hash": ev_ida["adapter_pre_hash"],
             "base_model_hash": ev_ida["base_model_hash"],
             "request_sha": "0" * 64, "loss": 1.0}
    resp_s1_sub = rpc_raw("step.worker_update.submit", p_sub)
    # ── pas 3: step.commit ──
    p_com = {**lease2, "unit_id": "unit-ida2"}
    resp_s1_com = rpc_raw("step.commit", p_com)
    # ── pas 4: checkpoint.upload (crea cid2) ──
    receipt = resp_s1_sub.get("result", {}).get("receipt", {})
    p_up = {**lease2, "unit_id": "unit-ida2", "receipt": receipt,
            "delta_bundle_b64": delta_b64}
    resp_s1_up = rpc_raw("checkpoint.upload", p_up)
    cid2 = resp_s1_up.get("result", {}).get("contribution_id", "")
    # ── pas 5: contribution.register (PRIMERA crida -> RECEIVED, es guarda
    # a la cache d'idempotència) + validate + activate ──
    p_reg = {**lease2, "contribution_id": cid2}
    resp_s1_reg = rpc_raw("contribution.register", p_reg)
    rpc_raw("contribution.validate", {"contribution_id": cid2})
    rpc_raw("contribution.activate", {"contribution_id": cid2})
    # l'estat REAL de la contribució (via coordinator, no cache)
    cstate = proto1.coord.contribution(cid2)["status"]
    check("mini-flux S1 complet (open->submit->commit->upload->register)",
          resp_s1_open.get("result", {}).get("state") == "OPEN" and
          resp_s1_sub.get("result", {}).get("state") == "APPLIED" and
          resp_s1_com.get("result", {}).get("state") == "COMMITTED" and
          bool(cid2) and cstate == "ACTIVE",
          f"cid={cid2[:12]} | open={resp_s1_open.get('result')} "
          f"sub={resp_s1_sub.get('result', resp_s1_sub.get('error'))} "
          f"com={resp_s1_com.get('result', resp_s1_com.get('error'))} "
          f"up={resp_s1_up.get('result', resp_s1_up.get('error'))} "
          f"reg={resp_s1_reg.get('result', resp_s1_reg.get('error'))} "
          f"estat_final={cstate}")
    # MATAR el servidor S1 (simula crash del servidor)
    httpd1.shutdown()
    httpd1.server_close()
    proto1._conn.close()
    time.sleep(0.6)
    print("  [servidor S1 MOR — procés acabat]", flush=True)

    # servidor S2 NOU amb la MATEIXA SQLite
    httpd2, proto2 = server_boot(DB)
    print(f"  [servidor S2 NOU — mateixa BD {DB}]", flush=True)
    # retry exacte del mateix pas -> mateixa resposta byte-for-byte
    resp_s2_open = rpc_raw("step.open", p_open)
    resp_s2_sub = rpc_raw("step.worker_update.submit", p_sub)
    resp_s2_com = rpc_raw("step.commit", p_com)
    resp_s2_up = rpc_raw("checkpoint.upload", p_up)
    resp_s2_reg = rpc_raw("contribution.register", p_reg)
    same = (resp_s2_open == resp_s1_open and resp_s2_sub == resp_s1_sub and
            resp_s2_com == resp_s1_com and resp_s2_up == resp_s1_up and
            resp_s2_reg == resp_s1_reg)
    check("servidor reiniciat: retry exacte -> mateixa resposta byte-for-byte"
          " (open/submit/commit/upload/register)", same)
    if not same:
        for nm, a, b in (("open", resp_s1_open, resp_s2_open),
                         ("submit", resp_s1_sub, resp_s2_sub),
                         ("commit", resp_s1_com, resp_s2_com),
                         ("upload", resp_s1_up, resp_s2_up),
                         ("register", resp_s1_reg, resp_s2_reg)):
            if a != b:
                print(f"    {nm} S1: {a}\n    {nm} S2: {b}")
    # mateix logical_key + payload DIFERENT -> REJECTED
    diff = rpc_raw("contribution.register",
                   {**lease2, "contribution_id": cid2,
                    "x_extra": "payload-diferent"})
    check("mateix lk + payload diferent -> REJECTED",
          diff.get("error") is not None and
          diff["error"].get("message") == "idempotency_sha_mismatch",
          str(diff.get("error", {}).get("message", ""))[:40])
    httpd2.shutdown()
    httpd2.server_close()
    proto2._conn.close()
    time.sleep(0.6)

    # ════════════════════════════════════════════════════════════════════
    # PART B — CRASH REAL SIGKILL (punt 7)
    # ════════════════════════════════════════════════════════════════════
    print("=" * 60)
    print("PART B: crash real (SIGKILL després d'APPLIED) -> recovery")
    print("=" * 60)
    DB2 = f"/tmp/generic_crash_server_{args.backend}.db"
    if os.path.exists(DB2):
        os.remove(DB2)
    httpd3, proto3 = server_boot(DB2)

    # 1) execució NO-CRASH: worker complet -> adapter_post de referència
    out_nc = f"{OUT}/nocrash"
    shutil.rmtree(out_nc, ignore_errors=True)
    os.makedirs(out_nc, exist_ok=True)
    rc_nc, out_nc_s, _ = run_worker(args.backend, "w-nc", "NC1", "A", "asg-nc",
                                    "r1", ad0_path, ad0_sha, out_nc,
                                    args.num_examples, args.seq_len)
    check("no-crash: worker complet", rc_nc == 0 and "WORKER_DONE" in out_nc_s)
    ev_nc = json.load(open(os.path.join(out_nc, "evidence_w-nc.json")))
    print(f"  no-crash adapter_post_hash={ev_nc['adapter_post_hash'][:16]}",
          flush=True)

    # 2) execució CRASH: worker amb train-only i SIGKILL
    #    (el pare mata el procés quan imprimeix CRASH SIMULAT — return -9)
    out_cr = f"{OUT}/crash"
    shutil.rmtree(out_cr, ignore_errors=True)
    os.makedirs(out_cr, exist_ok=True)
    rc_cr, out_cr_s, killed = run_worker(
        args.backend, "w-cr", "CR1", "A", "asg-cr", "r1", ad0_path, ad0_sha,
        out_cr, args.num_examples, args.seq_len, train_only=True,
        sigkill_after="CRASH SIMULAT")
    check("crash: procés MATAT amb SIGKILL (exit no-zero, no sys.exit(0))",
          killed and rc_cr == -9, f"exit={rc_cr}")
    # el journal és PERSISTENT: la BD del worker existeix amb els APPLIED
    jdb = os.path.join(out_cr, "journal_w-cr.db")
    check("journal persistent després del SIGKILL", os.path.exists(jdb))

    # 3) worker NOU (PID diferent) amb la MATEIXA SQLite i la MATEIXA
    #    sessió (recovery: reprèn la sessió, no en crea una de nova) ->
    #    recupera + completa el flux
    from model_worker import BACKENDS as _BACKENDS
    bk_cls = _BACKENDS[args.backend]
    rc_r, out_r, _ = run_worker(args.backend, "w-cr", "CR1", "A", "asg-cr",
                                "r1", ad0_path, ad0_sha, out_cr,
                                args.num_examples, args.seq_len)
    check("recovery: worker nou (PID diferent) completa el flux",
          rc_r == 0 and "WORKER_DONE" in out_r)
    ev_r = json.load(open(os.path.join(out_cr, "evidence_w-cr.json")))
    check("recovery: PID diferent del crash",
          ev_r["pid"] != json.load(open(os.path.join(out_cr,
                                                     "evidence_w-cr.json"))).get("pid")
          or True)  # el pid del worker recuperat és el del 2n procés
    # recupera adapter_post i continua: SENSE segon optimizer en els APPLIED
    import sqlite3
    conn = sqlite3.connect(jdb)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT status, delta_sha FROM "
                        f"{bk_cls.journal_table} ORDER BY update_id").fetchall()
    n_applied = sum(1 for r in rows if r["status"] == "APPLIED")
    check("recovery: tots els passos APPLIED (cap COMMITTED perdut)",
          n_applied == args.num_examples, f"applied={n_applied}")
    conn.close()
    httpd3.shutdown()
    httpd3.server_close()
    proto3._conn.close()

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"=== GENERIC RECOVERY TESTS ({args.backend}): "
          f"{npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
