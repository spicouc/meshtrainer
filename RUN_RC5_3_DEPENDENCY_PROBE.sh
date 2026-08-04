#!/usr/bin/env bash
# RUN_RC5_3_DEPENDENCY_PROBE.sh — prova negativa del runner (R6.1)
# Retira temporalment una dependència, executa el final gate i exigeix:
# dependency check FAIL, resum complet, Overall FAIL, exit no-zero.
set -uo pipefail
SRC="$(pwd)"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
export LOG_DIR
echo "=== DEPENDENCY FAILURE PROBE ==="
echo "LOG_DIR: $LOG_DIR"

# 1. còpia temporal del paquet (sense .venv/.git)
PROBE="$(mktemp -d "${TMPDIR:-/tmp}/dep_probe_XXXXXX")"
cp -r "$SRC"/. "$PROBE"/
cd "$PROBE"
rm -rf .venv .git __pycache__ 2>/dev/null || true

# 2. retirar una dependència essencial
mv rc5_3_multiworker.py rc5_3_multiworker.py.bak
echo "dependència retirada: rc5_3_multiworker.py"

# 3. executar el final gate (ha de fallar al pas 1 i continuar fins al resum)
export PYTHON_BIN="${PYTHON_BIN:-python3}" TMPDIR="${TMPDIR:-/tmp}"
RC53_SKIP_CLEAN_EXTRACTION=1 timeout 3600 bash RUN_RC5_3_FINAL_GATE.sh > "$LOG_DIR/DEPENDENCY_FAILURE_PROBE.log" 2>&1
GATE_EC=$?

# 4. comprovar el comportament esperat
DEP_FAIL=$(grep -c "dependency check: FAIL" "$LOG_DIR/DEPENDENCY_FAILURE_PROBE.log")
RESUM=$(grep -c "COMBINED GATE — Overall: FAIL" "$LOG_DIR/DEPENDENCY_FAILURE_PROBE.log")
echo "gate exit: $GATE_EC (ha de ser no-zero) | dependency FAIL: $DEP_FAIL | resum FAIL: $RESUM"
[ $GATE_EC -ne 0 ] || { echo "PROBE: FAIL (exit 0)"; exit 1; }
[ "$DEP_FAIL" -ge 1 ] || { echo "PROBE: FAIL (dependency check no va fallar)"; exit 1; }
[ "$RESUM" -ge 1 ] || { echo "PROBE: FAIL (resum no imprès)"; exit 1; }

# 5. neteja
rm -rf "$PROBE"
echo "dependency failure probe: PASS"
echo "  log: $LOG_DIR/DEPENDENCY_FAILURE_PROBE.log"
