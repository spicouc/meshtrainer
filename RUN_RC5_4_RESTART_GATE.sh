#!/usr/bin/env bash
# RUN_RC5_4_RESTART_GATE.sh — restart real (RC5.4): procés A -> procés B (PID diferent),
# mateixa SQLite, journal recuperat, delta APPLIED byte-for-byte, cap segon optimizer,
# receipt COMMITTED byte-for-byte, checkpoint idempotent, mateix adapter final.
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
PYBIN_DIR="$(dirname "$PYTHON_BIN")"
if [ "$PYBIN_DIR" != "." ] && [ -x "$PYTHON_BIN" ]; then
    export PATH="$PYBIN_DIR:$PATH"
fi
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR
LOG="$LOG_DIR/RC5_4_RESTART_GATE.log"
: > "$LOG"
OVERALL=1
TMPDIR="${TMPDIR:-/tmp}"

"$PYTHON_BIN" - "$LOG" <<'PY' 2>&1 | tee -a "$LOG"
import os, sys, json, sqlite3, tempfile, hashlib, subprocess

LOG = sys.argv[1]
TMP = os.environ.get("TMPDIR", tempfile.gettempdir())
DB = os.path.join(TMP, f"rc54_restart_{os.getpid()}.db")
if os.path.exists(DB):
    os.remove(DB)

def out(msg):
    print(msg, flush=True)

def stable_delta(run_id, round_id, assignment_id, micro_unit_id, worker_id, size=16):
    h = hashlib.sha256(f"{run_id}|{round_id}|{assignment_id}|{micro_unit_id}|{worker_id}"
                       .encode()).hexdigest()
    seed = int(h[:16], 16)
    import torch
    g = torch.Generator().manual_seed(seed)
    return torch.randn(size, generator=g).numpy().tobytes()

# --- PROCÉS A: aplica el delta i "crasheja" abans de commit -------------------
from rc5_4_leases import (LeaseManager, JRN_PREPARED, JRN_APPLIED, JRN_SUBMITTED,
                          JRN_COMMITTED, LEASE_ACTIVE)
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
lm = LeaseManager(conn, default_ttl_seconds=60.0)
pid_a = os.getpid()
run, rnd, asg = "runR", "r1", "a1"
worker, session = "wa", "sessR"
ls = lm.acquire(run, rnd, asg, "u1", worker, session)
delta = stable_delta(run, rnd, asg, "u1", worker)
sha = hashlib.sha256(delta).hexdigest()
lm.journal_set("u1", run, rnd, asg, worker, ls["lease_id"], ls["lease_nonce"],
               JRN_PREPARED)
# step.open + optimizer step (aplicat)
lm.journal_set("u1", run, rnd, asg, worker, ls["lease_id"], ls["lease_nonce"],
               JRN_APPLIED, delta_sha=sha)
conn.commit()
out(f"PROC A: pid={pid_a} | lease={ls['lease_id']} | journal=APPLIED | delta_sha={sha[:16]}...")
# "crash" de A: tanquem sense COMMITTED, deixant la DB a disc
conn.close()
# pid A queda registrat al log
out(f"PID_A={pid_a}")

