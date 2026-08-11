#!/usr/bin/env bash
# RUN_GENERIC_DISTRIBUTED_GATE.sh — GATE FINAL R2.2 (closeout)
#
# Cada subgate escriu el SEU log real (sense copiar el mateix log sota
# noms diferents). Els logs van a $LOG_DIR i el resum a
# evidence/GENERIC_DISTRIBUTED_FINAL_GATE.log
set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${QWEN3_PYTHON:-/opt/qwen3-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
ISO_PY="${ISO_PYTHON:-/tmp/iso_venv/bin/python}"   # venv sense torch (host)
MODEL="${QWEN3_MODEL:-/root/qwen3_0_6b_snapshot}"
DATA="$(pwd)/qwen3_pilot_data"
OUT="$(pwd)/qwen_distributed_output"
LOG_DIR="${LOG_DIR:-$(pwd)/qwen_distributed_logs}"
EVIDENCE="$(pwd)/evidence"
mkdir -p "$LOG_DIR" "$OUT" "$EVIDENCE"
export QWEN3_MODEL="$MODEL" QWEN3_DATA="$DATA"
OVERALL=0
GATE_LOG="$EVIDENCE/GENERIC_DISTRIBUTED_FINAL_GATE.log"

echo "============================================" | tee "$GATE_LOG"
echo "  GENERIC DISTRIBUTED FINAL GATE (R2.2)" | tee -a "$GATE_LOG"
echo "  Python venv Qwen: $($PYTHON --version 2>&1)" | tee -a "$GATE_LOG"
echo "  Python ISO (sense torch): $([ -x "$ISO_PY" ] && $ISO_PY --version 2>&1 || echo 'NO venv ISO')" | tee -a "$GATE_LOG"
echo "  Model: $MODEL" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"

run_sub() {
    local label="$1" cmd="$2" tmo="$3"
    echo "" | tee -a "$GATE_LOG"
    echo "=== $label ===" | tee -a "$GATE_LOG"
    timeout "$tmo" bash -c "$cmd" > "$LOG_DIR/$label.log" 2>&1
    local ec=$?
    if [ $ec -eq 124 ]; then echo "  $label: TIMEOUT" | tee -a "$GATE_LOG"; OVERALL=1
    elif [ $ec -eq 0 ]; then echo "  $label: PASS" | tee -a "$GATE_LOG"
    else echo "  $label: FAIL (exit $ec)" | tee -a "$GATE_LOG"; OVERALL=1; fi
}

# 1) dependency (venv Qwen)
run_sub "dependency" "$PYTHON -c 'import torch, transformers, peft, numpy; print(\"deps OK\")'" 120

# 2) py_compile de tot el core + backends
echo "" | tee -a "$GATE_LOG"; echo "=== pycompile ===" | tee -a "$GATE_LOG"
"$PYTHON" -m py_compile training_backend.py adapter_codec.py \
    model_round_coordinator.py training_http_server.py model_worker.py \
    backend_isolation.py generic_distributed_run.py generic_recovery_tests.py \
    backends/qwen3_backend.py backends/dummy_backend.py \
    >> "$LOG_DIR/pycompile.log" 2>&1
[ $? -eq 0 ] && echo "  pycompile: PASS" | tee -a "$GATE_LOG" \
             || { echo "  pycompile: FAIL" | tee -a "$GATE_LOG"; OVERALL=1; }

# 3) core sense imports Qwen (només imports reals, no strings del registry)
echo "" | tee -a "$GATE_LOG"; echo "=== core_sense_imports_qwen ===" | tee -a "$GATE_LOG"
if grep -nE "^(from|import) +qwen|from qwen|import qwen" \
        training_backend.py adapter_codec.py model_round_coordinator.py \
        training_http_server.py model_worker.py generic_distributed_run.py \
        generic_recovery_tests.py backend_isolation.py > "$LOG_DIR/core_sense_imports_qwen.log" 2>&1; then
    echo "  core_sense_imports_qwen: FAIL (imports detectats)" | tee -a "$GATE_LOG"; OVERALL=1
else
    echo "  core_sense_imports_qwen: PASS" | tee -a "$GATE_LOG"
fi

# 4) backend isolation (venv sense torch — al host, no CT112)
echo "" | tee -a "$GATE_LOG"; echo "=== backend_isolation ===" | tee -a "$GATE_LOG"
if [ -x "$ISO_PY" ]; then
    timeout 600 "$ISO_PY" backend_isolation.py > "$LOG_DIR/backend_isolation.log" 2>&1
    ec=$?
    if [ $ec -eq 0 ]; then echo "  backend_isolation: PASS" | tee -a "$GATE_LOG"
    else echo "  backend_isolation: FAIL (exit $ec)" | tee -a "$GATE_LOG"; OVERALL=1; fi
else
    echo "  backend_isolation: NO EXECUTADA (falta venv ISO)" | tee -a "$GATE_LOG"; OVERALL=1
fi

# 5) dummy e2e
run_sub "dummy_e2e" "$PYTHON generic_distributed_run.py --backend dummy --num-examples 4 --seq-len 16" 900

# 6) dummy recovery
run_sub "dummy_recovery" "$PYTHON generic_recovery_tests.py --backend dummy --num-examples 4 --seq-len 16" 900

# 7) qwen e2e (2 subprocess workers, FedAvg, Round 2, oracle)
run_sub "qwen_e2e" "$PYTHON generic_distributed_run.py --backend qwen3 --num-examples 10 --seq-len 128" 3600

# 8) qwen recovery (server restart + SIGKILL)
run_sub "qwen_recovery" "$PYTHON generic_recovery_tests.py --backend qwen3 --num-examples 2 --seq-len 64" 1800

# 9) quality (sobre els artefactes reals del qwen e2e)
echo "" | tee -a "$GATE_LOG"; echo "=== quality ===" | tee -a "$GATE_LOG"
QOK=1
# qwen_quality.py (script Qwen) només llegeix schema 'qwen_bundle_v1';
# els bundles del FedAvg genèric són 'adapter_bundle_v1' -> convertim.
convert_bundle() {
    "$PYTHON" - "$1" "$2" <<'PY'
import sys
from adapter_codec import unpack_tensors
from qwen3_training_backend import Qwen3TrainingBackend
import torch
src, dst = sys.argv[1], sys.argv[2]
data = open(src, "rb").read()
tensors = unpack_tensors(data)  # accepta qualsevol schema
out = Qwen3TrainingBackend._serialize_delta(
    {k: torch.as_tensor(v) for k, v in tensors.items()})
open(dst, "wb").write(out)
print(f"convertit {src} -> {dst} ({len(out)} B)")
PY
}
if [ -s "$OUT/adapter_0.bundle" ] && [ -s "$OUT/adapter_2.bundle" ]; then
    Q0=$(timeout 900 "$PYTHON" qwen_quality.py --model "$MODEL" \
        --adapter "$OUT/adapter_0.bundle" \
        --adapter-sha "$(sha256sum "$OUT/adapter_0.bundle" | cut -d' ' -f1)" \
        --data-dir "$DATA" --out "$OUT/quality_before.json" 2>&1 | tail -1)
    # adapter_2.bundle és del FedAvg genèric (adapter_bundle_v1): converteix
    convert_bundle "$OUT/adapter_2.bundle" "$OUT/adapter_2_qwen.bundle" \
        >> "$LOG_DIR/quality.log" 2>&1
    Q2=$(timeout 900 "$PYTHON" qwen_quality.py --model "$MODEL" \
        --adapter "$OUT/adapter_2_qwen.bundle" \
        --adapter-sha "$(sha256sum "$OUT/adapter_2_qwen.bundle" | cut -d' ' -f1)" \
        --data-dir "$DATA" --out "$OUT/quality_after.json" 2>&1 | tail -1)
    echo "  quality_before: $Q0" | tee -a "$GATE_LOG"
    echo "  quality_after:  $Q2" | tee -a "$GATE_LOG"
    echo "  (CLASSIFICACIÓ: PIPELINE TRAINING PASS · QUALITY PILOT)" | tee -a "$GATE_LOG"
    [ -s "$OUT/quality_before.json" ] && [ -s "$OUT/quality_after.json" ] \
        || { echo "  quality: FAIL (json absent)" | tee -a "$GATE_LOG"; QOK=0; }
else
    echo "  quality: NO EXECUTADA (falten adapter_0/2.bundle)" | tee -a "$GATE_LOG"; QOK=0
fi
[ $QOK -eq 1 ] && echo "  quality: PASS" | tee -a "$GATE_LOG" || OVERALL=1

# 10) regressió RC5.4 (12 fitxers congelats byte-for-byte)
echo "" | tee -a "$GATE_LOG"; echo "=== regression ===" | tee -a "$GATE_LOG"
R31_TARBALL="${R31_TARBALL:-/root/meshtrainer_rc54_stagea_r3_1_final_77595f4.tar.gz}"
if [ -f "$R31_TARBALL" ]; then
    EX=$(mktemp -d)
    tar -xzf "$R31_TARBALL" -C "$EX" 2>/dev/null
    FROZEN="rc5_3_multiworker.py rc5_3_tests.py rc5_3_adversarial_tests.py
rc5_2_worker_runtime.py rc5_2_http_server.py rc5_2_phase2_tests.py
aggregation.py rc5_4_leases.py rc5_2_tensor_bundle.py rc5_2_numerical_adapter.py
rc5_4_integration.py rc5_4_http_server.py"
    REG=0; N=0
    for f in $FROZEN; do
        N=$((N+1))
        h_ref=$(sha256sum "$EX/$f" 2>/dev/null | cut -d' ' -f1)
        h_cur=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)
        if [ -z "$h_ref" ] || [ "$h_ref" != "$h_cur" ]; then
            echo "  CANVIAT: $f" | tee -a "$GATE_LOG"; REG=1
        fi
    done
    rm -rf "$EX"
    if [ $REG -eq 0 ] && [ $N -eq 12 ]; then
        echo "  regression: PASS (12/12 byte-for-byte = R3.1)" | tee -a "$GATE_LOG"
    else
        echo "  regression: FAIL ($N fitxers, $REG canviats)" | tee -a "$GATE_LOG"; OVERALL=1
    fi
else
    echo "  regression: NO EXECUTADA (tarball R3.1 absent)" | tee -a "$GATE_LOG"; OVERALL=1
fi

# 11) artefactes
echo "" | tee -a "$GATE_LOG"; echo "=== artefactes ===" | tee -a "$GATE_LOG"
ART_OK=1
# el fitxer temporal de conversió no és un artefacte oficial
rm -f "$OUT/adapter_2_qwen.bundle"
for a in adapter_0.bundle adapter_1.bundle adapter_2.bundle \
         distributed_metrics.json quality_before.json quality_after.json; do
    if [ -s "$OUT/$a" ]; then echo "  artefacte $a: OK" | tee -a "$GATE_LOG"
    else echo "  artefacte $a: ABSENT" | tee -a "$GATE_LOG"; ART_OK=0; fi
done
[ $ART_OK -eq 1 ] && echo "  artefactes: PASS" | tee -a "$GATE_LOG" \
                   || { echo "  artefactes: FAIL" | tee -a "$GATE_LOG"; OVERALL=1; }

echo "" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"
echo "  GENERIC DISTRIBUTED FINAL GATE (R2.2) — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"
exit $OVERALL
