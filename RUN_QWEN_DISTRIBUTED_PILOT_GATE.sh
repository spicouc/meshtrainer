#!/usr/bin/env bash
# RUN_QWEN_DISTRIBUTED_PILOT_GATE.sh — QWEN PILOT R2 DISTRIBUÏT (secció 15)
#
#  1  dependency check (imports)
#  2  py_compile (fitxers nous)
#  3  backend tests single-worker (P1..P8)
#  4  backend R2 hardened (S1..S4: expected_sha, base identity, request_sha,
#     recovery post-APPLIED amb optimizer)
#  5  HTTP/lease adversarial (A1..A11: enforcement REJECTED/idempotent)
#  6  two-process workers + Coordinator contributions + FedAvg Coordinator
#     + oracle + Round 2 + quality before/after (run distribuït complet)
#  7  recovery subprocess (R-Q1..R-Q5: crash, lease expiry, reassignació,
#     mid-training, checkpoint retry)
#  8  regressió RC5.4 (fitxers congelats = tarball R3.1)
#  9  manifest + artefactes
set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${QWEN3_PYTHON:-/opt/qwen3-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
MODEL="${QWEN3_MODEL:-/root/qwen3_0_6b_snapshot}"
DATA="$(pwd)/qwen3_pilot_data"
OUT="$(pwd)/qwen_distributed_output"
R31_TARBALL="${R31_TARBALL:-/root/meshtrainer_rc54_stagea_r3_1_final_77595f4.tar.gz}"
LOG_DIR="${LOG_DIR:-$(pwd)/qwen_distributed_logs}"
mkdir -p "$LOG_DIR" "$OUT"
export QWEN3_MODEL="$MODEL" QWEN3_DATA="$DATA" QWEN3_OUT="$OUT"
OVERALL=0

echo "============================================"
echo "  QWEN DISTRIBUTED PILOT — FINAL GATE"
echo "  Python: $("$PYTHON" --version 2>&1)"
echo "  Model:  $MODEL"
echo "  LOG:    $LOG_DIR"
echo "============================================"

run_sub() {
    local label="$1" cmd="$2" tmo="$3"
    echo ""; echo "=== $label ==="
    timeout "$tmo" bash -c "$cmd" > "$LOG_DIR/$label.log" 2>&1
    local ec=$?
    if [ $ec -eq 124 ]; then echo "  $label: TIMEOUT"; OVERALL=1
    elif [ $ec -eq 0 ]; then echo "  $label: PASS"
    else echo "  $label: FAIL (exit $ec)"; OVERALL=1; fi
}

# 1) dependency
echo ""; echo "=== [1/9] dependency check ==="
"$PYTHON" - <<'PY' >> "$LOG_DIR/01_dependency.log" 2>&1
import torch, transformers, peft, numpy
import qwen3_training_backend, qwen3_pilot_dataset, qwen_round_coordinator
import qwen_http_server, qwen_worker, qwen_quality
import rc5_4_leases, aggregation
print("deps OK")
PY
[ $? -eq 0 ] && echo "  dependency check: PASS" || { echo "  dependency check: FAIL"; OVERALL=1; }

# 2) py_compile
echo ""; echo "=== [2/9] py_compile ==="
"$PYTHON" -m py_compile qwen3_training_backend.py qwen3_pilot_dataset.py \
    qwen3_pilot_tests.py qwen_round_coordinator.py qwen_http_server.py \
    qwen_worker.py qwen_quality.py qwen_distributed_run.py \
    qwen_backend_r2_check.py qwen_distributed_adversarial_tests.py \
    qwen_distributed_recovery_tests.py >> "$LOG_DIR/02_pycompile.log" 2>&1
[ $? -eq 0 ] && echo "  py_compile: PASS" || { echo "  py_compile: FAIL"; OVERALL=1; }

# 3) backend tests single-worker
run_sub "03_backend_tests" "QWEN3_SEQ=128 $PYTHON qwen3_pilot_tests.py" 3600

# 4) backend R2 hardened
run_sub "04_backend_r2_hardened" "$PYTHON qwen_backend_r2_check.py" 3600

# 5) HTTP/lease adversarial (enforcement)
run_sub "05_http_lease_adversarial" "$PYTHON qwen_distributed_adversarial_tests.py" 1800

# 6) run distribuït complet (workers + Coordinator + FedAvg + oracle + R2 + quality)
run_sub "06_distributed_run" "$PYTHON qwen_distributed_run.py --num-examples 10 --seq-len 128" 10800

# 7) recovery subprocess
run_sub "07_recovery_subprocess" "$PYTHON qwen_distributed_recovery_tests.py" 5400

# 8) regressió RC5.4
echo ""; echo "=== [8/9] regressió RC5.4 (fitxers congelats) ==="
if [ -f "$R31_TARBALL" ]; then
    EX="/tmp/qwen_r31_extract_$$"; rm -rf "$EX"; mkdir -p "$EX"
    tar -xzf "$R31_TARBALL" -C "$EX" 2>/dev/null
    FROZEN="rc5_3_multiworker.py rc5_3_tests.py rc5_3_adversarial_tests.py
rc5_2_worker_runtime.py rc5_2_http_server.py rc5_2_phase2_tests.py
aggregation.py rc5_4_leases.py rc5_2_tensor_bundle.py rc5_2_numerical_adapter.py
rc5_4_integration.py rc5_4_http_server.py"
    REG=0
    for f in $FROZEN; do
        h_ref=$(sha256sum "$EX/$f" 2>/dev/null | cut -d' ' -f1)
        h_cur=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)
        if [ -z "$h_ref" ] || [ "$h_ref" != "$h_cur" ]; then
            echo "  CANVIAT: $f"; REG=1
        fi
    done
    [ $REG -eq 0 ] && echo "  fitxers congelats: PASS (hashes = R3.1)" || { echo "  fitxers congelats: FAIL"; OVERALL=1; }
    rm -rf "$EX"
