#!/usr/bin/env bash
# RUN_CLEAN_EXTRACTION_AB.sh — CLEAN EXTRACTION A/B (R2.2, punt 11)
#
# Sobre el tarball DEFINITIU:
#   A: directori buit -> extract -> manifest -> backend isolation dummy
#      -> generic gate (subconjunt dummy) -> PASS
#   B: mateix procés -> PASS
# Guarda els dos logs a evidence/.
set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${QWEN3_PYTHON:-/opt/qwen3-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
ISO_PY="${ISO_PYTHON:-/tmp/iso_venv/bin/python}"
TARBALL="${1:-/root/meshtrainer_generic_distributed_qwen_r2_2_final_<commit>.tar.gz}"
EVIDENCE="$(pwd)/evidence"
mkdir -p "$EVIDENCE"
OVERALL=0

if [ ! -f "$TARBALL" ]; then
    echo "Tarball no trobat: $TARBALL"
    exit 1
fi
echo "Tarball: $TARBALL ($(stat -c%s "$TARBALL") bytes)"

run_ab() {
    local letter="$1"
    local dir=$(mktemp -d)
    local log="$EVIDENCE/CLEAN_EXTRACTION_${letter}.log"
    echo "══════════════════════════════════════════" > "$log"
    echo " CLEAN EXTRACTION ${letter} — $(date '+%F %T')" >> "$log"
    echo "══════════════════════════════════════════" >> "$log"
    local ok=1

    # 1) extracció a directori BUIDE
    echo "" >> "$log"; echo "=== [1] extracció a directori buit ===" >> "$log"
    tar -xzf "$TARBALL" -C "$dir" 2>> "$log"
    if [ $? -eq 0 ]; then echo "  extract: PASS ($(find "$dir" -type f | wc -l) fitxers)" >> "$log"
    else echo "  extract: FAIL"; ok=0; fi

    # 2) manifest sobre l'extracció exacta
    echo "" >> "$log"; echo "=== [2] manifest sobre l'extracció ===" >> "$log"
    if [ -f "$dir/MANIFEST.sha256" ]; then
        cd "$dir"
        ERR=$(sha256sum -c MANIFEST.sha256 2>&1 | grep -cv ': OK' || true)
        MF=$(wc -l < MANIFEST.sha256)
        REG=$(find . -type f ! -name 'MANIFEST.sha256' ! -path './.git/*' \
              ! -path './.venv/*' ! -path '*/__pycache__/*' ! -name '*.pyc' | wc -l)
        echo "  manifest lines: $MF" >> "$log"
        echo "  regular_files:  $REG" >> "$log"
        echo "  sha256sum -c: $ERR errors ($([ "$ERR" -eq 0 ] && echo PASS || echo FAIL))" >> "$log"
        [ "$ERR" -eq 0 ] || ok=0
        [ "$MF" -eq $((REG)) ] || { echo "  manifest_lines == regular_files: FAIL" >> "$log"; ok=0; }
        cd - > /dev/null
    else
        echo "  MANIFEST.sha256 absent"; ok=0
    fi

    # 3) backend isolation dummy (venv sense torch)
    echo "" >> "$log"; echo "=== [3] backend isolation (dummy, venv ISO) ===" >> "$log"
    if [ -x "$ISO_PY" ] && [ -f "$dir/backend_isolation.py" ]; then
        (cd "$dir" && timeout 600 "$ISO_PY" backend_isolation.py) >> "$log" 2>&1
        ec=$?
        echo "  backend_isolation: $([ $ec -eq 0 ] && echo PASS || echo FAIL (exit $ec))" >> "$log"
        [ $ec -eq 0 ] || ok=0
    else
        echo "  backend_isolation: NO EXECUTADA (falta venv ISO o script)"; ok=0
    fi

    # 4) generic gate (subconjunt dummy: e2e + recovery)
    echo "" >> "$log"; echo "=== [4] dummy e2e + recovery (extracció exacta) ===" >> "$log"
    if [ -f "$dir/generic_distributed_run.py" ] && [ -f "$dir/qwen3_pilot_data/train.jsonl" ]; then
        (cd "$dir" && timeout 600 "$PYTHON" generic_distributed_run.py \
            --backend dummy --num-examples 4 --seq-len 16) >> "$log" 2>&1
        ec=$?
        echo "  dummy e2e: $([ $ec -eq 0 ] && echo PASS || echo FAIL (exit $ec))" >> "$log"
        [ $ec -eq 0 ] || ok=0
        (cd "$dir" && timeout 600 "$PYTHON" generic_recovery_tests.py \
            --backend dummy --num-examples 4 --seq-len 16) >> "$log" 2>&1
        ec=$?
        echo "  dummy recovery: $([ $ec -eq 0 ] && echo PASS || echo FAIL (exit $ec))" >> "$log"
        [ $ec -eq 0 ] || ok=0
    else
        echo "  dummy gate: NO EXECUTADA (falten scripts o dataset)"; ok=0
    fi

    echo "" >> "$log"
    echo "══ CLEAN EXTRACTION ${letter} — Overall: $([ $ok -eq 1 ] && echo PASS || echo FAIL) ══" >> "$log"
    [ $ok -eq 1 ] || OVERALL=1
    rm -rf "$dir"
    cat "$log"
}

run_ab A
echo ""
run_ab B

echo ""
echo "══════════════════════════════════════════"
echo " CLEAN EXTRACTION A/B — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)"
echo "══════════════════════════════════════════"
exit $OVERALL
