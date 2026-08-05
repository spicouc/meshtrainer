#!/usr/bin/env bash
# RUN_RC5_4_ADVERSARIAL_GATE.sh — 14 probes mínimes RC5.4 (rc5_4_adversarial_tests.py)
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR
TMPDIR="${TMPDIR:-/tmp}"
"$PYTHON_BIN" rc5_4_adversarial_tests.py > "$LOG_DIR/RC5_4_ADVERSARIAL_GATE.log" 2>&1
EC=$?
echo "RC5.4 adversarial: $([ $EC -eq 0 ] && echo PASS || echo FAIL) (exit $EC)"
[ $EC -eq 0 ]
