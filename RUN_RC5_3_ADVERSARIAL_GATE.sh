#!/usr/bin/env bash
# RC5.3 Stage B R6 — ADVERSARIAL GATE (33/33, escriu a ${LOG_DIR})
set -euo pipefail
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
echo "============================================"
echo "  RC5.3 Stage B R6 — ADVERSARIAL GATE (33/33)"
echo "  LOG_DIR: $LOG_DIR"
echo "============================================"
python3 -m py_compile rc5_3_adversarial_tests.py rc5_3_multiworker.py rc5_2_http_server.py
echo ""; echo "=== Adversarial suite ==="
set +e; timeout 500 python3 rc5_3_adversarial_tests.py > "$LOG_DIR/RC5_3_ADVERSARIAL_GATE.log" 2>&1; S=$?; set -e
echo "Exit: $S"
tail -2 "$LOG_DIR/RC5_3_ADVERSARIAL_GATE.log"
[ $S -eq 0 ]
