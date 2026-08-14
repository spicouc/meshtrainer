#!/usr/bin/env bash
# RUN_R2_5_2_EVIDENCE.sh — R2.5.2 EVIDENCE-ONLY CLOSEOUT
#
# NO modifica cap .py/.sh funcional (ordre R2.5.2, punt 1).
# Reexecuta TOTS els gates sobre el commit funcional congelat b146723
# i guarda logs NOUS i inequívocs a evidence/final/ (punt 3):
#   dependency, pycompile, backend_isolation, authority_adversarial,
#   security_validation, dummy_e2e, dummy_recovery, qwen_e2e,
#   qwen_recovery, quality, regression, FINAL_GATE
#
# Ús: LOG_DIR=... ISO_PYTHON=... bash RUN_R2_5_2_EVIDENCE.sh
set -u
REPO="/root/meshtrainer"
cd "$REPO" || exit 2

PYTHON="${PYTHON:-/opt/qwen3-venv/bin/python}"
ISO_PY="${ISO_PYTHON:-/tmp/iso_venv/bin/python}"
MODEL="${QWEN3_MODEL:-/root/qwen3_0_6b_snapshot}"
DATA="${QWEN3_DATA:-/root/meshtrainer/qwen3_pilot_data}"
EVID="${EVIDENCE_DIR:-$REPO/evidence/final}"
mkdir -p "$EVID"
GATE_LOG="$EVID/FINAL_GATE.log"
OVERALL=0

echo "============================================" | tee "$GATE_LOG"
echo "  R2.5.2 EVIDENCE-ONLY FINAL GATE (b146723)" | tee -a "$GATE_LOG"
echo "  Python venv Qwen: $($PYTHON --version 2>&1)" | tee -a "$GATE_LOG"
echo "  Python ISO: $($ISO_PY --version 2>&1)" | tee -a "$GATE_LOG"
echo "  Model: $MODEL" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"

run_sub() {  # label, cmd, timeout
    local label="$1" cmd="$2" tmo="${3:-300}"
    echo "" | tee -a "$GATE_LOG"; echo "=== $label ===" | tee -a "$GATE_LOG"
    timeout "$tmo" bash -c "$cmd" > "$EVID/$label.log" 2>&1
    local ec=$?
    if [ $ec -eq 0 ]; then echo "  $label: PASS" | tee -a "$GATE_LOG"
    else echo "  $label: FAIL (exit $ec)" | tee -a "$GATE_LOG"; OVERALL=1; fi
}

# 1) dependency
run_sub dependency "$PYTHON -c 'import torch, transformers, peft, numpy; print(\"deps OK\")'" 120

# 2) pycompile (fitxers funcionals congelats)
run_sub pycompile "$PYTHON -m py_compile training_backend.py adapter_codec.py model_round_coordinator.py training_http_server.py model_worker.py backend_isolation.py coordinator_authority_tests.py fedavg_authority_tests.py generic_distributed_run.py generic_recovery_tests.py backends/qwen3_backend.py backends/dummy_backend.py" 120

# 3) backend isolation (venv sense torch/transformers/peft)
run_sub backend_isolation "$ISO_PY backend_isolation.py" 300

# 4) AUTH-01..14 + exploit (R2.3/R2.5)
run_sub authority_adversarial "$ISO_PY coordinator_authority_tests.py" 300

# 5) SEC-01..15 + VAL-01..08 + Exploit A/B/C (R2.4/R2.5.1)
run_sub security_validation "$ISO_PY fedavg_authority_tests.py" 300

# 6) dummy e2e (protocol Coordinator authority + admin boundary)
run_sub dummy_e2e "$PYTHON generic_distributed_run.py --backend dummy --num-examples 4 --seq-len 16" 900

# 7) dummy recovery (SIGKILL real + adapter_post exacte)
run_sub dummy_recovery "$PYTHON generic_recovery_tests.py --backend dummy --num-examples 4 --seq-len 16" 900

# 8) qwen e2e (model real, Round 1 + Round 2, oracle)
run_sub qwen_e2e "$PYTHON generic_distributed_run.py --backend qwen3 --num-examples 4 --seq-len 64" 2400

# 9) qwen recovery (SIGKILL real + crash_pid != recovery_pid)
# alliberem memòria abans (el qwen_e2e acaba de carregar models)
sync; pctfree=1; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
sleep 20
run_sub qwen_recovery "$PYTHON generic_recovery_tests.py --backend qwen3 --num-examples 2 --seq-len 64" 1700

