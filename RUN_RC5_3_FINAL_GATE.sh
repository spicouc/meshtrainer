#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.3 Stage B R5 — COMBINED FINAL GATE (20)"
echo "============================================"
LOG=RC5_3_FINAL_GATE.log
: > "$LOG"
OVERALL=0

run_sub() {
    local label="$1" cmd="$2" timeout="$3"
    echo ""; echo "=== $label ===" >> "$LOG"
    set +e; timeout "$timeout" bash -c "$cmd" >> "$LOG" 2>&1; local ec=$?; set -e
    if [ $ec -eq 124 ]; then echo "  $label: TIMEOUT" | tee -a "$LOG"; OVERALL=1
    elif [ $ec -eq 0 ]; then echo "  $label: PASS" | tee -a "$LOG"
    else echo "  $label: FAIL" | tee -a "$LOG"; OVERALL=1; fi
}

# --- 1) dependency check ---
echo ""; echo "=== [1/20] dependency check ===" | tee -a "$LOG"
python3 - <<'PY' >> "$LOG" 2>&1
import os, sys
need = ["rc5_3_multiworker.py","rc5_3_tests.py","rc5_3_adversarial_tests.py",
        "rc5_2_http_server.py","rc5_2_worker_runtime.py",
        "rc5_2_tensor_bundle.py","rc5_2_tensor_envelope.py","rc5_2_receipt.py",
        "rc5_2_coordinator.py","rc5_2_artifacts.py","rc5_2_canonical.py",
        "rc5_2_numerical_models.py","rc5_2_numerical_profile.py","rc5_2_lora.py",
        "rc5_2_jsonrpc.py","rc5_2_numerical_adapter.py","rc5_2_state.py",
        "RUN_RC5_2_PHASE2_TESTS.sh","RUN_RC5_2_PHASE2_ADVERSARIAL_GATE.sh",
        "RUN_RC5_2_PHASE2_MUTATION_GATE.sh","RUN_RC5_2_PHASE1_TESTS.sh",
        "RUN_RC5_2_PHASE1_MUTATION_GATE.sh","RUN_RC5_1_TESTS.sh","RUN_RC5_1_MUTATION_GATE.sh",
        "RUN_RC5_3_TESTS.sh","RUN_RC5_3_MUTATION_GATE.sh","RUN_RC5_3_ADVERSARIAL_GATE.sh",
        "MANIFEST.sha256"]
missing = [f for f in need if not os.path.exists(f)]
if missing:
    print("MISSING:", missing); sys.exit(1)
print("deps: OK")
PY
[ $? -eq 0 ] && echo "  dep check: PASS" | tee -a "$LOG" || { echo "  dep check: FAIL" | tee -a "$LOG"; OVERALL=1; }

# --- 2) py_compile ---
echo ""; echo "=== [2/20] py_compile ===" | tee -a "$LOG"
set +e; python3 -m py_compile *.py >> "$LOG" 2>&1; PC=$?; set -e
[ $PC -eq 0 ] && echo "  py_compile: PASS" | tee -a "$LOG" || { echo "  py_compile: FAIL" | tee -a "$LOG"; OVERALL=1; }

# --- 3/4) RC5.3 normal x2 ---
run_sub "[3/20] RC5.3 normal Run 1" "bash RUN_RC5_3_TESTS.sh" 1200
# Run 2 is the second run inside RUN_RC5_3_TESTS.sh; keep the explicit step:
run_sub "[4/20] RC5.3 normal Run 2" "python3 rc5_3_tests.py" 600
# --- 5) RC5.3 adversarial ---
run_sub "[5/20] RC5.3 adversarial" "bash RUN_RC5_3_ADVERSARIAL_GATE.sh" 700
# --- 6) RC5.3 mutation ---
run_sub "[6/20] RC5.3 mutation" "bash RUN_RC5_3_MUTATION_GATE.sh" 5000

# --- 7/8) RC5.2 normal x2 ---
run_sub "[7/20] RC5.2 normal Run 1" "python3 rc5_2_phase2_tests.py" 400
run_sub "[8/20] RC5.2 normal Run 2" "python3 rc5_2_phase2_tests.py" 400
# --- 9) RC5.2 adversarial ---
run_sub "[9/20] RC5.2 adversarial" "bash RUN_RC5_2_PHASE2_ADVERSARIAL_GATE.sh" 500
# --- 10) RC5.2 mutation ---
run_sub "[10/20] RC5.2 mutation" "bash RUN_RC5_2_PHASE2_MUTATION_GATE.sh" 5000

