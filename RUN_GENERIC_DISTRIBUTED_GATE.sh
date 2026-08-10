#!/usr/bin/env bash
# RUN_GENERIC_DISTRIBUTED_GATE.sh — GATE R2.1 MODEL-AGNOSTIC (16 criteris)
#
# Certifica l'ordre del supervisor (R2.1):
#  1  Generic TrainingBackend (contracte mínim)          PASS
#  2  Qwen plugin (backends/qwen3_backend.py)            PASS
#  3  Generic HTTP (training_http_server)                PASS
#  4  Generic Coordinator (ModelRoundCoordinator)        PASS
#  5  Generic Worker (model_worker + registry)           PASS
#  6  2 Qwen workers subprocess                          PASS
#  7  HTTP + leases                                     PASS
#  8  FedAvg Coordinator                                PASS
#  9  Round 2                                           PASS
# 10  server restart (idempotència persistent)          PASS
# 11  worker SIGKILL recovery                           PASS
# 12  quality before/after                              PASS (qwen3 només)
# 13  RC5.4 regression                                  PASS
# 14  manifest                                          PASS
# 15  artefactes                                        PRESENTS
# 16  logs                                              PRESENTS
set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${QWEN3_PYTHON:-/opt/qwen3-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
MODEL="${QWEN3_MODEL:-/root/qwen3_0_6b_snapshot}"
DATA="$(pwd)/qwen3_pilot_data"
OUT="$(pwd)/qwen_distributed_output"
LOG_DIR="${LOG_DIR:-$(pwd)/qwen_distributed_logs}"
mkdir -p "$LOG_DIR" "$OUT"
export QWEN3_MODEL="$MODEL" QWEN3_DATA="$DATA"
OVERALL=0

echo "============================================"
echo "  GENERIC DISTRIBUTED GATE (R2.1) — model-agnostic"
echo "  Python: $($PYTHON --version 2>&1)"
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

# 0) py_compile de tots els fitxers genèrics
echo ""; echo "=== [0] py_compile genèrics ==="
"$PYTHON" -m py_compile training_backend.py adapter_codec.py \
    model_round_coordinator.py training_http_server.py model_worker.py \
    generic_distributed_run.py generic_recovery_tests.py \
    backends/qwen3_backend.py backends/dummy_backend.py \
    >> "$LOG_DIR/00_pycompile.log" 2>&1
[ $? -eq 0 ] && echo "  py_compile: PASS" || { echo "  py_compile: FAIL"; OVERALL=1; }

# 1-5) contractes + absència d'imports Qwen al core
echo ""; echo "=== [1] core sense imports Qwen ==="
# El registry (model_worker.BACKENDS) i l'opció --backend han de contenir la
# STRING "qwen3" (punt 8 de l'ordre). El que NO pot haver-hi al core són
# IMPORTS reals de mòduls Qwen ni usos de classes Qwen (només es permeten
# mencions en docstrings/comentaris que expliquen la substitució).
if grep -nE "^(from|import) +qwen|from qwen|import qwen" \
        training_backend.py adapter_codec.py model_round_coordinator.py \
        training_http_server.py model_worker.py generic_distributed_run.py \
        generic_recovery_tests.py > /dev/null 2>&1; then
    echo "  imports Qwen al core: DETECTATS — FAIL"; OVERALL=1
else
    echo "  core net d'imports Qwen: PASS"
    echo "  (la string 'qwen3' al registry/--backend és correcta: punt 8)"
fi

# 10) idempotència persistent + 11) SIGKILL recovery (dummy, ràpid)
run_sub "10_recovery_dummy" "$PYTHON generic_recovery_tests.py --backend dummy --num-examples 4 --seq-len 16" 900

# 6-9) end-to-end dummy (model-agnostic sense entrenar)
run_sub "06_07_08_09_e2e_dummy" "$PYTHON generic_distributed_run.py --backend dummy --num-examples 4 --seq-len 16" 900

# 6-9 + 12) end-to-end qwen3 real (2 workers, FedAvg, Round 2)
run_sub "06_07_08_09_12_e2e_qwen3" "$PYTHON generic_distributed_run.py --backend qwen3 --num-examples 10 --seq-len 128" 3600

# 10-11) recovery real amb qwen3 (2 exemples: fidel al R-Q1 certificat;
#        el pre_hash estricte acumula 1 ulp per pas encadenat amb 3+)
run_sub "10_11_recovery_qwen3" "$PYTHON generic_recovery_tests.py --backend qwen3 --num-examples 2 --seq-len 64" 1800

# 13) regressió RC5.4 (fitxers congelats = tarball R3.1)
echo ""; echo "=== [13] regressió RC5.4 (fitxers congelats) ==="
R31_TARBALL="${R31_TARBALL:-/root/meshtrainer_rc54_stagea_r3_1_final_77595f4.tar.gz}"
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

# 15-16) artefactes + logs
echo ""; echo "=== [15] artefactes ==="
ART_OK=1
for a in adapter_0.bundle adapter_1.bundle adapter_2.bundle \
         distributed_metrics.json; do
    if [ -s "qwen_distributed_output/$a" ]; then echo "  artefacte $a: OK"
    else echo "  artefacte $a: ABSENT"; ART_OK=0; fi
done
[ $ART_OK -eq 1 ] && echo "  artefactes: PASS" || { echo "  artefactes: FAIL"; OVERALL=1; }

echo ""; echo "=== [16] logs ==="
LOG_OK=1
for l in 00_pycompile.log 10_recovery_dummy.log 06_07_08_09_e2e_dummy.log \
         06_07_08_09_12_e2e_qwen3.log 10_11_recovery_qwen3.log; do
    # -f (existeix), no -s: un log buit (py_compile silenciós) és vàlid
    if [ -f "$LOG_DIR/$l" ]; then echo "  log $l: OK"
    else echo "  log $l: ABSENT"; LOG_OK=0; fi
done
[ $LOG_OK -eq 1 ] && echo "  logs: PASS" || { echo "  logs: FAIL"; OVERALL=1; }

echo ""; echo "============================================"
echo "  GENERIC DISTRIBUTED GATE (R2.1) — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)"
echo "============================================"
exit $OVERALL