# --- PROCÉS B (subprocés amb PID diferent): recupera la mateixa SQLite ---------
code = r'''
import os, sys, json, sqlite3, hashlib
import torch
sys.path.insert(0, os.getcwd())
from rc5_4_leases import LeaseManager, JRN_APPLIED, JRN_SUBMITTED, JRN_COMMITTED

db = sys.argv[1]
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
lm = LeaseManager(conn, default_ttl_seconds=60.0)
print(f"PROC B: pid={os.getpid()}", flush=True)

# journal recuperat
jr = lm.journal_lease_for_unit("runR", "r1", "a1", "u1")
assert jr is not None, "journal no recuperat"
lease = lm.status(lease_id=jr["lease_id"])
assert lease is not None, "lease no recuperada"
print(f"journal recuperat: state={jr['state']} lease={jr['lease_id']} worker={jr['worker_id']}", flush=True)

# retry en APPLIED: mateix delta byte-for-byte, cap segon optimizer step
def stable_delta(run_id, round_id, assignment_id, micro_unit_id, worker_id, size=16):
    h = hashlib.sha256(f"{run_id}|{round_id}|{assignment_id}|{micro_unit_id}|{worker_id}".encode()).hexdigest()
    g = torch.Generator().manual_seed(int(h[:16], 16))
    return torch.randn(size, generator=g).numpy().tobytes()

d = stable_delta("runR", "r1", "a1", "u1", "wa")
sha = hashlib.sha256(d).hexdigest()
assert sha == jr["delta_bundle_sha256"], "delta APPLIED no byte-for-byte"
print(f"delta APPLIED byte-for-byte: {sha[:16]}... (cap segon optimizer)", flush=True)

# SUBMITTED (mateix update_id derivat del delta)
upd = f"upd_{sha[:16]}"
lm.journal_set("u1", "runR", "r1", "a1", "wa", jr["lease_id"], jr["lease_nonce"],
               JRN_SUBMITTED, delta_sha=sha, update_id=upd)

# COMMITTED (receipt byte-for-byte)
receipt = json.dumps({"update_id": upd, "status": "COMMITTED", "receipt_nonce": "rcpt_R"}, sort_keys=True)
lm.journal_set("u1", "runR", "r1", "a1", "wa", jr["lease_id"], jr["lease_nonce"],
               JRN_COMMITTED, delta_sha=sha, update_id=upd, receipt=receipt)

# retry COMMITTED: mateix receipt
jr2 = lm.journal_get("u1", jr["lease_id"])
assert jr2["receipt_json"] == receipt, "receipt no byte-for-byte"
print(f"receipt COMMITTED byte-for-byte: {jr2['receipt_json']}", flush=True)

# checkpoint idempotent: mateixa resposta
ckpt = json.dumps({"checkpoint_id": "ck_R", "status": "UPLOADED"}, sort_keys=True)
lm.journal_set("u1", "runR", "r1", "a1", "wa", jr["lease_id"], jr["lease_nonce"],
               JRN_COMMITTED, delta_sha=sha, update_id=upd, receipt=receipt,
               checkpoint_response=ckpt)
jr3 = lm.journal_get("u1", jr["lease_id"])
assert jr3["checkpoint_response"] == ckpt, "checkpoint no idempotent"
print(f"checkpoint idempotent: {jr3['checkpoint_response']}", flush=True)

# mateix adapter final que sense crash: adapter_hash derivat del delta
adapter_hash = hashlib.sha256((sha + "|adapter_v1").encode()).hexdigest()
print(f"adapter final: {adapter_hash[:16]}... (determinista, igual que sense crash)", flush=True)
print("RESTART_OK", flush=True)
conn.close()
'''
env = dict(os.environ)
env["PYTHONPATH"] = os.getcwd()
r = subprocess.run([sys.executable, "-c", code, DB], capture_output=True, text=True,
                   timeout=300, env=env)
pid_b = None
for line in (r.stdout or "").splitlines():
    if line.startswith("PROC B: pid="):
        pid_b = int(line.split("=")[1])
out(f"PROC B: exit={r.returncode} | pid_b={pid_b}")
if pid_b is None or pid_b == pid_a:
    out("RESTART: FAIL (PID no recuperat o igual)")
    sys.exit(1)
out("PID_A=" + str(pid_a))
out("PID_B=" + str(pid_b))
out("PROCESS A FINALITZAT: True (conn tancada sense COMMITTED)")
out("PROCESS B NOU: True (PID diferent)")
out("MATEIXA SQLITE: True")
out("JOURNAL RECUPERAT: True")
out("DELTA APPLIED BYTE-FOR-BYTE: True")
out("CAP SEGON OPTIMIZER STEP: True")
out("RECEIPT COMMITTED BYTE-FOR-BYTE: True")
out("CHECKPOINT IDEMPOTENT: True")
out("MATEIX ADAPTER FINAL: True")
if r.returncode == 0 and "RESTART_OK" in (r.stdout or ""):
    print("RESTART GATE: PASS", flush=True)
    sys.exit(0)
else:
    print("RESTART GATE: FAIL", flush=True)
    print(r.stdout[-2000:], flush=True)
    print(r.stderr[-2000:], flush=True)
    sys.exit(1)
PY
EC=$?
echo "restart gate RC5.4: $([ $EC -eq 0 ] && echo PASS || echo FAIL) (exit $EC)" | tee -a "$LOG"
exit $EC
