#!/usr/bin/env bash
# RUN_RC5_4_TESTS.sh — RC5.4 NORMAL x2 REAL: la MATEIXA suite (crash matrix)
# executada dues vegades (R12). L'adversarial es reporta separadament.
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR
TMPDIR="${TMPDIR:-/tmp}"

echo "=== RUN 1 (suite normal completa) ==="
"$PYTHON_BIN" rc5_4_tests.py > "$LOG_DIR/RC5_4_RUN_1.log" 2>&1; R1=$?
grep -E "PASS|FAIL" "$LOG_DIR/RC5_4_RUN_1.log" | tail -1

echo "=== RUN 2 (suite normal completa) ==="
"$PYTHON_BIN" rc5_4_tests.py > "$LOG_DIR/RC5_4_RUN_2.log" 2>&1; R2=$?
grep -E "PASS|FAIL" "$LOG_DIR/RC5_4_RUN_2.log" | tail -1

echo "=== adversarial (report separat) ==="
"$PYTHON_BIN" rc5_4_adversarial_tests.py > "$LOG_DIR/RC5_4_ADVERSARIAL_GATE.log" 2>&1; RA=$?
grep -E "PASS|FAIL" "$LOG_DIR/RC5_4_ADVERSARIAL_GATE.log" | tail -1

if [ $R1 -eq 0 ] && [ $R2 -eq 0 ]; then
    echo "RC5.4 normal x2: PASS (R1=$R1 R2=$R2) | adversarial: $([ $RA -eq 0 ] && echo PASS || echo FAIL)"
else
    echo "RC5.4 normal x2: FAIL (R1=$R1 R2=$R2) | adversarial: $([ $RA -eq 0 ] && echo PASS || echo FAIL)"
fi
[ $R1 -eq 0 ] && [ $R2 -eq 0 ] && [ $RA -eq 0 ]
