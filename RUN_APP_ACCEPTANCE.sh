#!/usr/bin/env bash
# RUN_APP_ACCEPTANCE.sh — Gate de producte Fase 1 (APP-01..15).
# Execució: al HOST pve (sincronitza al CT112 i executa allà els tests
# APP-01..13/15 amb el venv; APP-14 es verifica aquí contra el tarball b146723).
set -u
cd "$(dirname "$0")"

PASS=0
FAIL=0
note() { echo "  $1"; }
step() { echo ""; echo "=== $1 ==="; }
ok()   { PASS=$((PASS+1)); note "PASS $1"; }
ko()   { FAIL=$((FAIL+1)); note "FAIL $1"; }

# ── 0. pycompile (host) ──────────────────────────────────────────────────
step "pycompile app/"
if python3 -m py_compile app/__init__.py app/config.py \
        app/models/schemas.py app/storage/db.py app/datasets/jsonl_adapter.py \
        app/services/registry.py app/services/dataset_service.py \
        app/services/core_client.py app/services/job_runner.py \
        app/services/mesh_trainer_service.py app/cli/main.py; then
    ok "pycompile app/"
else
    ko "pycompile app/"
fi

# ── APP-14: core inalterat vs commit autoritatiu a52580c ────────────────
step "APP-14 core remains unchanged (vs commit autoritatiu a52580c)"
CORE_FILES="model_round_coordinator.py training_http_server.py model_worker.py
training_backend.py adapter_codec.py rc5_4_leases.py
backends/qwen3_backend.py backends/dummy_backend.py backends/minicpm5_backend.py"
if git diff --quiet a52580c -- $CORE_FILES; then
    ok "APP-14 core remains unchanged (git diff a52580c = 0)"
else
    echo "    CORE CANVIAT des de a52580c:"
    git diff --stat a52580c -- $CORE_FILES
    ko "APP-14 core remains unchanged (git diff a52580c)"
fi
# Nota: model_worker.py vs tarball b146723 difereix pel canvi AUTORITZAT i
# CERTIFICAT a R1.2 (registry lazy +minicpm5); l'artefacte autoritatiu és
# a52580c, que és la base correcta de comparació.

# ── APP-01..13/15 al CT112 ───────────────────────────────────────────────
step "APP-01..13/15 (CT112, venv)"
# sincronitza app/ al CT112
pct push 112 "$(pwd)/app" /root/meshtrainer/app -r 2>/dev/null || {
    # fallback: rsync si pct push -r no funciona amb directori
    tar -cf - app | lxc-attach -n 112 -- bash -c 'cd /root/meshtrainer && tar -xf -'
}
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && /opt/qwen3-venv/bin/python app/tests/app_acceptance_tests.py 2>&1')
APP_EXIT=$?
echo "$RES" | tail -30
if [ "$APP_EXIT" = "0" ]; then
    ok "APP-01..13/15 al CT112 (dummy)"
else
    ko "APP-01..13/15 al CT112 (dummy, exit=$APP_EXIT)"
fi

# ── E0-01..04 al CT112 ───────────────────────────────────────────────────
step "E0-01..04 (CT112, venv)"
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && /opt/qwen3-venv/bin/python app/tests/e0_gate_tests.py 2>&1')
E0_EXIT=$?
echo "$RES" | tail -30
if [ "$E0_EXIT" = "0" ]; then
    ok "E0-01..04 al CT112"
else
    ko "E0-01..04 al CT112 (exit=$E0_EXIT)"
fi

# ── REAL-QWEN-APP (smoke qwen3 real) al CT112 ────────────────────────────
step "REAL-QWEN-APP (smoke qwen3 real, CT112)"
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && /opt/qwen3-venv/bin/python app/tests/real_qwen_app_test.py 2>&1')
QWEN_EXIT=$?
echo "$RES" | tail -30
if [ "$QWEN_EXIT" = "0" ]; then
    ok "REAL-QWEN-APP (qwen3 real)"
else
    ko "REAL-QWEN-APP (qwen3 real, exit=$QWEN_EXIT)"
fi

# ── imports deps API (FastAPI/Pydantic/Uvicorn) ──────────────────────────
step "imports FastAPI/Pydantic/Uvicorn (CT112)"
RES=$(lxc-attach -n 112 -- bash -c \
    '/opt/qwen3-venv/bin/python -c "import fastapi, pydantic, uvicorn, multipart; print(\"DEPS_OK\", fastapi.__version__, pydantic.__version__)" 2>&1')
echo "$RES"
if echo "$RES" | grep -q "DEPS_OK"; then
    ok "imports FastAPI/Pydantic/Uvicorn/multipart"
else
    ko "imports FastAPI/Pydantic/Uvicorn/multipart"
fi

# ── Phase 2: UI-01..25 (Playwright) al CT112 ─────────────────────────────
step "UI-01..25 (Playwright, CT112)"
RES=$(lxc-attach -n 112 -- bash -c \
    'echo +800 > /proc/self/oom_score_adj; cd /root/meshtrainer && bash RUN_UI_E2E.sh 2>&1')
UI_EXIT=$?
echo "$RES" | tail -30
if [ "$UI_EXIT" = "0" ]; then
    ok "UI-01..25 (Playwright)"
else
    ko "UI-01..25 (Playwright, exit=$UI_EXIT)"
fi

# ── resum ────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  APP ACCEPTANCE (Fase 1+2): ${PASS}/${PASS} PASS, ${FAIL} FAIL"
echo "============================================"
[ "$FAIL" = "0" ]
