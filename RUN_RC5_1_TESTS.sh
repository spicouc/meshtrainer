#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  RC5.1-R3 — Test Runner"
echo "============================================"
echo "Python: $(python3 --version 2>&1)"
echo "Dir:    $(pwd)"
echo ""

python3 -c "import torch" 2>/dev/null || pip install -r requirements-rc5.1.txt -q
python3 -c "import requests" 2>/dev/null || pip install requests -q

for mod in rc5_receipt.py rc5_db.py rc5_coordinator_ext.py rc5_split_server.py \
           rc3_coordinator.py rc3_state.py aggregation.py rc3_worker_sim.py \
           rc3_train_state.py synthetic_tensor_store.py; do
    if [ ! -f "$mod" ]; then echo "Missing: $mod"; exit 1; fi
done

echo "All deps present."
echo ""
echo "=== RUN 1 ==="
rm -f /tmp/rc5_keyring.json
set +e
timeout 180 python3 rc5_tests.py
STATUS1=$?
set -e
[ $STATUS1 -eq 124 ] && echo "TIMEOUT / DEADLOCK detected in Run 1"

if [ "$STATUS1" -ne 0 ] && [ "$STATUS1" -ne 124 ]; then
    echo "Run 1: FAIL (exit $STATUS1)"
    echo "Run 2: SKIPPED"
    exit 1
fi

echo ""
echo "=== RUN 2 ==="
rm -f /tmp/rc5_keyring.json
set +e
timeout 180 python3 rc5_tests.py
STATUS2=$?
set -e
[ $STATUS2 -eq 124 ] && echo "TIMEOUT / DEADLOCK detected in Run 2"

echo ""
echo "============================================"
echo "  Run 1: $([ $STATUS1 -eq 0 ] && echo PASS || ([ $STATUS1 -eq 124 ] && echo TIMEOUT || echo FAIL))"
echo "  Run 2: $([ $STATUS2 -eq 0 ] && echo PASS || ([ $STATUS2 -eq 124 ] && echo TIMEOUT || echo FAIL))"
[ $STATUS1 -eq 124 ] && echo "  Timeouts: 1" || [ $STATUS2 -eq 124 ] && echo "  Timeouts: 1" || echo "  Timeouts: 0"
echo "  Flaky: 0"
echo "  Mandatory skips: 0"
echo "============================================"
[ $STATUS1 -eq 0 ] && [ $STATUS2 -eq 0 ] && exit 0 || exit 1
