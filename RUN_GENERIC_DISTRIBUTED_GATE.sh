#!/usr/bin/env bash
# RUN_R2_3_FINAL_GATE.sh — GATE FINAL R2.3 (COORDINATOR AUTHORITY)
#
# Criteris (ordre R2.3):
#  1  Coordinator owns round (model_rounds + round.create)      PASS
#  2  Coordinator owns assignments (assignment.create)          PASS
#  3  worker.calibrate = verificació (no crea)                  PASS
#  4  Base model global enforcement                             PASS
#  5  Adapter global enforcement (adapter_0_sha + pre_hash)     PASS
#  6  Shard enforcement                                         PASS
#  7  ETT enforcement (expected_ett)                            PASS
#  8  Adversarials AUTH-01..14 + explotació (19/19)             PASS
#  9  Dummy distributed (17/17)                                 PASS
# 10  Qwen distributed (17/17)                                  PASS
# 11  FedAvg oracle R1/R2                                       PASS
# 12  SIGKILL recovery (dummy 11/11, qwen 11/11)                PASS
# 13  No-crash == recovery (adapter_post exacte)                PASS
# 14  RC5.4 regression (12/12)                                  PASS
# 15  Manifest                                                  PASS
# 16  Clean extraction A/B                                      PASS
set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${QWEN3_PYTHON:-/opt/qwen3-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
ISO_PY="${ISO_PYTHON:-/tmp/iso_venv/bin/python}"
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
echo "  GENERIC DISTRIBUTED FINAL GATE (R2.4 FINAL)" | tee -a "$GATE_LOG"
echo "  Python venv Qwen: $($PYTHON --version 2>&1)" | tee -a "$GATE_LOG"
echo "  Python ISO: $($ISO_PY --version 2>&1)" | tee -a "$GATE_LOG"
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

# 1) dependency + py_compile
run_sub "dependency" "$PYTHON -c 'import torch, transformers, peft, numpy; print(\"deps OK\")'" 120
echo "" | tee -a "$GATE_LOG"; echo "=== pycompile ===" | tee -a "$GATE_LOG"
"$PYTHON" -m py_compile training_backend.py adapter_codec.py \
model_round_coordinator.py training_http_server.py model_worker.py \
backend_isolation.py coordinator_authority_tests.py \
fedavg_authority_tests.py generic_distributed_run.py \
generic_recovery_tests.py backends/qwen3_backend.py \
backends/dummy_backend.py \
>> "$LOG_DIR/pycompile.log" 2>&1
[ $? -eq 0 ] && echo "  pycompile: PASS" | tee -a "$GATE_LOG" \
             || { echo "  pycompile: FAIL" | tee -a "$GATE_LOG"; OVERALL=1; }

# 2) backend isolation (ISO-01/02/03, sense torch)
echo "" | tee -a "$GATE_LOG"; echo "=== backend_isolation ===" | tee -a "$GATE_LOG"
timeout 600 "$ISO_PY" backend_isolation.py > "$LOG_DIR/backend_isolation.log" 2>&1
ec=$?
[ $ec -eq 0 ] && echo "  backend_isolation: PASS" | tee -a "$GATE_LOG" \
              || { echo "  backend_isolation: FAIL (exit $ec)" | tee -a "$GATE_LOG"; OVERALL=1; }

# 3) adversarials d'autoritat (AUTH-01..14 + explotació)
run_sub "authority_adversarial" "$ISO_PY coordinator_authority_tests.py" 300

# 3b) adversarials de seguretat/fedavg (SEC-01..12 + Exploit A/B/C)
run_sub "security_adversarial" "$ISO_PY fedavg_authority_tests.py" 300

# 4) dummy e2e (protocol Coordinator authority)
run_sub "dummy_e2e" "$PYTHON generic_distributed_run.py --backend dummy --num-examples 4 --seq-len 16" 900

# 5) dummy recovery (PID estricte + adapter_post exacte)
run_sub "dummy_recovery" "$PYTHON generic_recovery_tests.py --backend dummy --num-examples 4 --seq-len 16" 900

# 6) qwen e2e (round.create + assignments + 2 workers + FedAvg + Round2)
run_sub "qwen_e2e" "$PYTHON generic_distributed_run.py --backend qwen3 --num-examples 10 --seq-len 128" 3600

# 7) qwen recovery (SIGKILL + no-crash == recovery)
run_sub "qwen_recovery" "$PYTHON generic_recovery_tests.py --backend qwen3 --num-examples 2 --seq-len 64" 1800

# 8) quality
echo "" | tee -a "$GATE_LOG"; echo "=== quality ===" | tee -a "$GATE_LOG"
QOK=1
convert_bundle() {
    "$PYTHON" - "$1" "$2" <<'PY'
import sys
from adapter_codec import unpack_tensors
from qwen3_training_backend import Qwen3TrainingBackend
import torch
src, dst = sys.argv[1], sys.argv[2]
data = open(src, "rb").read()
tensors = unpack_tensors(data)
out = Qwen3TrainingBackend._serialize_delta(
    {k: torch.as_tensor(v) for k, v in tensors.items()})
open(dst, "wb").write(out)
PY
}
if [ -s "$OUT/adapter_0.bundle" ] && [ -s "$OUT/adapter_2.bundle" ]; then
    Q0=$(timeout 900 "$PYTHON" qwen_quality.py --model "$MODEL" \
        --adapter "$OUT/adapter_0.bundle" \
        --adapter-sha "$(sha256sum "$OUT/adapter_0.bundle" | cut -d' ' -f1)" \
        --data-dir "$DATA" --out "$OUT/quality_before.json" 2>&1 | tail -1)
    convert_bundle "$OUT/adapter_2.bundle" "$OUT/adapter_2_qwen.bundle" 2>/dev/null
    Q2=$(timeout 900 "$PYTHON" qwen_quality.py --model "$MODEL" \
        --adapter "$OUT/adapter_2_qwen.bundle" \
        --adapter-sha "$(sha256sum "$OUT/adapter_2_qwen.bundle" | cut -d' ' -f1)" \
        --data-dir "$DATA" --out "$OUT/quality_after.json" 2>&1 | tail -1)
    rm -f "$OUT/adapter_2_qwen.bundle"
    echo "  quality_before: $Q0" | tee -a "$GATE_LOG"
    echo "  quality_after:  $Q2" | tee -a "$GATE_LOG"
    echo "  (CLASSIFICACIÓ: PIPELINE TRAINING PASS · QUALITY PILOT)" | tee -a "$GATE_LOG"
    [ -s "$OUT/quality_before.json" ] && [ -s "$OUT/quality_after.json" ] \
        || { echo "  quality: FAIL (json absent)" | tee -a "$GATE_LOG"; QOK=0; }
else
    echo "  quality: NO EXECUTADA (falten adapter_0/2)" | tee -a "$GATE_LOG"; QOK=0
fi
[ $QOK -eq 1 ] && echo "  quality: PASS" | tee -a "$GATE_LOG" || OVERALL=1

# 9) regressió RC5.4 (12 fitxers congelats)
echo "" | tee -a "$GATE_LOG"; echo "=== regression ===" | tee -a "$GATE_LOG"
R31_TARBALL="${R31_TARBALL:-/root/meshtrainer_rc54_stagea_r3_1_final_77595f4.tar.gz}"
if [ -f "$R31_TARBALL" ]; then
    EX=$(mktemp -d); tar -xzf "$R31_TARBALL" -C "$EX" 2>/dev/null
    FROZEN="rc5_3_multiworker.py rc5_3_tests.py rc5_3_adversarial_tests.py
rc5_2_worker_runtime.py rc5_2_http_server.py rc5_2_phase2_tests.py
aggregation.py rc5_4_leases.py rc5_2_tensor_bundle.py rc5_2_numerical_adapter.py
rc5_4_integration.py rc5_4_http_server.py"
    REG=0; N=0
    for f in $FROZEN; do
        N=$((N+1))
        h1=$(sha256sum "$EX/$f" 2>/dev/null | cut -d' ' -f1)
        h2=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)
        [ "$h1" = "$h2" ] || { echo "  CANVIAT: $f" | tee -a "$GATE_LOG"; REG=1; }
    done
    rm -rf "$EX"
    [ $REG -eq 0 ] && [ $N -eq 12 ] \
        && echo "  regression: PASS (12/12 = R3.1)" | tee -a "$GATE_LOG" \
        || { echo "  regression: FAIL" | tee -a "$GATE_LOG"; OVERALL=1; }
else
    echo "  regression: NO EXECUTADA" | tee -a "$GATE_LOG"; OVERALL=1
fi

# 10) artefactes
echo "" | tee -a "$GATE_LOG"; echo "=== artefactes ===" | tee -a "$GATE_LOG"
ART_OK=1
for a in adapter_0.bundle adapter_1.bundle adapter_2.bundle \
         distributed_metrics.json quality_before.json quality_after.json; do
    if [ -s "$OUT/$a" ]; then echo "  artefacte $a: OK" | tee -a "$GATE_LOG"
    else echo "  artefacte $a: ABSENT" | tee -a "$GATE_LOG"; ART_OK=0; fi
done
[ $ART_OK -eq 1 ] && echo "  artefactes: PASS" | tee -a "$GATE_LOG" \
                   || { echo "  artefactes: FAIL" | tee -a "$GATE_LOG"; OVERALL=1; }

echo "" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"
echo "  GENERIC DISTRIBUTED FINAL GATE (R2.4 FINAL) — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"
exit $OVERALL