else
    echo "  tarball R3.1 absent — regressió no executada"; OVERALL=1
fi

# 9) manifest + artefactes
echo ""; echo "=== [9/9] manifest ==="
FILES=$(find . -type f ! -path './.venv/*' ! -path './.git/*' ! -path '*/__pycache__/*' ! -path './qwen3_pilot_output/*' ! -path './qwen3_pilot_logs/*' ! -path './qwen_distributed_output/*' ! -path './qwen_distributed_logs/*' ! -name '*.pyc' ! -name 'MANIFEST.sha256' | wc -l)
MF=$(wc -l < MANIFEST.sha256 2>/dev/null || echo 0)
# fitxers del MANIFEST que viuen als directoris d'outputs (exclosos del find)
EXCL_MF=$(grep -cE '^  \./(qwen3_pilot_output|qwen3_pilot_logs|qwen_distributed_output|qwen_distributed_logs)/' MANIFEST.sha256 2>/dev/null || echo 0)
if [ "$FILES" -eq "$((MF - EXCL_MF))" ] && [ -f MANIFEST.sha256 ]; then
    ERR=$(sha256sum -c MANIFEST.sha256 2>&1 | grep -cv ': OK')
    echo "  manifest: $([ "$ERR" -eq 0 ] && echo PASS || echo FAIL) (coberts=$FILES manifest=$MF exclosos_manifest=$EXCL_MF err=$ERR)"
    [ "$ERR" -eq 0 ] || OVERALL=1
else
    echo "  manifest: FAIL (coberts=$FILES manifest=$MF exclosos_manifest=$EXCL_MF)"; OVERALL=1
fi
ART_OK=1
for a in adapter_0.bundle adapter_1.bundle adapter_2.bundle \
         distributed_metrics.json quality_before.json quality_after.json; do
    if [ -s "qwen_distributed_output/$a" ]; then echo "  artefacte $a: OK"
    else echo "  artefacte $a: ABSENT"; ART_OK=0; fi
done
[ $ART_OK -eq 1 ] && echo "  artefactes distribuïts: PASS" || { echo "  artefactes: FAIL"; OVERALL=1; }

echo ""; echo "============================================"
echo "  QWEN DISTRIBUTED PILOT GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)"
echo "============================================"
exit $OVERALL
