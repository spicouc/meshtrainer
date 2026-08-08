#!/usr/bin/env bash
# RUN_QWEN_PILOT_FINAL_GATE.sh — primer entrenament real MeshTrainer (Qwen3-0.6B)
#
# Passos:
#  1  dependency check (imports del backend)
#  2  py_compile (fitxers nous)
#  3  single worker tests (P1..P8)
#  4  adversarial (A1..A8)
#  5  entrenament real + prompts abans/desprès + mètriques
#  6  dos workers + FedAvg + oracle + round 2
#  7  recovery (crash APPLIED, leases, reassignació, mid-backward EXPIRED)
#  8  regressió RC5.4: fitxers congelats amb els MATEIXOS hashes que el
#     tarball certificat R3.1 (1a0da69a...)
#  9  manifest (cobertura completa)
set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${QWEN3_PYTHON:-/opt/qwen3-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
MODEL="${QWEN3_MODEL:-/root/qwen3_0_6b_snapshot}"
DATA="$(pwd)/qwen3_pilot_data"
OUT="$(pwd)/qwen3_pilot_output"
R31_TARBALL="${R31_TARBALL:-/root/meshtrainer_rc54_stagea_r3_1_final_77595f4.tar.gz}"
LOG_DIR="${LOG_DIR:-$(pwd)/qwen3_pilot_logs}"
mkdir -p "$LOG_DIR" "$OUT"
export QWEN3_MODEL="$MODEL" QWEN3_DATA="$DATA" QWEN3_OUT="$OUT"
OVERALL=0

echo "============================================"
echo "  QWEN PILOT — FINAL GATE"
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

# 1) dependency check
echo ""; echo "=== [1/9] dependency check ==="
"$PYTHON" - <<'PY' >> "$LOG_DIR/01_dependency.log" 2>&1
import sys
need = ["torch", "transformers", "peft", "numpy"]
for m in need:
    __import__(m)
print("deps OK:", " ".join(need))
import qwen3_training_backend, qwen3_pilot_dataset
print("mòduls pilot importables")
PY
DEP=$?
[ $DEP -eq 0 ] && echo "  dependency check: PASS" || { echo "  dependency check: FAIL"; OVERALL=1; }

# 2) py_compile
echo ""; echo "=== [2/9] py_compile ==="
"$PYTHON" -m py_compile qwen3_training_backend.py qwen3_pilot_dataset.py \
    qwen3_pilot_tests.py qwen3_pilot_adversarial_tests.py \
    qwen3_pilot_train.py qwen3_pilot_round.py qwen3_pilot_recovery.py \
    >> "$LOG_DIR/02_pycompile.log" 2>&1
PC=$?
[ $PC -eq 0 ] && echo "  py_compile: PASS" || { echo "  py_compile: FAIL"; OVERALL=1; }

# 3) single worker tests
run_sub "03_single_worker_tests" "QWEN3_SEQ=128 $PYTHON qwen3_pilot_tests.py" 5400

# 4) adversarial
run_sub "04_adversarial" "$PYTHON qwen3_pilot_adversarial_tests.py" 5400

# 5) entrenament real (subset pilot a CPU) + prompts abans/desprès
run_sub "05_train_real" "$PYTHON qwen3_pilot_train.py --exemples 20 --seq-len 256" 10800

# 6) dos workers + FedAvg + oracle + round 2
run_sub "06_round" "$PYTHON qwen3_pilot_round.py" 10800

# 7) recovery
run_sub "07_recovery" "$PYTHON qwen3_pilot_recovery.py" 5400

# 8) regressió RC5.4: fitxers congelats = tarball certificat R3.1
echo ""; echo "=== [8/9] regressió RC5.4 (fitxers congelats) ==="
if [ -f "$R31_TARBALL" ]; then
    EX="/tmp/qwen_r31_extract_$$"; rm -rf "$EX"; mkdir -p "$EX"
    tar -xzf "$R31_TARBALL" -C "$EX" 2>/dev/null
    FROZEN="rc5_3_multiworker.py rc5_3_tests.py rc5_3_adversarial_tests.py
rc5_2_worker_runtime.py rc5_2_http_server.py rc5_2_phase2_tests.py
aggregation.py rc5_4_leases.py rc5_2_tensor_bundle.py rc5_2_numerical_adapter.py"
    REG=0
    for f in $FROZEN; do
        h_ref=$(sha256sum "$EX/$f" 2>/dev/null | cut -d' ' -f1)
        h_cur=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)
        if [ -z "$h_ref" ] || [ "$h_ref" != "$h_cur" ]; then
            echo "  CANVIAT: $f ($h_cur != $h_ref)"; REG=1
        fi
    done
    [ $REG -eq 0 ] && echo "  fitxers congelats: PASS (hashes = R3.1)" || { echo "  fitxers congelats: FAIL"; OVERALL=1; }
    rm -rf "$EX"
else
    echo "  tarball R3.1 absent ($R31_TARBALL) — regressió no executada"; OVERALL=1
fi

# 9) manifest (cobertura completa dels fitxers del repo; els artefactes
# generats a qwen3_pilot_output/ es verifiquen per separat)
echo ""; echo "=== [9/9] manifest ==="
FILES=$(find . -type f ! -path './.venv/*' ! -path './.git/*' ! -path '*/__pycache__/*' ! -path './qwen3_pilot_output/*' ! -path './qwen3_pilot_logs/*' ! -name '*.pyc' ! -name 'MANIFEST.sha256' | wc -l)
MF=$(wc -l < MANIFEST.sha256 2>/dev/null || echo 0)
if [ "$FILES" -eq "$MF" ] && [ -f MANIFEST.sha256 ]; then
    ERR=$(sha256sum -c MANIFEST.sha256 2>&1 | grep -cv ': OK')
    echo "  manifest: $([ "$ERR" -eq 0 ] && echo PASS || echo FAIL) (coberts=$FILES manifest=$MF err=$ERR)"
    [ "$ERR" -eq 0 ] || OVERALL=1
else
    echo "  manifest: FAIL (coberts=$FILES manifest=$MF)"; OVERALL=1
fi
# artefactes generats (entrenament real)
ART_OK=1
for a in adapter_0.bundle adapter_1.bundle metrics.json responses_before.json responses_after.json round_metrics.json; do
    if [ -s "qwen3_pilot_output/$a" ]; then echo "  artefacte $a: OK"
    else echo "  artefacte $a: ABSENT"; ART_OK=0; fi
done
[ $ART_OK -eq 1 ] && echo "  artefactes entrenament: PASS" || { echo "  artefactes entrenament: FAIL"; OVERALL=1; }

# resum
echo ""; echo "============================================"
echo "  QWEN PILOT FINAL GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)"
echo "============================================"
exit $OVERALL
