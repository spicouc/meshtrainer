#!/usr/bin/env bash
# RUN_RC5_4_TESTS.sh — RC5.4 normal x2 (crash matrix + adversarial), LOG_DIR portable
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR
TMPDIR="${TMPDIR:-/tmp}"
"$PYTHON_BIN" rc5_4_tests.py > "$LOG_DIR/RC5_4_RUN_1.log" 2>&1; R1=$?
"$PYTHON_BIN" rc5_4_adversarial_tests.py > "$LOG_DIR/RC5_4_RUN_2.log" 2>&1; R2=$?
echo "RC5.4 normal x2: $([ $R1 -eq 0 ] && [ $R2 -eq 0 ] && echo PASS || echo FAIL) ($R1/$R2)"
[ $R1 -eq 0 ] && [ $R2 -eq 0 ]
