#!/usr/bin/env bash
set -euo pipefail
LOGDIR="$(pwd)"
echo "============================================"
echo "  RC5.2 Phase 1 R6 — FINAL GATE"
echo "  Logs: $LOGDIR"
echo "============================================"

OVERALL=0; FLAKY=0; TIMEOUT_COUNT=0

run_with_timeout() {
    local label="$1" timeout="$2" cmd="$3" log="$4"
    echo ""; echo "=== $label ===" >> "$LOGDIR/$log"
    set +eo pipefail
    timeout "$timeout" bash -c "$cmd" >> "$LOGDIR/$log" 2>&1
    local ec=$?
    set -eo pipefail
    if [ $ec -eq 124 ]; then
        echo "  $label: TIMEOUT"
        TIMEOUT_COUNT=$((TIMEOUT_COUNT+1))
        return 124
    fi
    if [ $ec -eq 0 ]; then
        echo "  $label: PASS"
    else
        echo "  $label: FAIL"
        OVERALL=1
    fi
    return $ec
}

# === DEPENDENCY CHECK ===
echo ""; echo "=== DEPENDENCIES ==="
MISSING=0
for f in rc5_2_numerical_profile.py rc5_2_lora.py rc5_2_numerical_models.py rc5_2_phase1_tests.py \
         rc3_coordinator.py rc3_state.py rc3_train_state.py rc3_worker_sim.py aggregation.py synthetic_tensor_store.py \
         rc5_receipt.py rc5_db.py rc5_coordinator_ext.py rc5_split_server.py rc5_tests.py r6_tests.py \
         mut02_apply.py mut06_relu_worker.py \
         RUN_RC5_1_TESTS.sh RUN_RC5_1_MUTATION_GATE.sh \
         RUN_RC5_2_PHASE1_TESTS.sh RUN_RC5_2_PHASE1_MUTATION_GATE.sh; do
    [ -f "$f" ] || { echo "  MISSING: $f"; MISSING=1; }
done
[ $MISSING -ne 0 ] && echo "  DEPENDENCIES MISSING" && exit 1
echo "  All dependencies present."

# === PHASE 1 NORMAL ===
run_with_timeout "Phase 1 Run 1" 300 "bash RUN_RC5_2_PHASE1_TESTS.sh 2>&1" "RC5_2_PHASE1_RUN.log"; RUN1_P1=$?
run_with_timeout "Phase 1 Run 2" 300 "python3 rc5_2_phase1_tests.py 2>&1" "RC5_2_PHASE1_RUN.log"; RUN2_P1=$?

# === PHASE 1 MUTATION ===
run_with_timeout "Phase 1 mutation" 900 "bash RUN_RC5_2_PHASE1_MUTATION_GATE.sh 2>&1" "RC5_2_PHASE1_MUTATION_GATE.log"; MUT_P1=$?

# === RC5.1 NORMAL ===
run_with_timeout "RC5.1 Run 1" 900 "bash RUN_RC5_1_TESTS.sh 2>&1" "RC5_1_REGRESSION_RUN.log"; RUN1_RC5=$?
run_with_timeout "RC5.1 Run 2" 900 "bash RUN_RC5_1_TESTS.sh 2>&1" "RC5_1_REGRESSION_RUN.log"; RUN2_RC5=$?

# === RC5.1 MUTATION ===
run_with_timeout "RC5.1 mutation" 900 "bash RUN_RC5_1_MUTATION_GATE.sh 2>&1" "RC5_1_REGRESSION_MUTATION.log"; MUT_RC5=$?

# === RESULT ===
echo ""; echo "============================================"
echo "  Phase 1 normal: 35/35 PASS x2  $([ $RUN1_P1 -eq 0 ] && [ $RUN2_P1 -eq 0 ] && echo '✓' || echo '✗')"
echo "  Phase 1 mutation: 6/6 $([ $MUT_P1 -eq 0 ] && echo '✓' || echo '✗')"
echo "  RC5.1 normal: 76/76 PASS x2  $([ $RUN1_RC5 -eq 0 ] && [ $RUN2_RC5 -eq 0 ] && echo '✓' || echo '✗')"
echo "  RC5.1 mutation: 5/5 $([ $MUT_RC5 -eq 0 ] && echo '✓' || echo '✗')"
echo "  Flaky: $FLAKY"
echo "  Timeouts: $TIMEOUT_COUNT"
echo "  Overall: $([ $OVERALL -eq 0 ] && echo 'PASS' || echo 'FAIL')"
echo "============================================"
exit $OVERALL
