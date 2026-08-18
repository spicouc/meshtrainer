#!/usr/bin/env bash
# RC5.2 Phase 2 R12 — Test Runner (escriu a ${LOG_DIR})
set -euo pipefail
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
echo "============================================"
echo "  RC5.2 Phase 2 R12 — Test Runner"
echo "  LOG_DIR: $LOG_DIR"
echo "============================================"
python3 -m py_compile *.py
echo ""; echo "=== Phase 2 Run 1 ==="
set +e; timeout 300 python3 rc5_2_phase2_tests.py > "$LOG_DIR/RC5_2_PHASE2_RUN_1.log" 2>&1; R1=$?; set -e
echo "Run 1 exit: $R1"
[ $R1 -eq 0 ] || { echo "FAIL: Run 1"; exit 1; }
echo ""; echo "=== Phase 2 Run 2 ==="
set +e; timeout 300 python3 rc5_2_phase2_tests.py > "$LOG_DIR/RC5_2_PHASE2_RUN_2.log" 2>&1; R2=$?; set -e
echo "Run 2 exit: $R2"
[ $R2 -eq 0 ] || { echo "FAIL: Run 2"; exit 1; }
echo "BOTH RUNS: PASS"
