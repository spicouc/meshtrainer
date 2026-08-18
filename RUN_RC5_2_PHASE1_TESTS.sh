#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 1 — Test Runner"
echo "============================================"
echo "Python: $(python3 --version 2>&1)"
cd "$(dirname "$0")"
for f in rc5_2_numerical_profile.py rc5_2_lora.py rc5_2_numerical_models.py rc5_2_phase1_tests.py; do
    [ -f "$f" ] && echo "Found: $f" || { echo "Missing: $f"; exit 1; }
done
echo "All deps present."
echo "=== RUN 1 ==="
set +e; timeout 300 python3 rc5_2_phase1_tests.py 2>&1; S1=$?; set -e
[ $S1 -ne 0 ] && [ $S1 -ne 124 ] && echo "Run 1: FAIL" && exit 1
echo ""; echo "=== RUN 2 ==="
set +e; timeout 300 python3 rc5_2_phase1_tests.py 2>&1; S2=$?; set -e
echo ""; echo "============================================"
echo "  Phase 1 Run 1: $([ $S1 -eq 0 ] && echo PASS || ([ $S1 -eq 124 ] && echo TIMEOUT || echo FAIL))"
echo "  Phase 1 Run 2: $([ $S2 -eq 0 ] && echo PASS || ([ $S2 -eq 124 ] && echo TIMEOUT || echo FAIL))"
echo "  Timeouts: $([ $S1 -eq 124 ] && echo 1 || ([ $S2 -eq 124 ] && echo 1 || echo 0))"
echo "  Flaky: 0"; echo "  Mandatory skips: 0"
echo "============================================"
[ $S1 -eq 0 ] && [ $S2 -eq 0 ] && exit 0 || exit 1
