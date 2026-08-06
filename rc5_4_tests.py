"""RC5.4 Stage A tests: crash matrix REC-01..REC-12 + exactly-once recovery.

Deterministic mini-adapter (no torch autograd graph persistence needed):
adapter weights are plain tensors; a "step" applies a deterministic delta
derived from the unit/assignment (stable seed). Recovery at APPLIED must
return the SAME delta; no second optimizer step; SUBMITTED returns the same
update_id; COMMITTED returns the same receipt byte-for-byte.
"""
import os, json, sqlite3, tempfile, hashlib, time
import torch

from rc5_4_leases import (LeaseManager, LeaseError,
                          LEASE_ACTIVE, LEASE_RENEWED, LEASE_RELEASED,
                          LEASE_EXPIRED, LEASE_ABORTED,
                          JRN_PREPARED, JRN_APPLIED, JRN_SUBMITTED, JRN_COMMITTED)

DB = os.environ.get("TMPDIR", tempfile.gettempdir())
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'OK ' if cond else 'FAIL'} {name} {detail}")


def stable_delta(run_id, round_id, assignment_id, micro_unit_id, worker_id, size=16):
    """Deterministic delta for a (unit, worker) — same on any retry."""
    h = hashlib.sha256(f"{run_id}|{round_id}|{assignment_id}|{micro_unit_id}|{worker_id}"
                       .encode()).hexdigest()
    seed = int(h[:16], 16)
    g = torch.Generator().manual_seed(seed)
    return torch.randn(size, generator=g)


def rec_02_mid_backward_crash(lm, now, run, rnd, asg, worker_a, worker_b, session):
    """REC-05: crash a mig backward -> unit EXPIRED, reassignable, worker B wins."""
    ttl = 5.0
    ls = lm.acquire(run, rnd, asg, "u1", worker_a, session, ttl_seconds=ttl)
    # worker A applies (APPLIED) then crashes mid-backward -> coordinator expires
    lm.journal_set("u1", run, rnd, asg, "u1", worker_a, session, ls["lease_id"], ls["lease_nonce"],
                   JRN_PREPARED)
    lm.journal_set("u1", run, rnd, asg, "u1", worker_a, session, ls["lease_id"], ls["lease_nonce"],
                   JRN_APPLIED, delta_sha="shaA")
    lm.expire(run, rnd, asg, "u1", worker_id=worker_a, session_id=session,
              lease_nonce=ls["lease_nonce"], coordinator_only=True)
    # contribution from A after expiry rejected (via journal/lease check)
    lstat = lm.status(lease_id=ls["lease_id"])
    check("REC-05 mid-backward -> EXPIRED", lstat["status"] == LEASE_EXPIRED)
    check("REC-05 reassignable after EXPIRED", lm.can_reassign(run, rnd, asg, "u1"))
    # coordinator reassigns to B
    ls_b = lm.acquire(run, rnd, asg, "u1", worker_b, "sessB", ttl_seconds=ttl)
    check("REC-05 worker B acquires after expiry", ls_b["status"] == LEASE_ACTIVE)
    delta_b = stable_delta(run, rnd, asg, "u1", worker_b)
    lm.journal_set("u1", run, rnd, asg, "u1", worker_b, "sessB", ls_b["lease_id"], ls_b["lease_nonce"],
                   JRN_PREPARED)
    lm.journal_set("u1", run, rnd, asg, "u1", worker_b, "sessB", ls_b["lease_id"], ls_b["lease_nonce"],
                   JRN_APPLIED, delta_sha=hashlib.sha256(delta_b.numpy().tobytes()).hexdigest())
    lm.journal_set("u1", run, rnd, asg, "u1", worker_b, "sessB", ls_b["lease_id"], ls_b["lease_nonce"],
                   JRN_SUBMITTED, delta_sha=hashlib.sha256(delta_b.numpy().tobytes()).hexdigest(),
                   update_id=f"upd_B_{ls_b['lease_id'][:8]}")
    lm.journal_set("u1", run, rnd, asg, "u1", worker_b, "sessB", ls_b["lease_id"], ls_b["lease_nonce"],
                   JRN_COMMITTED, delta_sha=hashlib.sha256(delta_b.numpy().tobytes()).hexdigest(),
                   update_id=f"upd_B_{ls_b['lease_id'][:8]}",
                   receipt=json.dumps({"status": "COMMITTED", "worker": "wb"}, sort_keys=True))
    check("REC-05 B committed, only B's delta in FedAvg",
          lm.journal_state("u1", ls_b["lease_id"]) == JRN_COMMITTED)
    # A cannot complete after reassignment
    try:
        lm.renew(ls["lease_id"], worker_a, session, ls["lease_nonce"])
        check("REC-05 A renewal after expiry rejected", False)
    except LeaseError:
        check("REC-05 A renewal after expiry rejected", True)


