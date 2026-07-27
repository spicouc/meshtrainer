#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  RC5.1 — Tiny Split PoC Test Runner"
echo "============================================"
echo ""
echo "Python: $(python3 --version 2>&1)"
echo "Dir:    $(pwd)"
echo ""

# Install dependencies if not present
if ! python3 -c "import torch" 2>/dev/null; then
    echo "[SETUP] Installing dependencies..."
    pip install -r requirements-rc5.1.txt -q
    echo "[SETUP] Done."
fi

echo ""
echo "=== RUN 1 ==="
python3 rc5_tests.py 2>&1
STATUS1=$?

if [ "$STATUS1" -eq 0 ]; then
    echo ""
    echo "=== RUN 2 ==="
    # Reset keyring for run 2
    rm -f /tmp/rc5_keyring.json
    python3 rc5_tests.py 2>&1
    STATUS2=$?
else
    STATUS2=1
fi

echo ""
echo "============================================"
echo "  RESULTS"
echo "============================================"
if [ "$STATUS1" -eq 0 ] && [ "$STATUS2" -eq 0 ]; then
    echo "  Run 1: PASS"
    echo "  Run 2: PASS"
    echo "  0 flaky"
    exit 0
else
    echo "  Run 1: $([ $STATUS1 -eq 0 ] && echo PASS || echo FAIL)"
    echo "  Run 2: $([ $STATUS2 -eq 0 ] && echo PASS || echo FAIL)"
    exit 1
fi
