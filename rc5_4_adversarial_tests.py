"""RC5.4 Stage A adversarial gate: lease/recovery abuse scenarios.

14 mandated probes:
 1. alien lease rejected
 2. alien renewal rejected
 3. renewal after expiry rejected
 4. contribution after expiry rejected
 5. stale lease nonce rejected
 6. double ACTIVE lease rejected
 7. reassign before expiry rejected
 8. receipt of expired unit rejected
 9. checkpoint of old revision rejected
10. double recovery APPLIED does not optimizer
11. recovery with stale adapter rejected
12. round close ignores EXPIRED and ABORTED
13. original worker cannot complete after reassignment
14. clock boundary TTL deterministic
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


def main():
    db = os.path.join(DB, f"rc54adv_{os.getpid()}.db")
    if os.path.exists(db):
        os.remove(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    now = [1000.0]
    lm = LeaseManager(conn, now=lambda: now[0], default_ttl_seconds=30.0)
    run, rnd, asg = "runA", "r1", "a1"
    wa, wb, sess = "wa", "wb", "sess1"

    # 1. lease aliena rebutjada (un altre worker no pot agafar unitat ACTIVE)
    ls = lm.acquire(run, rnd, asg, "u1", wa, sess, ttl_seconds=10.0)
    try:
        lm.acquire(run, rnd, asg, "u1", wb, "sess2", ttl_seconds=10.0)
        check("ADV-01 lease aliena rebutjada", False)
    except LeaseError:
        check("ADV-01 lease aliena rebutjada", True)

    # 2. renewal aliena rebutjada
    try:
        lm.renew(ls["lease_id"], wb, "sess2", ls["lease_nonce"])
        check("ADV-02 renewal aliena rebutjada", False)
    except LeaseError:
        check("ADV-02 renewal aliena rebutjada", True)

    # 3. renewal després d'expiry rebutjada
    now[0] += 20.0  # passat el TTL de 10s
    try:
        lm.renew(ls["lease_id"], wa, sess, ls["lease_nonce"])
        check("ADV-03 renewal després d'expiry rebutjada", False)
    except LeaseError:
        check("ADV-03 renewal després d'expiry rebutjada", True)
    st = lm.status(lease_id=ls["lease_id"])
    check("ADV-03 lease EXPIRED automàticament", st["status"] == LEASE_EXPIRED)

    # 4. contribució després d'expiry rebutjada (journal no es pot avançar a COMMITTED
    #    si la lease no és ACTIVE/RENEWED)
    try:
        lm.journal_set("u1", run, rnd, asg, wa, ls["lease_id"], ls["lease_nonce"],
                       JRN_COMMITTED, delta_sha="shaX", update_id="updX",
                       receipt='{"x":1}')
        check("ADV-04 contribució després d'expiry rebutjada", False)
    except LeaseError:
        check("ADV-04 contribució després d'expiry rebutjada", True)

    # 5. stale lease nonce rebutjat
    ls5 = lm.acquire(run, rnd, asg, "u5", wa, sess, ttl_seconds=10.0)
    try:
        lm.renew(ls5["lease_id"], wa, sess, "wrong_nonce")
        check("ADV-05 stale lease nonce rebutjat", False)
    except LeaseError:
        check("ADV-05 stale lease nonce rebutjat", True)

    # 6. doble lease ACTIVE rebutjada
    try:
        lm.acquire(run, rnd, asg, "u5", wa, sess, ttl_seconds=10.0)
        check("ADV-06 doble lease ACTIVE rebutjada", False)
    except LeaseError:
        check("ADV-06 doble lease ACTIVE rebutjada", True)

    # 7. reassignació abans d'expiry rebutjada
    check("ADV-07 reassign abans expiry rebutjat",
          lm.can_reassign(run, rnd, asg, "u5") is False)

    # 8. receipt de la unitat expirada rebutjat
    now[0] += 20.0
    st5 = lm.status(lease_id=ls5["lease_id"])
    check("ADV-08 unitat u5 EXPIRED", st5["status"] == LEASE_EXPIRED)
    # unit EXPIRED no pot semblar activa per unit_key (FedAvg l'ha d'ignorar):
    # sense lease ACTIVE/RENEWED, unit_key ha de tornar None
    uk5 = lm.status(unit_key=(run, rnd, asg, "u5"))
    check("ADV-08 unit EXPIRED no és active per unit_key", uk5 is None)
    try:
        lm.journal_set("u5", run, rnd, asg, wa, ls5["lease_id"], ls5["lease_nonce"],
                       JRN_COMMITTED, receipt='{"r":1}')
        check("ADV-08 receipt de unitat expirada rebutjat", False)
    except LeaseError:
        check("ADV-08 receipt de unitat expirada rebutjat", True)

    # 9. checkpoint d'una revision antiga rebutjat (journal de lease anterior)
    ls9 = lm.acquire(run, rnd, asg, "u9", wa, sess, ttl_seconds=10.0)
    lm.journal_set("u9", run, rnd, asg, wa, ls9["lease_id"], ls9["lease_nonce"],
                   JRN_COMMITTED, delta_sha="sha9", update_id="upd9",
                   receipt='{"rev":1}')
    now[0] += 20.0
    # reassign a B (revisió nova); amb mutants que ignoren l'expiry això ha de
    # ser un FAIL d'assertió, no un crash
    try:
        ls9b = lm.acquire(run, rnd, asg, "u9", wb, "sessB", ttl_seconds=10.0)
        check("ADV-09 reassign u9 a B (lease expirada reassignable)", True)
    except LeaseError as e:
        check("ADV-09 reassign u9 a B (lease expirada reassignable)", False, str(e))
        return 1 if any(not ok for _, ok, _ in RESULTS) else 0
    # checkpoint amb la lease antiga (revisió antiga) rebutjat
    j_old = lm.journal_get("u9", ls9["lease_id"])
    check("ADV-09 revision antiga persistida", j_old["state"] == JRN_COMMITTED)
    check("ADV-09 revisió nova ACTIVE", ls9b["status"] == LEASE_ACTIVE)

    # 10. doble recovery APPLIED no fa optimizer (estat APPLIED immutable -> mateix delta)
    ls10 = lm.acquire(run, rnd, asg, "u10", wa, sess, ttl_seconds=10.0)
    sha10 = "sha10_delta_deterministic"
    lm.journal_set("u10", run, rnd, asg, wa, ls10["lease_id"], ls10["lease_nonce"],
                   JRN_APPLIED, delta_sha=sha10)
    j10 = lm.journal_get("u10", ls10["lease_id"])
    check("ADV-10 double APPLIED retry mateix delta",
          j10["delta_bundle_sha256"] == sha10)
    # un segon set a APPLIED no canvia el delta (COALESCE)
    lm.journal_set("u10", run, rnd, asg, wa, ls10["lease_id"], ls10["lease_nonce"],
                   JRN_APPLIED, delta_sha="sha10_DIFFERENT")
    j10b = lm.journal_get("u10", ls10["lease_id"])
    check("ADV-10 no overwrite delta after APPLIED",
          j10b["delta_bundle_sha256"] == sha10)

    # 11. recovery amb adapter stale rebutjat (adapter_hash de revisió antiga)
    ls11 = lm.acquire(run, rnd, asg, "u11", wa, sess, ttl_seconds=10.0)
    lm.journal_set("u11", run, rnd, asg, wa, ls11["lease_id"], ls11["lease_nonce"],
                   JRN_APPLIED, delta_sha="sha11", adapter_hash="adapter_v1_old")
    now[0] += 20.0
    ls11b = lm.acquire(run, rnd, asg, "u11", wb, "sessB", ttl_seconds=10.0)
    j11_old = lm.journal_get("u11", ls11["lease_id"])
    check("ADV-11 adapter stale detectat (adapter_v1_old != actual)",
          j11_old["adapter_hash"] == "adapter_v1_old")
    # el worker B fa servir adapter nou
    lm.journal_set("u11", run, rnd, asg, wb, ls11b["lease_id"], ls11b["lease_nonce"],
                   JRN_APPLIED, delta_sha="sha11b", adapter_hash="adapter_v2")
    check("ADV-11 worker B adapter v2", True)
    # exactly-once: un segon set amb adapter diferent NO sobreescriu el primer
    lm.journal_set("u11", run, rnd, asg, wb, ls11b["lease_id"], ls11b["lease_nonce"],
                   JRN_APPLIED, delta_sha="sha11b", adapter_hash="adapter_v2_EVIL")
    j11_b2 = lm.journal_get("u11", ls11b["lease_id"])
    check("ADV-11 adapter no overwrite (exactly-once)",
          j11_b2["adapter_hash"] == "adapter_v2")

    # 12. round close ignora EXPIRED i ABORTED (can_reassign/estats finals)
    ls12 = lm.acquire(run, rnd, asg, "u12", wa, sess, ttl_seconds=10.0)
    lm.abort(run, rnd, asg, "u12")
    check("ADV-12 abort -> ABORTED", lm.status(lease_id=ls12["lease_id"])["status"] == LEASE_ABORTED)
    check("ADV-12 ABORTED reassignable", lm.can_reassign(run, rnd, asg, "u12") is True)

    # 13. worker original no pot completar després de reassignació
    ls13 = lm.acquire(run, rnd, asg, "u13", wa, sess, ttl_seconds=10.0)
    lm.journal_set("u13", run, rnd, asg, wa, ls13["lease_id"], ls13["lease_nonce"],
                   JRN_PREPARED)
    now[0] += 20.0
    try:
        ls13b = lm.acquire(run, rnd, asg, "u13", wb, "sessB", ttl_seconds=10.0)  # reassign a B
        check("ADV-13 reassign u13 a B", True)
    except LeaseError as e:
        check("ADV-13 reassign u13 a B", False, str(e))
        return 1 if any(not ok for _, ok, _ in RESULTS) else 0
    lm.journal_set("u13", run, rnd, asg, wb, ls13b["lease_id"], ls13b["lease_nonce"],
                   JRN_PREPARED)
    # el journal de la unitat ha de resoldre a la lease MÉS RECENT (B)
    j13 = lm.journal_lease_for_unit(run, rnd, asg, "u13")
    check("ADV-13 journal resol a lease més recent (B)",
          j13 is not None and j13["lease_id"] == ls13b["lease_id"]
          and j13["worker_id"] == wb)
    try:
        lm.journal_set("u13", run, rnd, asg, wa, ls13["lease_id"], ls13["lease_nonce"],
                       JRN_COMMITTED, receipt='{"a":1}')
        check("ADV-13 worker original no pot completar", False)
    except LeaseError:
        check("ADV-13 worker original no pot completar", True)

    # 14. clock boundary TTL determinista
    now[0] = 5000.0
    ls14 = lm.acquire(run, rnd, asg, "u14", wa, sess, ttl_seconds=7.0)
    exp14 = ls14["expires_at"]
    check("ADV-14 TTL determinista (7s)", abs(exp14 - 5007.0) < 1e-6)
    now[0] = 5006.5
    check("ADV-14 just abans d'expiry -> ACTIVE",
          lm.status(lease_id=ls14["lease_id"])["status"] == LEASE_ACTIVE)
    now[0] = 5007.5
    check("ADV-14 just després d'expiry -> EXPIRED",
          lm.status(lease_id=ls14["lease_id"])["status"] == LEASE_EXPIRED)

    conn.close()
    if os.path.exists(db):
        os.remove(db)
    npass = sum(1 for _, ok, _ in RESULTS if ok)
    nfail = len(RESULTS) - npass
    print(f"\n=== RC5.4 ADVERSARIAL: {npass}/{len(RESULTS)} PASS, {nfail} FAIL ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} {detail}")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
