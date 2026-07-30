#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 2 R6 — Test Runner"
echo "============================================"
python3 -m py_compile *.py
echo ""; echo "=== Phase 2 Run 1 ==="
set +e; timeout 300 python3 rc5_2_phase2_tests.py > RC5_2_PHASE2_RUN_1.log 2>&1; echo "Exit: $?"; set -e
echo ""; echo "=== Phase 2 Run 2 ==="
set +e; timeout 300 python3 rc5_2_phase2_tests.py > RC5_2_PHASE2_RUN_2.log 2>&1; echo "Exit: $?"; set -e
tail -3 RC5_2_PHASE2_RUN_1.log RC5_2_PHASE2_RUN_2.log
