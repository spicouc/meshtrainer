#!/usr/bin/env bash
# RUN_MINICPM5_PORTABILITY_GATE.sh — MiniCPM5 Portability Pilot (punt 23)
# Subgates: dependency, pycompile, backend isolation (ISO-M1..M4), backend
# unit, adversarial (MADV), single-worker, distributed R1, FedAvg oracle R1,
# Round 2, oracle R2, recovery, quality, qwen regression, RC5.4 regression,
# portability audit, artefacts, manifest.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
PYTHON="${PYTHON:-/opt/qwen3-venv/bin/python}"
ISO_PY="${ISO_PYTHON:-/tmp/iso_venv/bin/python}"
EVID="${EVIDENCE_DIR:-$HERE/evidence/minicpm5}"
mkdir -p "$EVID"
GATE_LOG="$EVID/FINAL_GATE.log"
OVERALL=0

run_sub() {
    label="$1"; cmd="$2"; tmo="${3:-600}"
    echo "" | tee -a "$GATE_LOG"
    echo "=== $label ===" | tee -a "$GATE_LOG"
    if timeout "$tmo" bash -c "$cmd" > "$EVID/$label.log" 2>&1; then
        echo "  $label: PASS" | tee -a "$GATE_LOG"
    else
        echo "  $label: FAIL (exit $?)" | tee -a "$GATE_LOG"; OVERALL=1
    fi
}

echo "============================================" | tee "$GATE_LOG"
echo "  MINICPM5 PORTABILITY GATE (b146723)" | tee -a "$GATE_LOG"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"

# 1 dependency (backend plugin imports)
run_sub dependency "$PYTHON -c \"import sys; sys.path.insert(0,'.'); from model_worker import load_backend; c=load_backend('minicpm5'); print('plugin minicpm5:', c.__name__)\"" 120
# 2 pycompile
run_sub pycompile "$PYTHON -m py_compile backends/minicpm5_backend.py minicpm5_isolation_tests.py minicpm5_adapter_adversarial_tests.py minicpm5_adversarial_tests.py && echo pycompile-OK" 120
# 3 backend isolation ISO-M1..M4
run_sub backend_isolation "$ISO_PY minicpm5_isolation_tests.py" 300
# 4 MiniCPM backend unit (single-worker smoke: loss finit, delta!=0, ETT>0, post!=pre, exactly-once, recovery)
run_sub backend_unit "$PYTHON -c \"
import sys, math, base64, os
sys.path.insert(0, '.')
if os.path.exists('/tmp/mc_gate_unit.db'): os.remove('/tmp/mc_gate_unit.db')
from model_worker import load_backend
bk = load_backend('minicpm5')('/root/minicpm5_1b_snapshot', db_path='/tmp/mc_gate_unit.db', seq_len=64)
bk.load_model()
tok = bk.tokenize_example('Què és la derivada?', 'La derivada mesura la taxa.')
assert bk.compute_ett(tok[2]) > 0, 'ETT > 0'
d64, dsha = bk.train_step('g-1', 'Què és la derivada?', 'La derivada mesura la taxa de variació instantània.', request_sha='rq'*32)
assert math.isfinite(bk._last_loss), 'loss finit'
d = bk.delta_tensors('g-1')
assert any(abs(v).max() > 0 for v in d.values()), 'delta != 0'
assert bk.adapter_hash() != bk._lora_pre and True, 'post != pre'
d64b, _ = bk.train_step('g-1', 'Què és la derivada?', 'La derivada mesura la taxa de variació instantània.', request_sha='rq'*32)
assert d64 == d64b, 'exactly-once'
try:
    bk.train_step('g-1', 'altre', 'payload', request_sha='ZZ'*32); raise SystemExit('no reject')
