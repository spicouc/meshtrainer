#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.3 Stage B R5 — ADVERSARIAL GATE (28/28)"
echo "============================================"
python3 -m py_compile rc5_3_adversarial_tests.py rc5_3_multiworker.py rc5_2_http_server.py
echo ""; echo "=== Adversarial suite ==="
set +e; timeout 500 python3 rc5_3_adversarial_tests.py > RC5_3_ADVERSARIAL_GATE.log 2>&1; S=$?; set -e
echo "Exit: $S"
tail -2 RC5_3_ADVERSARIAL_GATE.log
[ $S -eq 0 ]
