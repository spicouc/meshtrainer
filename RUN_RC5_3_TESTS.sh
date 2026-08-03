#!/usr/bin/env bash
# RC5.3 Stage B R6 — Test Runner (escriu a ${LOG_DIR})
set -euo pipefail
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
echo "============================================"
echo "  RC5.3 Stage B R6 — Test Runner"
echo "  LOG_DIR: $LOG_DIR"
echo "============================================"
python3 -m py_compile *.py
: > "$LOG_DIR/RC5_3_RUN_1.log"
: > "$LOG_DIR/RC5_3_RUN_2.log"
echo ""; echo "=== RC5.3 Run 1 ==="
set +e; timeout 500 python3 rc5_3_tests.py > "$LOG_DIR/RC5_3_RUN_1.log" 2>&1; R1=$?; set -e
echo "Run 1 exit: $R1"; [ $R1 -eq 0 ] || { echo "FAIL: Run 1"; exit 1; }
echo ""; echo "=== RC5.3 Run 2 ==="
set +e; timeout 500 python3 rc5_3_tests.py > "$LOG_DIR/RC5_3_RUN_2.log" 2>&1; R2=$?; set -e
echo "Run 2 exit: $R2"; [ $R2 -eq 0 ] || { echo "FAIL: Run 2"; exit 1; }
echo "BOTH RUNS: PASS"
