#!/usr/bin/env bash
# RUN_RC5_4_FINAL_GATE.sh — RC5.4 Stage A combined gate (16 passos, portable)
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
PYBIN_DIR="$(dirname "$PYTHON_BIN")"
if [ "$PYBIN_DIR" != "." ] && [ -x "$PYTHON_BIN" ]; then
    export PATH="$PYBIN_DIR:$PATH"
fi
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR
LOG="$LOG_DIR/RC5_4_FINAL_GATE.log"
: > "$LOG"
OVERALL=0

run_sub() {
    local label="$1" cmd="$2" tmo="${3:-300}"
    echo ""; echo "=== $label ===" | tee -a "$LOG"
    timeout "$tmo" bash -c "$cmd" >> "$LOG" 2>&1; EC=$?
    echo "  $label: $([ $EC -eq 0 ] && echo PASS || echo FAIL) (exit $EC)" | tee -a "$LOG"
    [ $EC -eq 0 ] || OVERALL=1
    return $EC
}

echo "============================================" | tee -a "$LOG"
echo "  RC5.4 Stage A — COMBINED FINAL GATE" | tee -a "$LOG"
echo "  Python: $($PYTHON_BIN --version 2>&1)" | tee -a "$LOG"
echo "  LOG_DIR: $LOG_DIR" | tee -a "$LOG"
echo "============================================" | tee -a "$LOG"

# 1) dependency check
"$PYTHON_BIN" - <<'PY' >> "$LOG" 2>&1
import os, sys
need = ["rc5_4_leases.py", "rc5_4_tests.py", "rc5_4_adversarial_tests.py",
        "rc5_3_multiworker.py", "rc5_2_worker_runtime.py", "rc5_2_http_server.py",
        "rc5_2_coordinator.py", "rc5_2_tensor_bundle.py", "rc5_2_tensor_envelope.py",
        "rc5_2_canonical.py", "rc5_2_artifacts.py", "rc5_2_numerical_adapter.py"]
missing = [f for f in need if not os.path.exists(f)]
if missing:
    print(f"dependency check: FAIL ({missing})")
    sys.exit(1)
try:
    import torch, numpy, httpx, requests  # noqa
    print(f"dependency check: PASS (torch {torch.__version__}, numpy {numpy.__version__})")
except ImportError as e:
    print(f"dependency check: FAIL ({e})")
    sys.exit(1)
PY
EC=$?
echo "  [1/17] dependency check: $([ $EC -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
[ $EC -eq 0 ] || OVERALL=1

# 2) py_compile
"$PYTHON_BIN" -m py_compile rc5_4_leases.py rc5_4_tests.py rc5_4_adversarial_tests.py \
    rc5_3_multiworker.py rc5_2_*.py rc5_1_*.py 2>> "$LOG"; PC=$?
echo "  [2/17] py_compile: $([ $PC -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
[ $PC -eq 0 ] || OVERALL=1

# 3) RC5.4 normal x2 (crash matrix + adversarial, runner oficial)
run_sub "[3/17] RC5.4 normal x2" "bash RUN_RC5_4_TESTS.sh" 900

# 4) RC5.4 adversarial (runner oficial)
run_sub "[4/17] RC5.4 adversarial" "bash RUN_RC5_4_ADVERSARIAL_GATE.sh" 600

# 5) RC5.4 mutation (runner oficial)
run_sub "[5/17] RC5.4 mutation" "bash RUN_RC5_4_MUTATION_GATE.sh" 3600

# 6) RC5.3 combined gate (regressió, runner R6.1)
run_sub "[6/17] RC5.3 combined" "bash RUN_RC5_3_FINAL_GATE.sh" 3600

# 7) RC5.2 Phase 2 (regressió, runner R6.1)
run_sub "[7/17] RC5.2 Phase 2" "bash RUN_RC5_2_PHASE2_FINAL_GATE.sh" 1800

# 8) Phase 1 (regressió)
run_sub "[8/17] Phase 1" "bash RUN_RC5_2_PHASE1_TESTS.sh" 900

# 9) RC5.1 (regressió)
run_sub "[9/17] RC5.1" "bash RUN_RC5_1_TESTS.sh" 1200

# 10) restart subprocess REAL (RC5.4, amb PID inicial/recuperat)
run_sub "[10/17] restart subprocess real" "bash RUN_RC5_4_RESTART_GATE.sh" 600

# 11) controlled-failure RC5.3 (regressió)
run_sub "[11/17] controlled-failure RC5.3" "bash RUN_RC5_3_CONTROLLED_FAILURE.sh" 900

# 12) controlled-failure RC5.2 (regressió)
run_sub "[12/17] controlled-failure RC5.2" "bash RUN_RC5_2_CONTROLLED_FAILURE.sh" 600

# 13) controlled-failure RC5.4 (còpia temporal, una assertion)
run_sub "[13/17] controlled-failure RC5.4" "bash RUN_RC5_4_CONTROLLED_FAILURE.sh" 600

# 14) dependency probe (negativa)
run_sub "[14/17] dependency probe" "bash RUN_RC5_3_DEPENDENCY_PROBE.sh" 3600

# 15) manifest probe (negativa)
run_sub "[15/17] manifest probe" "bash RUN_RC5_3_MANIFEST_PROBE.sh" 3600

# 16) manifest complet (regulars - 1)
FILES=$(find . -type f ! -path './.venv/*' ! -path './.git/*' ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name 'MANIFEST.sha256' | wc -l)
MF=$(wc -l < MANIFEST.sha256 2>/dev/null || echo 0)
if [ "$((FILES - 1))" -eq "$MF" ] && [ -f MANIFEST.sha256 ]; then
    ERR=$(sha256sum -c MANIFEST.sha256 2>&1 | grep -cv ': OK')
    echo "  [16/17] manifest: $([ "$ERR" -eq 0 ] && echo PASS || echo FAIL) (regulars=$FILES manifest=$MF err=$ERR)" | tee -a "$LOG"
    [ "$ERR" -eq 0 ] || OVERALL=1
else
    echo "  [16/17] manifest: FAIL (regulars=$FILES manifest=$MF, esperat $((FILES-1)))" | tee -a "$LOG"
    OVERALL=1
fi

# 17) clean extraction (mode delegat: SKIP si RC54_SKIP_CLEAN_EXTRACTION=1)
if [ "${RC54_SKIP_CLEAN_EXTRACTION:-0}" = "1" ]; then
    echo "  [17/17] clean extraction: SKIPPED (mode delegat)" | tee -a "$LOG"
else
    run_sub "[17/17] clean extraction" "bash RUN_RC5_4_CLEAN_EXTRACTION_GATE.sh ${RC54_TARBALL:-meshtrainer_rc54_stagea_candidate.tar.gz}" 5400
fi

echo ""; echo "============================================" | tee -a "$LOG"
echo "  RC5.4 COMBINED GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
echo "============================================" | tee -a "$LOG"
exit $OVERALL
