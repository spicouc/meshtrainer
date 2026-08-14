#!/usr/bin/env bash
# RUN_CLEAN_EXTRACTION_EXTERNAL.sh — R2.5.2 punt 14: CE A/B EXTERNES
# Sobre el tarball IMMUTABLE, extreu a directori buit, verifica:
#   tar SHA, tar size, regular files, manifest lines, 0 missing, 0 FAILED,
#   AUTH, SEC+VAL, dummy e2e, dummy recovery, Overall PASS.
# Els logs es guarden FORA del tar (a /root).
set -u
TARBALL="${1:?ús: $0 <tarball>}"
LABEL="${2:-A}"
ISO_PY="${ISO_PYTHON:-/tmp/iso_venv/bin/python}"
PYTHON="${PYTHON:-/opt/qwen3-venv/bin/python}"
LOG="/root/CLEAN_EXTRACTION_${LABEL}.log"
DETACHED="${TARBALL}.sha256"
EXPECTED_SHA="$(cat "$DETACHED")"
ACTUAL_SHA="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
SIZE=$(stat -c%s "$TARBALL")
OVERALL=0

echo "══ CLEAN EXTRACTION ${LABEL} — tarball immutable ══" > "$LOG"
echo "tarball: $TARBALL" >> "$LOG"
echo "tar SHA: $ACTUAL_SHA" >> "$LOG"
echo "tar size: $SIZE B" >> "$LOG"
echo "detached SHA: $EXPECTED_SHA" >> "$LOG"
if [ "$ACTUAL_SHA" = "$EXPECTED_SHA" ]; then
    echo "SHA check: PASS" >> "$LOG"
else
    echo "SHA check: FAIL (mismatch!)" >> "$LOG"; OVERALL=1
fi

EX=$(mktemp -d)
tar -xzf "$TARBALL" -C "$EX" 2>>"$LOG"
cd "$EX" || exit 2
echo "regular files (extret): $(find . -type f ! -name MANIFEST.sha256 | wc -l)" >> "$LOG"
echo "manifest lines: $(wc -l < MANIFEST.sha256)" >> "$LOG"
MF=$(sha256sum -c MANIFEST.sha256 2>&1 | grep -cv ': OK')
echo "sha256sum -c: $MF no-OK (0 missing/0 FAILED esperat)" >> "$LOG"
[ "$MF" -eq 0 ] || OVERALL=1

echo "--- gates lleugers (ISO venv, sense Qwen) ---" >> "$LOG"
# AUTH (necessita numpy del venv ISO)
if [ -x "$ISO_PY" ]; then
    (cd "$EX" && timeout 300 "$ISO_PY" coordinator_authority_tests.py) >> "$LOG" 2>&1
    AEC=$?
    A_RES=$([ $AEC -eq 0 ] && echo PASS || echo FAIL)
    echo "AUTH: $A_RES (exit $AEC)" >> "$LOG"
    [ $AEC -eq 0 ] || OVERALL=1
    # SEC+VAL
    (cd "$EX" && timeout 300 "$ISO_PY" fedavg_authority_tests.py) >> "$LOG" 2>&1
    SEC=$?
    S_RES=$([ $SEC -eq 0 ] && echo PASS || echo FAIL)
    echo "SEC+VAL: $S_RES (exit $SEC)" >> "$LOG"
    [ $SEC -eq 0 ] || OVERALL=1
else
    echo "AUTH: SKIP (no ISO venv)" >> "$LOG"
    echo "SEC+VAL: SKIP (no ISO venv)" >> "$LOG"
fi
# dummy e2e (amb el venv qwen, que té numpy)
if [ -x "$PYTHON" ]; then
    (cd "$EX" && timeout 600 "$PYTHON" generic_distributed_run.py \
        --backend dummy --num-examples 4 --seq-len 16) >> "$LOG" 2>&1
    DEC=$?
    D_RES=$([ $DEC -eq 0 ] && echo PASS || echo FAIL)
    echo "Dummy E2E: $D_RES (exit $DEC)" >> "$LOG"
    [ $DEC -eq 0 ] || OVERALL=1
    (cd "$EX" && timeout 600 "$PYTHON" generic_recovery_tests.py \
        --backend dummy --num-examples 4 --seq-len 16) >> "$LOG" 2>&1
    DRC=$?
    DR_RES=$([ $DRC -eq 0 ] && echo PASS || echo FAIL)
    echo "Dummy recovery: $DR_RES (exit $DRC)" >> "$LOG"
    [ $DRC -eq 0 ] || OVERALL=1
    # R1.1: MiniCPM5 artefact integrity (sense carregar el model de 2.1 GB —
    # només coherència criptogràfica dels artefactes empaquetats)
    (cd "$EX" && timeout 120 "$PYTHON" minicpm5_artefact_integrity.py) >> "$LOG" 2>&1
    AIC=$?
    AI_RES=$([ $AIC -eq 0 ] && echo PASS || echo FAIL)
    echo "MiniCPM5 artefact integrity: $AI_RES (exit $AIC)" >> "$LOG"
    [ $AIC -eq 0 ] || OVERALL=1
fi

echo "══ CLEAN EXTRACTION ${LABEL} — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL) ══" >> "$LOG"
rm -rf "$EX"
exit $OVERALL
