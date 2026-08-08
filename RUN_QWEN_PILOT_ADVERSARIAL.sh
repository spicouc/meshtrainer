#!/usr/bin/env bash
# RUN_QWEN_PILOT_ADVERSARIAL.sh — proteccions mínimes (A1..A8)
set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${QWEN3_PYTHON:-/opt/qwen3-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
echo "============================================"
echo "  QWEN PILOT — ADVERSARIAL (A1..A8)"
echo "  Python: $("$PYTHON" --version 2>&1)"
echo "============================================"
QWEN3_MODEL="${QWEN3_MODEL:-/root/qwen3_0_6b_snapshot}" \
QWEN3_DATA="${QWEN3_DATA:-$(pwd)/qwen3_pilot_data}" \
timeout "${QWEN3_TIMEOUT:-5400}" "$PYTHON" qwen3_pilot_adversarial_tests.py
exit $?
