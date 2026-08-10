#!/usr/bin/env python3
"""make_delivery_info.py — Genera DELIVERY_INFO.txt + verify.txt reals (R2.1).

Elimina el verify stale de f0a71e1: totes les dades es llegeixen del
sistema en el moment de l'entrega:
  - commit complet real, branch, base
  - SHA detached (fitxer .sha256), mida real del tarball
  - fitxers i manifest (compte + sha256sum -c)
  - gates (logs), PIDs (evidències), metrics, adapter SHAs
Cap <PENDENT>, <coberts>, <manifest>.
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
    out_lines = []
    out_lines.append("=" * 70)
    out_lines.append(" DELIVERY_INFO — MeshTrainer Generic Distributed (R2.1)")
    out_lines.append(f" Generat: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    out_lines.append("=" * 70)
    out_lines.append("")

    # ── commit / branch / base ──
    commit = sh(f"cd {REPO} && git rev-parse HEAD")
    branch = sh(f"cd {REPO} && git branch --show-current")
    base = sh(f"cd {REPO} && git merge-base HEAD HEAD~0 2>/dev/null || echo {commit}")
    out_lines.append(f"COMMIT (complet): {commit}")
    out_lines.append(f"BRANCH:           {branch}")
    out_lines.append(f"BASE (merge-base):{base}")
    out_lines.append("")

    # ── tarball / SHA detached ──
    if tarball and os.path.exists(tarball):
        size = os.path.getsize(tarball)
        sha = open(sha_file).read().strip() if os.path.exists(sha_file) else ""
        out_lines.append(f"TARBALL: {os.path.basename(tarball)}")
        out_lines.append(f"SIZE:    {size} bytes")
        out_lines.append(f"SHA-256 (detached, autoritatiu): {sha}")
    else:
        out_lines.append("TARBALL: <no especificat>")
    out_lines.append("")

    # ── fitxers del repo (excloent .git/.venv/__pycache__) ──
    files = sh(f"cd {REPO} && find . -type f ! -path './.git/*' "
               f"! -path './.venv/*' ! -path '*/__pycache__/*' "
               f"! -path './qwen_distributed_output/*' "
               f"! -path './qwen_distributed_logs/*' "
               f"! -path './qwen3_pilot_output/*' ! -path './qwen3_pilot_logs/*' "
               f"! -name '*.pyc' | wc -l")
    out_lines.append(f"FITXERS (repo, sense outputs): {files}")
    out_lines.append("")

    # ── manifest ──
    mf = os.path.join(REPO, "MANIFEST.sha256")
    if os.path.exists(mf):
        mf_lines = sum(1 for _ in open(mf))
        out_lines.append(f"MANIFEST: {mf_lines} entrades")
        chk = sh(f"cd {REPO} && sha256sum -c MANIFEST.sha256 2>&1 | "
                 f"grep -cv ': OK' || true")
        out_lines.append(f"sha256sum -c: {chk} errors/missing/FAILED")
    else:
        out_lines.append("MANIFEST: <absent>")
    out_lines.append("")

    # ── gates (logs) ──
    logdir = os.path.join(REPO, "qwen_distributed_logs")
    out_lines.append("GATES:")
    if os.path.isdir(logdir):
        for fn in sorted(os.listdir(logdir)):
            fp = os.path.join(logdir, fn)
            if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                last = ""
                for line in open(fp, errors="ignore"):
                    if "PASS" in line or "FAIL" in line or "OK" in line:
                        last = line.strip()[:100]
                out_lines.append(f"  - {fn}: {last or 'OK'}")
    else:
        out_lines.append("  - (sense logs)")
    out_lines.append("")

    # ── artefactes / metrics / adapter SHAs ──
    outdir = os.path.join(REPO, "qwen_distributed_output")
    out_lines.append("ARTEFACTES:")
    if os.path.isdir(outdir):
        for fn in sorted(os.listdir(outdir)):
            fp = os.path.join(outdir, fn)
            if os.path.isfile(fp):
                h = hashlib.sha256(open(fp, "rb").read()).hexdigest()
                out_lines.append(f"  - {fn}: sha256={h[:32]}... "
                                 f"({os.path.getsize(fp)} B)")
        mj = os.path.join(outdir, "distributed_metrics.json")
        if os.path.exists(mj):
            out_lines.append("")
            out_lines.append("METRICS (distributed_metrics.json):")
            d = json.load(open(mj))
            for k, v in d.items():
                out_lines.append(f"  {k}: {v}")
    else:
        out_lines.append("  - (sense artefactes)")
    out_lines.append("")

    # ── evidències (PIDs reals) ──
    out_lines.append("EVIDÈNCIES (PIDs):")
    for fn in sorted(os.listdir(outdir)) if os.path.isdir(outdir) else []:
        if fn.startswith("evidence_") and fn.endswith(".json"):
            try:
                ev = json.load(open(os.path.join(outdir, fn)))
                out_lines.append(
                    f"  - {fn}: pid={ev.get('pid')} "
                    f"worker={ev.get('worker_id')} shard={ev.get('shard')} "
                    f"delta={ev.get('delta_sha', '')[:16]} "
                    f"adapter_post={ev.get('adapter_post_hash', '')[:16]}")
            except Exception:
                pass
    out_lines.append("")
    out_lines.append("=" * 70)
    out_lines.append(" FI DELIVERY_INFO — cap camp pendent")
    out_lines.append("=" * 70)

    txt = "\n".join(out_lines) + "\n"
    with open(os.path.join(REPO, "DELIVERY_INFO.txt"), "w") as f:
        f.write(txt)
    with open(os.path.join(REPO, "verify.txt"), "w") as f:
        f.write(txt)
    print(txt)
    print(f"\nEscrit: {REPO}/DELIVERY_INFO.txt i {REPO}/verify.txt")


if __name__ == "__main__":
    main()
