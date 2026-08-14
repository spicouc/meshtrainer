#!/usr/bin/env python3
"""minicpm5_artefact_integrity.py — R1.1 punt 11: integritat dels artefactes.

NO carrega cap model (2.1 GB). Valida exclusivament la coherència
criptogràfica dels artefactes empaquetats a minicpm5_output/:

  - distributed_metrics.backend == "minicpm5"
  - sha256(adapter_0.bundle) == metrics.adapter_0_sha
  - sha256(adapter_1.bundle) == metrics.adapter_1_sha
  - sha256(adapter_2.bundle) == metrics.adapter_2_sha
  - evidence_w-A/B/A2/B2.json presents
  - PIDs dels workers coincideixen entre evidence i distributed.log
  - quality_before/after.backend == "minicpm5"
"""
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "minicpm5_output")
EVID = os.path.join(HERE, "evidence", "minicpm5")

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def sha_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 60)
    print(" MINICPM5 ARTEFACT INTEGRITY (sense carregar model)")
    print("=" * 60)

    # ── 1. metrics present i backend correcte ──
    mp = os.path.join(OUT, "distributed_metrics.json")
    if not os.path.exists(mp):
        check("metrics present", False, f"no existeix: {mp}")
        print(f"\n=== ARTEFACT INTEGRITY: {sum(1 for _, ok in CHECKS if ok)}/"
              f"{len(CHECKS)} PASS ===")
        sys.exit(1)
    m = json.load(open(mp))
    check("metrics.backend == minicpm5", m.get("backend") == "minicpm5",
          f"backend={m.get('backend')!r}")

    # ── 2. SHAs dels 3 adapters ──
    for i in (0, 1, 2):
        ap = os.path.join(OUT, f"adapter_{i}.bundle")
        key = f"adapter_{i}_sha"
        if not os.path.exists(ap):
            check(f"adapter_{i}.bundle present", False)
            continue
        actual = sha_of(ap)
        expected = m.get(key)
        check(f"sha256(adapter_{i}) == metrics.{key}", actual == expected,
              f"{actual[:12]} vs {str(expected)[:12]}")

    # ── 3. evidence dels 4 workers ──
    for w in ("A", "B", "A2", "B2"):
        ep = os.path.join(OUT, f"evidence_w-{w}.json")
        ok = os.path.exists(ep)
        check(f"evidence_w-{w}.json present", ok)

    # ── 4. PIDs coherents entre evidence i distributed.log ──
    pid_ok = True
    pids_evidence = {}
    for w in ("A", "B", "A2", "B2"):
        ep = os.path.join(OUT, f"evidence_w-{w}.json")
        if os.path.exists(ep):
            ev = json.load(open(ep))
            pids_evidence[w] = ev.get("pid")
    log = ""
    if os.path.exists(os.path.join(EVID, "distributed.log")):
        log = open(os.path.join(EVID, "distributed.log")).read()
    for w, pid in pids_evidence.items():
        if pid is None:
            pid_ok = False
            continue
        # el log ha de contenir el worker i el PID
        pat = re.compile(rf"\[w-{w}\] \w+.*\b{pid}\b")
        if not pat.search(log) and str(pid) not in log:
            pid_ok = False
    check("PIDs evidence_w-* == distributed.log (mateixa execució)", pid_ok,
          f"pids={pids_evidence}")

    # ── 5. quality jsons amb backend == minicpm5 ──
    for q in ("quality_before.json", "quality_after.json"):
        qp = os.path.join(OUT, q)
        if not os.path.exists(qp):
            check(f"{q} present", False)
            continue
        qd = json.load(open(qp))
        check(f"{q}.backend == minicpm5", qd.get("backend") == "minicpm5",
              f"backend={qd.get('backend')!r}")

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"\n=== ARTEFACT INTEGRITY: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
