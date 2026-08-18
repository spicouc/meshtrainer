#!/usr/bin/env bash
# RUN_UI_E2E.sh — executa la suite Playwright UI-01..25 (CT112).
set -u
cd /root/meshtrainer || exit 1

# Fix CT112: SSL_CERT_FILE apunta a un fitxer inexistent (bug d'entorn
# conegut que afecta httpx/requests). Es desactiva per a aquesta execució.
unset SSL_CERT_FILE SSL_CERT_DIR 2>/dev/null || true

pkill -f "uvicorn app[.]api.main" 2>/dev/null || true
sleep 1

APP_ENV=test /opt/qwen3-venv/bin/python -m uvicorn app.api.main:app \
    --host 127.0.0.1 --port 8014 > /tmp/ui_server.log 2>&1 &
SRV=$!
sleep 4

# espera activa del port
for i in $(seq 1 20); do
    if curl -s http://127.0.0.1:8014/api/health >/dev/null 2>&1 || \
       /opt/qwen3-venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8014/api/health', timeout=2)" 2>/dev/null; then
        break
    fi
    sleep 1
done

echo "+800" > /proc/self/oom_score_adj 2>/dev/null
/opt/qwen3-venv/bin/python app/tests/ui_e2e_tests.py 2>&1
RC=$?

kill $SRV 2>/dev/null || true
pkill -f "uvicorn app[.]api.main" 2>/dev/null || true
exit $RC
