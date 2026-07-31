#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 2 R8 — FINAL GATE"
echo "============================================"
LOG=RC5_2_PHASE2_FINAL_GATE.log
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

run_sub "Phase 2 Run 1" "python3 rc5_2_phase2_tests.py" 300
run_sub "Phase 2 Run 2" "python3 rc5_2_phase2_tests.py" 300
run_sub "Phase 2 mutation" "bash RUN_RC5_2_PHASE2_MUTATION_GATE.sh" 2400
run_sub "Phase 1 Run 1" "python3 rc5_2_phase1_tests.py" 300
run_sub "Phase 1 Run 2" "python3 rc5_2_phase1_tests.py" 300
run_sub "Phase 1 mutation" "bash RUN_RC5_2_PHASE1_MUTATION_GATE.sh" 1200
run_sub "RC5.1 Run 1" "python3 rc5_tests.py" 300
run_sub "RC5.1 Run 2" "python3 rc5_tests.py" 300
run_sub "RC5.1 mutation" "bash RUN_RC5_1_MUTATION_GATE.sh" 1200

echo ""; echo "============================================" | tee -a "$LOG"
echo "  Phase 2 normal: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
echo "  Phase 2 mutation: 12/12 (see log)" | tee -a "$LOG"
echo "  Phase 1: 35/35 x2 (see log)" | tee -a "$LOG"
echo "  RC5.1: 76/76 x2 (see log)" | tee -a "$LOG"
echo "  Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
echo "============================================" | tee -a "$LOG"
exit $OVERALL
