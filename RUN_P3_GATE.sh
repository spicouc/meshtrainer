#!/usr/bin/env bash
# RUN_P3_GATE.sh — Gate complet Phase 3 (CT112).
# P3-01..30 + regressions (APP, E0, UI) + core unchanged + dummy hidden.
set -u
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  PASS $1"; }
ko()  { FAIL=$((FAIL+1)); echo "  FAIL $1 — $2"; }
step(){ echo "── $1"; }

cd /root/meshtrainer || exit 1

step "1. pycompile app/"
if python3 -m compileall -q app; then ok "pycompile"; else ko "pycompile" "rc=$?"; fi

step "2. P3-01..30 (Phase 3 gate, CT112)"
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && /opt/qwen3-venv/bin/python app/tests/p3_gate_tests.py 2>&1')
P3_EXIT=$?
echo "$RES" | tail -4
if [ "$P3_EXIT" = "0" ]; then ok "P3-01..30"; else ko "P3-01..30" "exit=$P3_EXIT"; fi

step "3. APP-01..15 (regressió, CT112)"
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && /opt/qwen3-venv/bin/python app/tests/app_acceptance_tests.py 2>&1 | tail -2')
echo "$RES"
echo "$RES" | grep -q "17/17" && ok "APP-01..15" || ko "APP-01..15" "$RES"

step "4. E0-01..04 (regressió, CT112)"
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && /opt/qwen3-venv/bin/python app/tests/e0_gate_tests.py 2>&1 | tail -2')
echo "$RES"
echo "$RES" | grep -q "31/31" && ok "E0-01..04" || ko "E0-01..04" "$RES"

step "5. UI-01..25 (Playwright, regressió, CT112)"
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && bash RUN_UI_E2E.sh 2>&1 | tail -4')
echo "$RES"
echo "$RES" | grep -q "27/27" && ok "UI-01..25" || ko "UI-01..25" "$RES"

step "6. core unchanged (vs a52580c)"
if git diff --quiet a52580c -- model_round_coordinator.py training_http_server.py model_worker.py training_backend.py adapter_codec.py rc5_4_leases.py backends/ generic_distributed_run.py generic_recovery_tests.py; then
    ok "core unchanged"
else
    ko "core unchanged" "diff != 0"
fi

step "7. REAL-QWEN-UI (qwen3 real via Web UI, CT112)"
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && /opt/qwen3-venv/bin/python app/tests/real_qwen_ui_test.py 2>&1 | tail -6')
echo "$RES"
echo "$RES" | grep -q "REAL-QWEN-UI: PASS" && ok "REAL-QWEN-UI" || ko "REAL-QWEN-UI" "$RES"

echo ""
echo "============================================"
echo "  GATE PHASE 3: ${PASS}/${PASS} PASS, ${FAIL} FAIL"
echo "============================================"
[ "$FAIL" = "0" ]
