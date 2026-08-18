#!/usr/bin/env bash
# RC5.2 Phase 2 R12 — FINAL GATE (R6.1: CF dedicat, LOG_DIR, resum sempre)
set -uo pipefail
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
export LOG_DIR
LOG="$LOG_DIR/RC5_2_R12_FINAL_GATE.log"
: > "$LOG"
OVERALL=0

echo "============================================"
echo "  RC5.2 Phase 2 R12 — FINAL GATE"
echo "  LOG_DIR: $LOG_DIR"
echo "============================================"

run_sub() {
    local label="$1" cmd="$2" timeout="$3"
    echo ""; echo "=== $label ===" >> "$LOG"
    timeout "$timeout" bash -c "$cmd" >> "$LOG" 2>&1; local ec=$?
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

# 8) Controlled-failure RC5.2 — runner dedicat (substitució exacta J4)
echo ""; echo "=== Controlled failure (RC5.2) ===" | tee -a "$LOG"
timeout 700 bash RUN_RC5_2_CONTROLLED_FAILURE.sh >> "$LOG" 2>&1; CF=$?
if [ $CF -eq 0 ]; then echo "  controlled-failure RC5.2: PASS" | tee -a "$LOG"
else echo "  controlled-failure RC5.2: FAIL (exit $CF)" | tee -a "$LOG"; OVERALL=1; fi

echo ""; echo "============================================" | tee -a "$LOG"
echo "  RC5.2 Phase 2 R12 — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
echo "============================================" | tee -a "$LOG"
exit $OVERALL
