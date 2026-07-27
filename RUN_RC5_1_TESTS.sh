#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  RC5.1 P0 — Test Runner"
echo "============================================"
echo "Python: $(python3 --version 2>&1)"
echo "Dir:    $(pwd)"
echo ""

# Install deps if missing
python3 -c "import torch" 2>/dev/null || pip install -r requirements-rc5.1.txt -q
python3 -c "import requests" 2>/dev/null || pip install requests -q

# Verify all local dependencies exist
for mod in rc5_receipt.py rc5_db.py rc5_coordinator_ext.py rc5_split_server.py \
           rc3_coordinator.py rc3_state.py aggregation.py rc3_worker_sim.py \
           rc3_train_state.py synthetic_tensor_store.py; do
    if [ ! -f "$mod" ]; then
        echo "Missing dependency: $mod"
        exit 1
    fi
done
echo "All local deps present."

echo ""
echo "=== RUN 1 ==="
rm -f /tmp/rc5_keyring.json
set +e
python3 rc5_tests.py
STATUS1=$?
set -e

if [ "$STATUS1" -ne 0 ]; then
    echo ""
    echo "=== RUN 1: FAIL (exit $STATUS1) ==="
    echo ""
    echo "============================================"
    echo "  Run 1: FAIL"
    echo "  Run 2: SKIPPED"
    echo "============================================"
    exit 1
fi

echo ""
echo "=== RUN 2 ==="
rm -f /tmp/rc5_keyring.json
set +e
python3 rc5_tests.py
STATUS2=$?
set -e

echo ""
echo "============================================"
if [ "$STATUS1" -eq 0 ] && [ "$STATUS2" -eq 0 ]; then
    echo "  Run 1: PASS"
    echo "  Run 2: PASS"
    echo "  0 flaky"
    echo "  0 mandatory skip"
    echo "============================================"
    exit 0
else
    echo "  Run 1: $([ $STATUS1 -eq 0 ] && echo PASS || echo FAIL)"
    echo "  Run 2: $([ $STATUS2 -eq 0 ] && echo PASS || echo FAIL)"
    echo "============================================"
    exit 1
fi
