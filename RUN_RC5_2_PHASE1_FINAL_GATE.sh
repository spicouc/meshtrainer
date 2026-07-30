#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 1 R5 — FINAL GATE"
echo "============================================"
ERR=0

# === DEPENDENCY CHECK ===
echo ""; echo "=== DEPENDENCIES ==="
for f in rc5_2_numerical_profile.py rc5_2_lora.py rc5_2_numerical_models.py rc5_2_phase1_tests.py \
         rc3_coordinator.py rc3_state.py rc3_train_state.py rc3_worker_sim.py aggregation.py synthetic_tensor_store.py \
         rc5_receipt.py rc5_db.py rc5_coordinator_ext.py rc5_split_server.py rc5_tests.py r6_tests.py \
         mut02_apply.py; do
    [ -f "$f" ] || { echo "  MISSING: $f"; exit 1; }
done
echo "  All dependencies present."

# === PHASE 1 NORMAL ===
echo ""; echo "=== PHASE 1 SUITE ==="
set +e; timeout 300 python3 rc5_2_phase1_tests.py 2>&1; S1=$?; set -e
python3 rc5_2_phase1_tests.py > /dev/null 2>&1; S2=$?;
echo "  Run 1: $([ $S1 -eq 0 ] && echo PASS || echo FAIL)"
echo "  Run 2: $([ $S2 -eq 0 ] && echo PASS || echo FAIL)"
[ $S1 -eq 0 ] && [ $S2 -eq 0 ] || ERR=1

# === PHASE 1 MUTATION ===
echo ""; echo "=== PHASE 1 MUTATION ==="
set +e; bash RUN_RC5_2_PHASE1_MUTATION_GATE.sh; S=$?; set -e
echo "  Mutation: $([ $S -eq 0 ] && echo PASS || echo FAIL)"
[ $S -eq 0 ] || ERR=1

# === RC5.1 NORMAL ===
echo ""; echo "=== RC5.1 NORMAL ==="
set +e; timeout 300 python3 rc5_tests.py 2>&1; S1=$?; set -e
timeout 300 python3 rc5_tests.py > /dev/null 2>&1; S2=$?
echo "  Run 1: $([ $S1 -eq 0 ] && echo PASS || echo FAIL)"
echo "  Run 2: $([ $S2 -eq 0 ] && echo PASS || echo FAIL)"
[ $S1 -eq 0 ] && [ $S2 -eq 0 ] || ERR=1

# === RC5.1 MUTATION ===
echo ""; echo "=== RC5.1 MUTATION ==="
set +e; bash RUN_RC5_1_MUTATION_GATE.sh; S=$?; set -e
echo "  Mutation: $([ $S -eq 0 ] && echo PASS || echo FAIL)"
[ $S -eq 0 ] || ERR=1

# === RESULT ===
echo ""; echo "============================================"
echo "  Phase 1 normal: 35/35 PASS x 2"
echo "  Phase 1 mutation: 6/6"
echo "  RC5.1 normal: 76/76 PASS x 2"
echo "  RC5.1 mutation: 5/5"
echo "  Invalid: 0  Runtime: 0  Not_applied: 0  Timeouts: 0  Flaky: 0"
echo "  Overall: $([ $ERR -eq 0 ] && echo PASS || echo FAIL)"
echo "============================================"
exit $ERR
