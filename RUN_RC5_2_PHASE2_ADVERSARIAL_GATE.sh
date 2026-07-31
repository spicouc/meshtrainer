#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 2 R11 — ADVERSARIAL GATE"
echo "============================================"
python3 -m py_compile *.py
echo ""; echo "=== Adversarial suite ==="
set +e; timeout 300 python3 rc5_2_adversarial_tests.py > RC5_2_PHASE2_ADVERSARIAL.log 2>&1; S=$?; set -e
echo "Exit: $S"
tail -3 RC5_2_PHASE2_ADVERSARIAL.log
[ $S -eq 0 ]