except ValueError: pass
bk.close()
print('UNIT PASS')
\"" 500
# 5 MiniCPM adversarial MADV-01..16
run_sub adversarial "$PYTHON minicpm5_adversarial_tests.py" 500
# 5b lease/revision REALS (R1.1: MREC-05, MREC-06, MADV-13, MADV-14)
run_sub lease_revision "$PYTHON minicpm5_lease_revision_tests.py" 200
# 6 adapter adversarial ADV-A01..07
run_sub adapter_adversarial "$PYTHON minicpm5_adapter_adversarial_tests.py" 500
# 7 single-worker real (via quality pilot versionat — R1.1 punt 9)
run_sub single_worker "$PYTHON minicpm5_quality.py" 600
# 8 distributed Round 1 + FedAvg + oracle R1 + Round 2 + oracle R2
# R1.1: outputs EXCLUSIUS de minicpm5 (mai qwen_distributed_output)
# R1.2: num-examples=6 -> A triga prou perquè B assoleixi la seva lease
# mentre A encara treballa (PAR-02: A_LEASE < B_LEASE < A_RELEASE) sense
# canviar model/dataset/seq_len/LoRA/f32
run_sub distributed "$PYTHON generic_distributed_run.py --backend minicpm5 --model /root/minicpm5_1b_snapshot --num-examples 6 --seq-len 64 --out-dir /root/meshtrainer/minicpm5_output" 2400
# 9 distributed recovery MREC
run_sub recovery "$PYTHON generic_recovery_tests.py --backend minicpm5 --num-examples 1 --seq-len 64" 1800
# 10 qwen regression (load + adapter + smoke)
run_sub qwen_regression "$PYTHON -c \"
import sys, hashlib
sys.path.insert(0, '.')
from model_worker import load_backend
bk = load_backend('qwen3')('/root/qwen3_0_6b_snapshot', db_path='/tmp/qwen_reg_minicpm.db', seq_len=64)
bk.load_model()
b = bk.adapter_to_bundle()
sha = hashlib.sha256(b).hexdigest()
bk.load_adapter(b, sha, strict=True)
tok = bk.tokenize_example('Hola', 'Resposta')
assert bk.compute_ett(tok[2]) > 0
bk.close()
print('QWEN REGRESSION PASS')
\"" 400
# 11 dummy regression — R1.1: directori SEPARAT + minicpm5_output immutable
run_sub dummy_regression "bash -c '
set -e
SNAP=\$(find /root/meshtrainer/minicpm5_output -type f -exec sha256sum {} + 2>/dev/null | sort | sha256sum | cut -d\" \" -f1)
timeout 400 /opt/qwen3-venv/bin/python generic_distributed_run.py --backend dummy --num-examples 4 --seq-len 16 --out-dir /tmp/minicpm5_dummy_regression_output
SNAP2=\$(find /root/meshtrainer/minicpm5_output -type f -exec sha256sum {} + 2>/dev/null | sort | sha256sum | cut -d\" \" -f1)
[ \"\$SNAP\" = \"\$SNAP2\" ] && echo \"minicpm5_output immutable després del dummy: OK\" || { echo \"minicpm5_output CANVIAT pel dummy!\"; exit 1; }
'" 500
# 12 RC5.4 regression 12/12
run_sub core_regression "bash -c '
R31=/root/meshtrainer_rc54_stagea_r3_1_final_77595f4.tar.gz
EX=\$(mktemp -d); tar -xzf \$R31 -C \$EX 2>/dev/null
N=0; BAD=0
for f in rc5_3_multiworker.py rc5_3_tests.py rc5_3_adversarial_tests.py rc5_2_worker_runtime.py rc5_2_http_server.py rc5_2_phase2_tests.py aggregation.py rc5_4_leases.py rc5_2_tensor_bundle.py rc5_2_numerical_adapter.py rc5_4_integration.py rc5_4_http_server.py; do
  N=\$((N+1))
  h1=\$(sha256sum \$EX/\$f 2>/dev/null | cut -d\" \" -f1); h2=\$(sha256sum \$f 2>/dev/null | cut -d\" \" -f1)
  [ \"\$h1\" = \"\$h2\" ] || { BAD=\$((BAD+1)); echo \"DIFF \$f\"; }
done
rm -rf \$EX
[ \$BAD -eq 0 ] && [ \$N -eq 12 ] && echo \"RC5.4: \$N/12 IDENTICAL\" || { echo \"RC5.4: FAIL \$((N-BAD))/\$N\"; exit 1; }
'" 120
# 13 portability audit (informe present + 0 canvis core declarats)
run_sub portability "bash -c '
[ -f PORTABILITY_REPORT.md ] && grep -q \"MULTI-MODEL PORTABILITY: PROVEN\" PORTABILITY_REPORT.md && echo \"PORTABILITY_REPORT.md OK\"
git diff --stat HEAD -- training_http_server.py model_round_coordinator.py 2>/dev/null | grep -q . && echo \"ALERTA: core canviat!\" && exit 1 || echo \"core sense canvis (Coordinator/HTTP)\"
'" 60
# 14 artefactes — R1.1: NOMÉS els fitxers produïts DIRECTAMENT per MiniCPM5
# (sense cap cp des de qwen_distributed_output)
run_sub artefacts "bash -c '
cd minicpm5_output 2>/dev/null || { echo \"minicpm5_output absent\"; exit 1; }
for f in adapter_0.bundle adapter_1.bundle adapter_2.bundle distributed_metrics.json evidence_w-A.json evidence_w-B.json evidence_w-A2.json evidence_w-B2.json quality_before.json quality_after.json; do
  [ -s \"\$f\" ] && echo \"artefacte \$f: OK\" || { echo \"artefacte \$f: ABSENT\"; exit 1; }
done
grep -q \"\\\"backend\\\": \\\"minicpm5\\\"\" distributed_metrics.json && echo \"metrics backend=minicpm5: OK\" || { echo \"metrics backend != minicpm5\"; exit 1; }
'" 60
# 14b artefact integrity — R1.1 punt 11 (sense carregar model)
run_sub artefact_integrity "$PYTHON minicpm5_artefact_integrity.py" 120
# 15 manifest
run_sub manifest "bash -c '
find . -type f ! -path \"./.git/*\" ! -path \"./.venv/*\" ! -path \"*/__pycache__/*\" ! -name \"*.pyc\" ! -name \"MANIFEST.sha256\" -print0 | sort -z | xargs -0 sha256sum > /tmp/mc_manifest.sha256
N=\$(wc -l < /tmp/mc_manifest.sha256)
R=\$(find . -type f ! -path \"./.git/*\" ! -path \"./.venv/*\" ! -path \"*/__pycache__/*\" ! -name \"*.pyc\" ! -name \"MANIFEST.sha256\" | wc -l)
E=\$(sha256sum -c /tmp/mc_manifest.sha256 2>&1 | grep -cv \": OK\")
echo \"manifest: \$N entrades, \$R regulars, \$E errors\"
[ \$N -eq \$R ] && [ \$E -eq 0 ]
'" 180

echo "" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"
echo "  MINICPM5 PORTABILITY GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$GATE_LOG"
echo "============================================" | tee -a "$GATE_LOG"
exit $OVERALL
