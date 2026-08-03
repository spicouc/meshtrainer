#!/usr/bin/env bash
# RC5.2 Phase 2 R12 — ADVERSARIAL GATE (escriu a ${LOG_DIR})
set -euo pipefail
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
echo "============================================"
echo "  RC5.2 Phase 2 R12 — ADVERSARIAL GATE"
echo "  LOG_DIR: $LOG_DIR"
echo "============================================"
python3 -m py_compile *.py
echo ""; echo "=== Adversarial suite ==="
set +e; timeout 300 python3 rc5_2_adversarial_tests.py > "$LOG_DIR/RC5_2_PHASE2_ADVERSARIAL.log" 2>&1; S=$?; set -e
echo "Exit: $S"
tail -3 "$LOG_DIR/RC5_2_PHASE2_ADVERSARIAL.log"
[ $S -eq 0 ]
