#!/usr/bin/env bash
# RUN_RC5_3_MANIFEST_PROBE.sh — prova negativa del runner (R6.1)
# Altera temporalment un fitxer, executa el final gate i exigeix:
# manifest FAIL, resum complet, Overall FAIL, exit no-zero.
set -uo pipefail
SRC="$(pwd)"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
export LOG_DIR
echo "=== MANIFEST FAILURE PROBE ==="
echo "LOG_DIR: $LOG_DIR"

# 1. còpia temporal del paquet (sense .venv/.git)
PROBE="$(mktemp -d "${TMPDIR:-/tmp}/mf_probe_XXXXXX")"
cp -r "$SRC"/. "$PROBE"/
cd "$PROBE"
rm -rf .venv .git __pycache__ 2>/dev/null || true

# 2. alterar un fitxer cobert pel manifest (canvia el seu hash)
echo "# probe alteration" >> rc5_3_adversarial_tests.py
echo "fitxer alterat: rc5_3_adversarial_tests.py (hash canviat)"

# 3. executar el final gate (ha de fallar al pas manifest i continuar fins al resum)
export PYTHON_BIN="${PYTHON_BIN:-python3}" TMPDIR="${TMPDIR:-/tmp}"
RC53_SKIP_CLEAN_EXTRACTION=1 timeout 400 bash RUN_RC5_3_FINAL_GATE.sh > "$LOG_DIR/MANIFEST_FAILURE_PROBE.log" 2>&1
GATE_EC=$?

# 4. comprovar el comportament esperat
MF_FAIL=$(grep -c "manifest: FAIL" "$LOG_DIR/MANIFEST_FAILURE_PROBE.log")
RESUM=$(grep -c "COMBINED GATE — Overall: FAIL" "$LOG_DIR/MANIFEST_FAILURE_PROBE.log")
echo "gate exit: $GATE_EC (ha de ser no-zero) | manifest FAIL: $MF_FAIL | resum FAIL: $RESUM"
[ $GATE_EC -ne 0 ] || { echo "PROBE: FAIL (exit 0)"; exit 1; }
[ "$MF_FAIL" -ge 1 ] || { echo "PROBE: FAIL (manifest check no va fallar)"; exit 1; }
[ "$RESUM" -ge 1 ] || { echo "PROBE: FAIL (resum no imprès)"; exit 1; }

# 5. neteja
rm -rf "$PROBE"
echo "manifest failure probe: PASS"
echo "  log: $LOG_DIR/MANIFEST_FAILURE_PROBE.log"