# --- 11/12) Phase 1 x2 ---
run_sub "[11/20] Phase 1 Run 1" "python3 rc5_2_phase1_tests.py" 400
run_sub "[12/20] Phase 1 Run 2" "python3 rc5_2_phase1_tests.py" 400
# --- 13) Phase 1 mutation ---
run_sub "[13/20] Phase 1 mutation" "bash RUN_RC5_2_PHASE1_MUTATION_GATE.sh" 2500

# --- 14/15) RC5.1 x2 ---
run_sub "[14/20] RC5.1 Run 1" "bash RUN_RC5_1_TESTS.sh" 700
run_sub "[15/20] RC5.1 Run 2" "bash RUN_RC5_1_TESTS.sh" 700
# --- 16) RC5.1 mutation ---
run_sub "[16/20] RC5.1 mutation" "bash RUN_RC5_1_MUTATION_GATE.sh" 2500

# --- 17) restart subprocess ---
echo ""; echo "=== [17/20] restart subprocess ===" | tee -a "$LOG"
set +e; timeout 400 python3 -c "
import rc5_3_tests as t
t.test_process_restart_real()
print('restart subprocess: PASS')
" >> "$LOG" 2>&1; RS=$?; set -e
[ $RS -eq 0 ] && echo "  restart subprocess: PASS" | tee -a "$LOG" || { echo "  restart subprocess: FAIL" | tee -a "$LOG"; OVERALL=1; }

# --- 18) controlled-failure (REAL: copy of suite, ONE assertion broken) ---
echo ""; echo "=== [18/20] controlled-failure ===" | tee -a "$LOG"
CF_DIR="${TMPDIR:-/tmp}/rc53_cf_r5_$$"
mkdir -p "$CF_DIR"
cp rc5_3_tests.py "$CF_DIR"/rc5_3_tests.py
# break exactly one assertion: W1 (first check of the first test)
sed -i 's/check("W1: two workers completed"/check("W1: two workers completed", False)/' "$CF_DIR/rc5_3_tests.py"
set +e; (cd "$CF_DIR" && timeout 300 python3 rc5_3_tests.py > cf.log 2>&1); CF=$?; set -e
rm -rf "$CF_DIR"
if [ $CF -ne 0 ]; then echo "  controlled-failure: PASS (suite FAIL detected)" | tee -a "$LOG"
else echo "  controlled-failure: FAIL (broken suite passed!)" | tee -a "$LOG"; OVERALL=1; fi

# --- 19) manifest ---
echo ""; echo "=== [19/20] manifest ===" | tee -a "$LOG"
set +e; sha256sum -c MANIFEST.sha256 >> "$LOG" 2>&1; MF=$?; set -e
[ $MF -eq 0 ] && echo "  manifest: PASS (ALL FILES OK)" | tee -a "$LOG" || echo "  manifest: FAIL" | tee -a "$LOG"

# --- 20) clean extraction self-audit ---
echo ""; echo "=== [20/20] clean extraction ===" | tee -a "$LOG"
TARBALL=$(ls meshtrainer_rc53_stageb_r5_final_*.tar.gz 2>/dev/null | head -1 || true)
if [ -n "$TARBALL" ]; then
    EX_DIR="${TMPDIR:-/tmp}/rc53_extract_$$"
    rm -rf "$EX_DIR"; mkdir -p "$EX_DIR"
    tar -xzf "$TARBALL" -C "$EX_DIR"
    set +e; (cd "$EX_DIR" && sha256sum -c MANIFEST.sha256 > /dev/null 2>&1); EX=$?; set -e
    rm -rf "$EX_DIR"
    [ $EX -eq 0 ] && echo "  clean extraction: PASS" | tee -a "$LOG" || { echo "  clean extraction: FAIL" | tee -a "$LOG"; OVERALL=1; }
else
    echo "  clean extraction: NOT EXECUTED (no tarball yet)" | tee -a "$LOG"
fi

echo ""; echo "============================================" | tee -a "$LOG"
echo "  RC5.3 COMBINED GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
echo "============================================" | tee -a "$LOG"
exit $OVERALL
