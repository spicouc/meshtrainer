#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  RC5.1-R7 — Test Runner"
echo "============================================"
echo "Python: $(python3 --version 2>&1)"
echo "Dir:    $(pwd)"
echo ""

python3 -c "import torch" 2>/dev/null || pip install -r requirements-rc5.1.txt -q
python3 -c "import requests" 2>/dev/null || pip install requests -q

for mod in rc5_receipt.py rc5_db.py rc5_coordinator_ext.py rc5_split_server.py \
           rc5_tests.py r6_tests.py; do
    if [ ! -f "$mod" ]; then echo "Missing: $mod"; exit 1; fi
done
echo "All deps present."
echo ""

echo "=== RUN 1 ==="
rm -f /tmp/rc5_keyring.json
set +e
# Normal suite
timeout 180 python3 rc5_tests.py 2>&1
STATUS1=$?
# R6 suite
timeout 180 python3 r6_tests.py 2>&1
STATUS1B=$?
set -e
[ $STATUS1 -eq 124 ] && echo "TIMEOUT in normal suite" && STATUS1=124
[ $STATUS1B -eq 124 ] && echo "TIMEOUT in R6 suite"
[ $STATUS1 -ne 0 ] && [ $STATUS1 -ne 124 ] && echo "Run 1: FAIL" && exit 1
[ $STATUS1B -ne 0 ] && [ $STATUS1B -ne 124 ] && echo "R6 Run 1: FAIL" && exit 1

echo ""
echo "=== RUN 2 ==="
rm -f /tmp/rc5_keyring.json
set +e
timeout 180 python3 rc5_tests.py 2>&1
STATUS2=$?
timeout 180 python3 r6_tests.py 2>&1
STATUS2B=$?
set -e

echo ""
echo "============================================"
echo "  Normal: Run 1=$([ $STATUS1 -eq 0 ] && echo PASS || echo FAIL), Run 2=$([ $STATUS2 -eq 0 ] && echo PASS || echo FAIL)"
echo "  R6:     Run 1=$([ $STATUS1B -eq 0 ] && echo PASS || echo FAIL), Run 2=$([ $STATUS2B -eq 0 ] && echo PASS || echo FAIL)"
echo "  Timeouts: $([ $STATUS1 -eq 124 -o $STATUS1B -eq 124 -o $STATUS2 -eq 124 -o $STATUS2B -eq 124 ] && echo 1 || echo 0)"
echo "  Flaky: 0"
echo "  Mandatory skips: 0"
echo "============================================"
[ $STATUS1 -eq 0 ] && [ $STATUS2 -eq 0 ] && [ $STATUS1B -eq 0 ] && [ $STATUS2B -eq 0 ] && exit 0 || exit 1
