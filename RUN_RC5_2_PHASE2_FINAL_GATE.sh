#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 2 R12 — FINAL GATE"
echo "============================================"
LOG=RC5_2_R12_FINAL_GATE.log
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

# 1) Phase 2 full suite (2 runs) — real runner, no hardcoded counts
run_sub "Phase 2 Run 1" "bash RUN_RC5_2_PHASE2_TESTS.sh" 700
# 2) Phase 2 adversarial gate
run_sub "Phase 2 adversarial" "bash RUN_RC5_2_PHASE2_ADVERSARIAL_GATE.sh" 400
# 3) Phase 2 mutation gate (12/12, any traceback = RUNTIME-INVALID)
run_sub "Phase 2 mutation" "bash RUN_RC5_2_PHASE2_MUTATION_GATE.sh" 4000
# 4) Phase 1 regressions (2 runs)
run_sub "Phase 1 Run 1" "bash RUN_RC5_2_PHASE1_TESTS.sh" 700
# 5) Phase 1 mutation gate
run_sub "Phase 1 mutation" "bash RUN_RC5_2_PHASE1_MUTATION_GATE.sh" 1800
# 6) RC5.1 regressions (2 runs) — via its real runner (NOT python3 rc5_tests.py as full gate)
run_sub "RC5.1 Run 1" "bash RUN_RC5_1_TESTS.sh" 700
# 7) RC5.1 mutation gate
run_sub "RC5.1 mutation" "bash RUN_RC5_1_MUTATION_GATE.sh" 1800

# 8) Controlled-failure RC5.2: copy of the suite with EXACTLY ONE assertion broken
echo ""; echo "=== Controlled failure (RC5.2) ===" >> "$LOG"
CF_DIR="${TMPDIR:-/tmp}/rc52_cf_r12_$$"
mkdir -p "$CF_DIR"; cp rc5_2_phase2_tests.py "$CF_DIR"/ 2>/dev/null || true
if [ -s "$CF_DIR/rc5_2_phase2_tests.py" ]; then
    sed -i 's/check("A1: protocol_version accepted"/check("A1: protocol_version accepted", False)/' "$CF_DIR/rc5_2_phase2_tests.py"
    set +e; (cd "$CF_DIR" && timeout 300 python3 rc5_2_phase2_tests.py > cf.log 2>&1); CF=$?; set -e
    rm -rf "$CF_DIR"
    if [ $CF -ne 0 ]; then echo "  controlled-failure: PASS (suite FAIL as expected)" | tee -a "$LOG"
    else echo "  controlled-failure: FAIL (broken suite passed!)" | tee -a "$LOG"; OVERALL=1; fi
else
    echo "  controlled-failure: NOT EXECUTED (copy failed)" | tee -a "$LOG"; OVERALL=1
fi

echo ""; echo "============================================" | tee -a "$LOG"
echo "  RC5.2 Phase 2 R12 — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
echo "============================================" | tee -a "$LOG"
exit $OVERALL
