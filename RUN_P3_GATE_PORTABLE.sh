#!/usr/bin/env bash
# RUN_P3_GATE_PORTABLE.sh — Gate Phase 3 PORTABLE (R1-09).
# Funciona des del directori actual del repo, sense:
#   cd /root/meshtrainer · lxc-attach · /opt/qwen3-venv
# Requereix: PYTHON_BIN (env) o .venv/ al directori actual.
# Nota: REAL-QWEN-UI i la suite UI (Playwright) necessiten el LAB (veure
# RUN_P3_GATE.sh) — aquest gate cobreix P3 + APP + E0 + core + dummy hidden.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  PASS $1"; }
ko()  { FAIL=$((FAIL+1)); echo "  FAIL $1 — $2"; }
step(){ echo "── $1"; }

# python: PYTHON_BIN env > .venv local > python3
if [ -n "${PYTHON_BIN:-}" ]; then
    PY="$PYTHON_BIN"
elif [ -x "$HERE/.venv/bin/python" ]; then
    PY="$HERE/.venv/bin/python"
else
    PY="$(command -v python3)"
fi
echo "  python: $PY"
[ -n "$PY" ] || { echo "  FAIL: no python"; exit 1; }

step "1. pycompile app/"
if "$PY" -m compileall -q app; then ok "pycompile"; else ko "pycompile" "rc=$?"; fi

step "2. P3-01..30 (portable)"
RES=$(P3_PACKAGE="${P3_PACKAGE:-}" APP_ENV=test "$PY" app/tests/p3_gate_tests.py 2>&1)
echo "$RES" | tail -4
echo "$RES" | grep -q "0 FAIL" && ok "P3-01..30" || ko "P3-01..30" "vegeu sortida"

step "3. APP-01..15 (dummy)"
RES=$(APP_ENV=test "$PY" app/tests/app_acceptance_tests.py 2>&1 | tail -1)
echo "    $RES"
echo "$RES" | grep -q "17/17" && ok "APP-01..15" || ko "APP-01..15" "$RES"

step "4. E0-01..04"
RES=$(APP_ENV=test "$PY" app/tests/e0_gate_tests.py 2>&1 | tail -1)
echo "    $RES"
echo "$RES" | grep -q "31/31" && ok "E0-01..04" || ko "E0-01..04" "$RES"

step "5. core unchanged (vs a52580c)"
if git diff --quiet a52580c -- model_round_coordinator.py training_http_server.py model_worker.py training_backend.py adapter_codec.py rc5_4_leases.py backends/ generic_distributed_run.py generic_recovery_tests.py; then
    ok "core unchanged"
else
    ko "core unchanged" "diff != 0"
fi

step "6. Dummy hidden production"
if grep -q 'HIDDEN_BACKENDS = {"dummy"} if APP_ENV == "production"' app/config.py; then
    ok "dummy hidden"
else
    ko "dummy hidden" "no found"
fi

echo ""
echo "============================================"
echo "  GATE PHASE 3 PORTABLE: ${PASS}/${PASS} PASS, ${FAIL} FAIL"
echo "============================================"
[ "$FAIL" = "0" ]