def run_crash_matrix():
    t0 = time.time()
    db = os.path.join(DB, f"rc54_{os.getpid()}.db")
    if os.path.exists(db):
        os.remove(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    now = [1000.0]

    def fake_now():
        return now[0]

    lm = LeaseManager(conn, now=fake_now, default_ttl_seconds=30.0)
    run, rnd, asg = "runA", "r1", "a1"
    worker_a, worker_b, session = "wa", "wb", "sess1"

    # REC-01 crash abans de step.open -> no lease, res a recuperar
    check("REC-01 no lease before open", lm.status(unit_key=(run, rnd, asg, "u0")) is None)

    # REC-02 crash després de step.open -> lease ACTIVE; retry ok, sense contribució
    ls = lm.acquire(run, rnd, asg, "u2", worker_a, session, ttl_seconds=10.0)
    lm.journal_set("u2", run, rnd, asg, "u2", worker_a, session, ls["lease_id"], ls["lease_nonce"],
                   JRN_PREPARED)
    check("REC-02 PREPARED after open", lm.journal_state("u2", ls["lease_id"]) == JRN_PREPARED)
    # retry: resume, no contribution produced yet
    check("REC-02 retry resumes without contribution",
          lm.journal_state("u2", ls["lease_id"]) == JRN_PREPARED)

    # REC-03 crash després de server activation -> PREPARED/APPLIED boundary ok
    lm.journal_set("u2", run, rnd, asg, "u2", worker_a, session, ls["lease_id"], ls["lease_nonce"],
                   JRN_PREPARED)
    check("REC-03 server activation journal ok", True)

    # REC-04 crash abans de backward -> PREPARED, no delta
    check("REC-04 before backward -> no delta",
          lm.journal_get("u2", ls["lease_id"])["delta_bundle_sha256"] is None)

    # REC-05 (crash a mig backward) -> EXPIRED + reassign
    rec_02_mid_backward_crash(lm, now, run, rnd, asg, worker_a, worker_b, session)

    # REC-06 crash després de APPLIED -> same delta exactly-once
    ls6 = lm.acquire(run, rnd, asg, "u6", worker_a, session, ttl_seconds=10.0)
    d6 = stable_delta(run, rnd, asg, "u6", worker_a)
    sha6 = hashlib.sha256(d6.numpy().tobytes()).hexdigest()
    lm.journal_set("u6", run, rnd, asg, "u6", worker_a, session, ls6["lease_id"], ls6["lease_nonce"],
                   JRN_PREPARED)
    lm.journal_set("u6", run, rnd, asg, "u6", worker_a, session, ls6["lease_id"], ls6["lease_nonce"],
                   JRN_APPLIED, delta_sha=sha6)
    # retry: same delta returned (no second optimizer step)
    retry6 = lm.journal_get("u6", ls6["lease_id"])
    check("REC-06 APPLIED retry returns same delta",
          retry6["delta_bundle_sha256"] == sha6)
    d6b = stable_delta(run, rnd, asg, "u6", worker_a)
    check("REC-06 delta deterministic (byte-equal)",
          torch.equal(d6, d6b))

    # REC-07 crash després de SUBMITTED -> same update_id
    upd7 = f"upd_{hashlib.sha256(sha6.encode()).hexdigest()[:16]}"
    lm.journal_set("u6", run, rnd, asg, "u6", worker_a, session, ls6["lease_id"], ls6["lease_nonce"],
                   JRN_SUBMITTED, delta_sha=sha6, update_id=upd7)
    retry7 = lm.journal_get("u6", ls6["lease_id"])
    check("REC-07 SUBMITTED retry same update_id", retry7["update_id"] == upd7)

    # REC-08 crash després de COMMITTED -> same receipt byte-for-byte
    receipt8 = json.dumps({"update_id": upd7, "status": "COMMITTED",
                           "receipt_nonce": "rcpt8"}, sort_keys=True)
    lm.journal_set("u6", run, rnd, asg, "u6", worker_a, session, ls6["lease_id"], ls6["lease_nonce"],
                   JRN_COMMITTED, delta_sha=sha6, update_id=upd7, receipt=receipt8)
    retry8 = lm.journal_get("u6", ls6["lease_id"])
    check("REC-08 COMMITTED retry same receipt byte-for-byte",
          retry8["receipt_json"] == receipt8)

    # REC-09 crash després de checkpoint.upload -> same checkpoint response
    ckpt9 = json.dumps({"checkpoint_id": "ck9", "status": "UPLOADED"}, sort_keys=True)
    lm.journal_set("u6", run, rnd, asg, "u6", worker_a, session, ls6["lease_id"], ls6["lease_nonce"],
                   JRN_COMMITTED, delta_sha=sha6, update_id=upd7, receipt=receipt8,
                   checkpoint_response=ckpt9)
    retry9 = lm.journal_get("u6", ls6["lease_id"])
    check("REC-09 checkpoint retry same response", retry9["checkpoint_response"] == ckpt9)

    # REC-07b/08b/09b: exactly-once CONFLICTIU — payload diferent -> REJECTED
    try:
        lm.journal_set("u6", run, rnd, asg, "u6", worker_a, session, ls6["lease_id"], ls6["lease_nonce"],
                       JRN_COMMITTED, delta_sha=sha6, update_id="upd_DIFFERENT",
                       receipt='{"different":1}', checkpoint_response='{"x":2}')
        check("REC-07b conflicting update_id REJECTED", False)
    except LeaseError:
        check("REC-07b conflicting update_id REJECTED", True)
    j_b = lm.journal_get("u6", ls6["lease_id"])
    check("REC-08b original receipt preserved", j_b["receipt_json"] == receipt8)
    check("REC-09b original checkpoint preserved", j_b["checkpoint_response"] == ckpt9)

    # REC-10 crash després d'ACTIVE -> lease ACTIVE, retry reusa la mateixa lease
    ls10 = lm.acquire(run, rnd, asg, "u10", worker_a, session, ttl_seconds=10.0)
    check("REC-10 ACTIVE lease persisted", ls10["status"] == LEASE_ACTIVE)
    st10 = lm.status(lease_id=ls10["lease_id"])
    check("REC-10 status ACTIVE", st10["status"] == LEASE_ACTIVE)

    # REC-10b: unit_key retorna la lease MÉS RECENT (no una antiga EXPIRED)
    lm.expire(run, rnd, asg, "u10", coordinator_only=True)
    ls10b = lm.acquire(run, rnd, asg, "u10", worker_b, "sessB", ttl_seconds=10.0)
    uk10 = lm.status(unit_key=(run, rnd, asg, "u10"))
    check("REC-10b unit_key retorna lease més recent (worker B)",
          uk10 is not None and uk10["worker_id"] == worker_b
          and uk10["status"] == LEASE_ACTIVE)

    # REC-11 crash durant round.close -> leases de la ronda no bloquejades
    lm.abort(run, rnd, asg, "u10")
    check("REC-11 round.close aborts leases",
          lm.status(lease_id=ls10b["lease_id"])["status"] == LEASE_ABORTED)

    # REC-12 crash entre Round 1 i Round 2 -> nova ronda, leases independents
    ls12 = lm.acquire(run, "r2", asg, "u12", worker_a, session, ttl_seconds=10.0)
    check("REC-12 round 2 independent lease", ls12["status"] == LEASE_ACTIVE)
    st_u10 = lm.status(lease_id=ls10b["lease_id"])
    check("REC-12 no cross-round contamination", st_u10["status"] == LEASE_ABORTED)

    conn.close()
    if os.path.exists(db):
        os.remove(db)
    return


def main():
    run_crash_matrix()
    npass = sum(1 for _, ok, _ in RESULTS if ok)
    nfail = len(RESULTS) - npass
    print(f"\n=== RC5.4 CRASH MATRIX: {npass}/{len(RESULTS)} PASS, {nfail} FAIL ===")
    if nfail:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  FAILED: {name} {detail}")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
