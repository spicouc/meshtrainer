#!/usr/bin/env bash
# RUN_QWEN_PILOT_TESTS.sh — single worker real (P1..P8)
# Executa els checks del backend Qwen3-0.6B + LoRA + dataset pilot.
set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${QWEN3_PYTHON:-/opt/qwen3-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
echo "============================================"
echo "  QWEN PILOT — SINGLE WORKER TESTS"
echo "  Python: $("$PYTHON" --version 2>&1)"
echo "  Model:  ${QWEN3_MODEL:-/root/qwen3_0_6b_snapshot}"
echo "============================================"
QWEN3_MODEL="${QWEN3_MODEL:-/root/qwen3_0_6b_snapshot}" \
QWEN3_DATA="${QWEN3_DATA:-$(pwd)/qwen3_pilot_data}" \
QWEN3_SEQ="${QWEN3_SEQ:-128}" \
timeout "${QWEN3_TIMEOUT:-5400}" "$PYTHON" qwen3_pilot_tests.py
exit $?
