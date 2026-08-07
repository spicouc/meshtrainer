#!/usr/bin/env bash
# RUN_RC5_4_HTTP_LEASE_GATE.sh — adversarials HTTP reals (lease-gated dispatcher)
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR
"$PYTHON_BIN" rc5_4_http_lease_tests.py > "$LOG_DIR/HTTP_LEASE_ADVERSARIAL.log" 2>&1; R=$?
grep -E "PASS|FAIL" "$LOG_DIR/HTTP_LEASE_ADVERSARIAL.log" | tail -1
echo "HTTP lease adversarial: $([ $R -eq 0 ] && echo PASS || echo FAIL) (exit $R)"
[ $R -eq 0 ]
