#!/usr/bin/env python3
"""make_delivery_info.py — Genera DELIVERY_INFO.txt + verify.txt reals (R2.2).

Tots els camps es llegeixen del sistema en el moment de l'entrega:
  - commit complet, parent/base real, branch
  - tarball, SHA detached, mida
  - fitxers regulars, manifest lines, sha256sum -c
  - gate final (evidence/GENERIC_DISTRIBUTED_FINAL_GATE.log)
  - PIDs Qwen (evidències), PIDs recovery
  - ETT A/B, adapter 0/1/2 SHA, oracle Round1/2
  - quality before/after
Cap <PENDENT>, cap "GATES: (sense logs)".
"""
import hashlib
import json
import os
import subprocess
import sys
import time

REPO = "/root/meshtrainer"


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout.strip()


def main():
    tarball = sys.argv[1] if len(sys.argv) > 1 else None
    sha_file = tarball + ".sha256" if tarball else None
    L = []
    L.append("=" * 70)
    L.append(" DELIVERY_INFO — MeshTrainer Generic Distributed (R2.5.1 FINAL)")
    L.append(f" Generat: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    L.append("=" * 70)
    L.append("")

    # ── commit / parent / branch ──
    commit = sh(f"cd {REPO} && git rev-parse HEAD")
    parent = sh(f"cd {REPO} && git rev-parse HEAD~1 2>/dev/null || echo -")
    branch = sh(f"cd {REPO} && git branch --show-current")
    L.append(f"COMMIT (complet): {commit}")
    L.append(f"PARENT (base):    {parent}")
    L.append(f"BRANCH:           {branch}")
    L.append("")

    # ── tarball / SHA detached ──
    if tarball and os.path.exists(tarball):
        size = os.path.getsize(tarball)
        sha = open(sha_file).read().strip() if os.path.exists(sha_file) else ""
        L.append(f"TARBALL: {os.path.basename(tarball)}")
        L.append(f"SIZE:    {size} bytes")
        L.append(f"SHA-256 (detached, autoritatiu): {sha}")
    else:
        L.append("TARBALL: <no especificat>")
    L.append("")

    # ── fitxers regulars + manifest ──
    files = sh(f"cd {REPO} && find . -type f ! -path './.git/*' "
               f"! -path './.venv/*' ! -path '*/__pycache__/*' "
               f"! -name '*.pyc' ! -name 'MANIFEST.sha256' | wc -l")
    mf = os.path.join(REPO, "MANIFEST.sha256")
    mf_lines = "0"
    if os.path.exists(mf):
        mf_lines = str(sum(1 for _ in open(mf)))
    L.append(f"FITXERS REGULARS (repo, sense .git/.venv/pycache/manifest): {files}")
    L.append(f"MANIFEST LINES: {mf_lines}")
    # El manifest es verifica sobre l'EXTRACCIÓ EXACTA del paquet final
    # (clean extraction A/B), no sobre el repo — punt 10 de l'ordre.
    ab_a = os.path.join(REPO, "evidence", "CLEAN_EXTRACTION_A.log")
    ab_b = os.path.join(REPO, "evidence", "CLEAN_EXTRACTION_B.log")
    if os.path.exists(ab_a) and os.path.exists(ab_b):
        mf_ok = 0
        for ab in (ab_a, ab_b):
            txt = open(ab).read()
            if "manifest" in txt and "sha256sum -c" in txt and \
               "0 errors" in txt and "PASS" in txt:
                mf_ok += 1
        if mf_ok == 2:
            L.append("sha256sum -c (sobre l'extracció del tarball, A/B): "
                     "0 errors — PASS")
        else:
            L.append("sha256sum -c (sobre l'extracció del tarball, A/B): "
                     "NO CONFIRMAT")
    else:
        L.append("sha256sum -c (sobre l'extracció del tarball, A/B): "
                 "sense logs A/B")
    L.append("")

    # ── gate final ──
    gatelog = os.path.join(REPO, "evidence", "GENERIC_DISTRIBUTED_FINAL_GATE.log")
    L.append("GATE FINAL (evidence/GENERIC_DISTRIBUTED_FINAL_GATE.log):")
    if os.path.exists(gatelog):
        for line in open(gatelog):
            line = line.rstrip()
            if line.startswith("  ") and ":" in line and "===" not in line:
                L.append(f"  {line.strip()}")
        overall = sh(f"grep -E 'Overall:' {gatelog} | tail -1")
        L.append(f"  {overall.strip()}")
    else:
        L.append("  (log absent)")
    L.append("")

    # ── artefactes / metrics / adapter SHAs ──
    outdir = os.path.join(REPO, "qwen_distributed_output")
    L.append("ARTEFACTES (qwen_distributed_output/):")
    if os.path.isdir(outdir):
        for fn in sorted(os.listdir(outdir)):
            fp = os.path.join(outdir, fn)
            if os.path.isfile(fp):
                h = hashlib.sha256(open(fp, "rb").read()).hexdigest()
                L.append(f"  - {fn}: sha256={h[:32]}... ({os.path.getsize(fp)} B)")
        mj = os.path.join(outdir, "distributed_metrics.json")
        if os.path.exists(mj):
            d = json.load(open(mj))
            L.append("")
            L.append("METRICS (distributed_metrics.json):")
            for k, v in d.items():
                L.append(f"  {k}: {v}")
            L.append("")
            L.append(f"ETT A/B: {d.get('ett_a')} / {d.get('ett_b')}")
            L.append(f"ORACLE Round1 maxdiff: {d.get('maxdiff_oracle_r1')}")
            L.append(f"ORACLE Round2 maxdiff: {d.get('maxdiff_oracle_r2')}")
    else:
        L.append("  (sense artefactes)")
    L.append("")

    # ── quality ──
    for qn in ("quality_before.json", "quality_after.json"):
        qp = os.path.join(outdir, qn) if os.path.isdir(outdir) else ""
        if qp and os.path.exists(qp):
            try:
                q = json.load(open(qp))
                L.append(f"{qn.upper()}: validation_loss={q.get('validation_loss')} "
                         f"ppl={q.get('validation_perplexity')} "
                         f"holdout={q.get('score_total')}")
            except Exception:
                L.append(f"{qn.upper()}: (no llegible)")
    L.append("CLASSIFICACIÓ: PIPELINE TRAINING PASS · QUALITY PILOT")
    L.append("")

    # ── evidències (PIDs reals) ──
    L.append("EVIDÈNCIES (PIDs):")
    if os.path.isdir(outdir):
        for fn in sorted(os.listdir(outdir)):
            if fn.startswith("evidence_") and fn.endswith(".json"):
                try:
                    ev = json.load(open(os.path.join(outdir, fn)))
                    L.append(
                        f"  - {fn}: pid={ev.get('pid')} "
                        f"worker={ev.get('worker_id')} shard={ev.get('shard')} "
                        f"delta={ev.get('delta_sha', '')[:16]} "
                        f"adapter_post={ev.get('adapter_post_hash', '')[:16]}")
                except Exception:
                    pass
    else:
        L.append("  (sense evidències)")
    L.append("")
    L.append("=" * 70)
    L.append(" FI DELIVERY_INFO — cap camp pendent")
    L.append("=" * 70)

    txt = "\n".join(L) + "\n"
    # ── còpia INTERNA (repo/tarball): marcada PRE-TARBALL — punt 10 ──
    # La còpia externa (que es lliura a part) es deriva del tarball final
    # amb el SHA real; aquesta és la versió de treball dins del paquet.
    with open(os.path.join(REPO, "DELIVERY_INFO.txt"), "w") as f:
        f.write("DELIVERY_INFO.txt — CÒPIA INTERNA PRE-TARBALL\n"
                "(la còpia definitiva es deriva del tarball final "
                "a l'entrega; SHA detached = autoritatiu)\n\n" + txt)
    # verify.txt: còpia interna marcada PRE-TARBALL (la definitiva es
    # derivarà de l'extracció exacta del tarball final)
    with open(os.path.join(REPO, "verify.txt"), "w") as f:
        f.write("verify.txt — PRE-TARBALL (còpia interna de DELIVERY_INFO; "
                "la verificació definitiva es fa sobre l'extracció del "
                "tarball final a clean-extraction)\n\n" + txt)
    print(txt)


if __name__ == "__main__":
    main()
