#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 2 R8 — PREDELIVERY"
echo "============================================"
LOG=RC5_2_PHASE2_PREDELIVERY.log
: > "$LOG"

echo "[1/5] py_compile" | tee -a "$LOG"
python3 -m py_compile *.py >> "$LOG" 2>&1 || { echo "FAIL: py_compile" | tee -a "$LOG"; exit 1; }
echo "  py_compile: PASS" | tee -a "$LOG"

echo "[2/5] import check" | tee -a "$LOG"
python3 - <<'PY' >> "$LOG" 2>&1
import rc5_2_phase2_tests
print("phase2 imports: PASS")
PY
[ $? -eq 0 ] || { echo "FAIL: imports" | tee -a "$LOG"; exit 1; }

echo "[3/5] Phase 2 suite" | tee -a "$LOG"
timeout 600 python3 rc5_2_phase2_tests.py >> "$LOG" 2>&1 || { echo "FAIL: suite" | tee -a "$LOG"; exit 1; }
echo "  suite: PASS" | tee -a "$LOG"

echo "[4/5] mutation gate" | tee -a "$LOG"
timeout 2400 bash RUN_RC5_2_PHASE2_MUTATION_GATE.sh >> "$LOG" 2>&1 || { echo "FAIL: mutation" | tee -a "$LOG"; exit 1; }
echo "  mutation: PASS" | tee -a "$LOG"

echo "[5/5] forbidden patterns" | tee -a "$LOG"
if grep -RIn -e 'id(cut_activation)' -e 'COORDINATOR_CONTRIBUTIONS' -e 'rtol=1e-3' -e 'atol=1e-3' -e 'delta_data.hex()' --include='*.py' . 2>/dev/null; then
    echo "FAIL: forbidden patterns found" | tee -a "$LOG"; exit 1
fi
echo "  patterns: CLEAN" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "PREDELIVERY: PASS" | tee -a "$LOG"
