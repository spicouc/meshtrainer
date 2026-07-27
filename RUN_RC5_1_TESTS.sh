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

echo ""
echo "=== RUN 1 ==="
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
    echo "============================================"
    exit 0
else
    echo "  Run 1: $([ $STATUS1 -eq 0 ] && echo PASS || echo FAIL)"
    echo "  Run 2: $([ $STATUS2 -eq 0 ] && echo PASS || echo FAIL)"
    echo "============================================"
    exit 1
fi