# 10) quality (before/after sobre artefactes reals)
echo "" | tee -a "$GATE_LOG"; echo "=== quality ===" | tee -a "$GATE_LOG"
QOK=1
if [ -s "$REPO/qwen_distributed_output/adapter_0.bundle" ] && [ -s "$REPO/qwen_distributed_output/adapter_2.bundle" ]; then
    "$PYTHON" qwen_quality.py --model "$MODEL" \
        --adapter "$REPO/qwen_distributed_output/adapter_0.bundle" \
        --adapter-sha "$(sha256sum "$REPO/qwen_distributed_output/adapter_0.bundle" | cut -d' ' -f1)" \
        --data-dir "$DATA" --out "$REPO/qwen_distributed_output/quality_before.json" \
        >> "$EVID/quality.log" 2>&1 \
        && echo "quality_before: QUALITY OK" >> "$EVID/quality.log" || QOK=0
    # adapter_2 és adapter_bundle_v1 (FedAvg genèric) → converteix a
    # qwen_bundle_v1 amb el MATEIX serialitzador del backend certificat
    "$PYTHON" -c "
import sys
sys.path.insert(0, '$REPO')
from adapter_codec import unpack_tensors
from qwen3_training_backend import Qwen3TrainingBackend
import torch
data = open('$REPO/qwen_distributed_output/adapter_2.bundle','rb').read()
tensors = unpack_tensors(data)
out = Qwen3TrainingBackend._serialize_delta(
    {k: torch.as_tensor(v) for k, v in tensors.items()})
open('/tmp/adapter_2_qwen.bundle','wb').write(out)
print('convertit adapter_2 → qwen_bundle_v1:', len(out), 'bytes')
" >> "$EVID/quality.log" 2>&1
    "$PYTHON" qwen_quality.py --model "$MODEL" \
        --adapter /tmp/adapter_2_qwen.bundle \
        --adapter-sha "$(sha256sum /tmp/adapter_2_qwen.bundle | cut -d' ' -f1)" \
        --data-dir "$DATA" --out "$REPO/qwen_distributed_output/quality_after.json" \
        >> "$EVID/quality.log" 2>&1 \
        && echo "quality_after: QUALITY OK" >> "$EVID/quality.log" || QOK=0
    rm -f /tmp/adapter_2_qwen.bundle
else
    echo "artefactes quality ABSENTS" >> "$EVID/quality.log"; QOK=0
fi
if [ $QOK -eq 0 ]; then echo "  quality: FAIL" | tee -a "$GATE_LOG"; OVERALL=1
else echo "  quality: PASS" | tee -a "$GATE_LOG"; fi

# 11) regression RC5.4: 12 fitxers congelats == R3.1 (byte-for-byte)
echo "" | tee -a "$GATE_LOG"; echo "=== regression ===" | tee -a "$GATE_LOG"
R31_TARBALL="${R31_TARBALL:-/root/meshtrainer_rc54_stagea_r3_1_final_77595f4.tar.gz}"
REGR_OK=1; N=0
if [ -f "$R31_TARBALL" ]; then
    EX=$(mktemp -d)
    tar -xzf "$R31_TARBALL" -C "$EX" 2>/dev/null
    FROZEN="rc5_3_multiworker.py rc5_3_tests.py rc5_3_adversarial_tests.py
rc5_2_worker_runtime.py rc5_2_http_server.py rc5_2_phase2_tests.py
aggregation.py rc5_4_leases.py rc5_2_tensor_bundle.py rc5_2_numerical_adapter.py
rc5_4_integration.py rc5_4_http_server.py"
    for f in $FROZEN; do
        N=$((N+1))
        h1=$(sha256sum "$EX/$f" 2>/dev/null | cut -d' ' -f1)
        h2=$(sha256sum "$REPO/$f" 2>/dev/null | cut -d' ' -f1)
        if [ "$h1" = "$h2" ] && [ -n "$h1" ]; then
            echo "IDENTICAL  $f" >> "$EVID/regression.log"
        else
            echo "DIFF       $f  r31=$h1 now=$h2" >> "$EVID/regression.log"; REGR_OK=0
        fi
    done
    rm -rf "$EX"
else
    echo "tarball R3.1 NO trobat: $R31_TARBALL" >> "$EVID/regression.log"; REGR_OK=0
fi
if [ $REGR_OK -eq 0 ] || [ "$N" -ne 12 ]; then echo "  regression: FAIL ($N/12)" | tee -a "$GATE_LOG"; OVERALL=1
else echo "  regression: PASS ($N/12 = R3.1)" | tee -a "$GATE_LOG"; fi

# 12) artefactes qwen presents
echo "" | tee -a "$GATE_LOG"; echo "=== artefactes ===" | tee -a "$GATE_LOG"
ART_OK=1
for a in adapter_0.bundle adapter_1.bundle adapter_2.bundle \
         distributed_metrics.json evidence_w-A.json evidence_w-B.json \
         evidence_w-A2.json evidence_w-B2.json quality_before.json \
         quality_after.json; do
    if [ -s "$REPO/qwen_distributed_output/$a" ]; then
        echo "artefacte $a: OK" | tee -a "$GATE_LOG"
    else
        echo "artefacte $a: ABSENT" | tee -a "$GATE_LOG"; ART_OK=0
    fi
done
[ $ART_OK -eq 0 ] && OVERALL=1

echo "" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"
echo "  R2.5.2 EVIDENCE-ONLY FINAL GATE (b146723) — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"
exit $OVERALL
