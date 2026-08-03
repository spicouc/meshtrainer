#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.3 Stage B R5 — Test Runner"
echo "============================================"
python3 -m py_compile *.py
: > RC5_3_RUN_1.log
: > RC5_3_RUN_2.log
echo ""; echo "=== RC5.3 Run 1 ==="
set +e; timeout 500 python3 rc5_3_tests.py > RC5_3_RUN_1.log 2>&1; R1=$?; set -e
echo "Run 1 exit: $R1"; [ $R1 -eq 0 ] || { echo "FAIL: Run 1"; exit 1; }
echo ""; echo "=== RC5.3 Run 2 ==="
set +e; timeout 500 python3 rc5_3_tests.py > RC5_3_RUN_2.log 2>&1; R2=$?; set -e
echo "Run 2 exit: $R2"; [ $R2 -eq 0 ] || { echo "FAIL: Run 2"; exit 1; }
echo "BOTH RUNS: PASS"
