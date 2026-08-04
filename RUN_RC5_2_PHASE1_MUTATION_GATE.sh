#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 1 R5 — FINAL MUTATION GATE"
echo "============================================"
SRC="$(pwd)"; TMPDIR="${TMPDIR:-/tmp}/p1r3_mut_$$"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile *.py && echo "  py_compile: PASS"
set +e; timeout 300 python3 rc5_2_phase1_tests.py > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/
SCORE=0; I=0; R=0; NA=0; T=0

for i in 1 2 3 4 5 6; do
    W="$TMPDIR/mut_$i"; rm -rf "$W"; mkdir -p "$W"; cp "$TMPDIR"/*.py "$W"/; cd "$W"
    OK=0; NAME=""
    case $i in
        1) NAME="MUT-P1-01-randn-grad"
           sed -i 's/cut_activation.backward(cut_gradient)/cut_activation.backward(torch.randn_like(cut_activation))/' rc5_2_numerical_models.py
           grep -c 'randn_like' rc5_2_numerical_models.py | grep -q '^1$' || OK=1 ;;
        2) NAME="MUT-P1-02-server-ignore-labels"
           # Replace labels in SplitServerNumericalModel ONLY (not monolithic)
           sed -i '/class SplitServerNumericalModel/,/class WorkerNumericalModel/s/shift_labels.view(-1)/(100+torch.zeros_like(shift_labels)).view(-1)/' rc5_2_numerical_models.py
           CNT=$(grep -c 'zeros_like(shift_labels)' rc5_2_numerical_models.py)
           [ "$CNT" -eq 1 ] || OK=1 ;;  # one code occurrence
        3) NAME="MUT-P1-03-bypass-worker-diff"
           # Differentiable bypass: keep LoRA in graph but zero out local layers
           sed -i '/def worker_forward/,/return x/{/for layer in self.local_layers:/{N;s/for layer.*causal_mask)$/x = server_activation + 0.0 * (self.lora_wrapper.lora_A.weight.sum() + self.lora_wrapper.lora_B.weight.sum()) # mut: bypass/}}' rc5_2_numerical_models.py
           grep -c 'mut: bypass' rc5_2_numerical_models.py | grep -q '^1$' || OK=1 ;;
        4) NAME="MUT-P1-04-extra-param"
           sed -i 's/\[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight\]/[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight, next(self.local_layers[0].self_attn.out_proj.parameters())]/' rc5_2_numerical_models.py
           grep -c 'out_proj.parameters' rc5_2_numerical_models.py | grep -q '^1$' || OK=1 ;;
        5) NAME="MUT-P1-05-alter-gradient"
           sed -i 's/\(cut_gradient = torch\.autograd\.grad(loss, cut_leaf, retain_graph=False)\)\[0\]\.detach()\.clone()/cg = \1[0].detach().clone(); cg[0,0,0] = cg[0,0,0] + 0.1; cut_gradient = cg/' rc5_2_numerical_models.py
           grep -c 'cg\[0,0,0\] = cg\[0,0,0\]' rc5_2_numerical_models.py | grep -q '^1$' || OK=1 ;;
        6) NAME="MUT-P1-06-relu-worker-lora"
           # Worker-only ReLU between LoRA A and B (Python script, precise)
           python3 mut06_relu_worker.py
           CNT=$(grep -c 'mut: ReLU' rc5_2_numerical_models.py 2>/dev/null || true)
           [ "$CNT" -eq 1 ] || OK=1 ;;
    esac
    [ "$OK" -ne 0 ] && echo "  $NAME: NOT APPLIED" && NA=$((NA+1)) && continue
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ "$C" -ne 0 ] && echo "  $NAME: INVALID" && I=$((I+1)) && continue
    set +e; timeout 120 python3 rc5_2_phase1_tests.py > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ "$S" -eq 124 ] && echo "  $NAME: TIMEOUT" && T=$((T+1)) && continue
    # ANY traceback = RUNTIME-INVALID (R4-02), regardless of assertion count
    TRACE=$(grep -c 'Traceback\|Error:\|Exception:\|TypeError\|ValueError\|RuntimeError\|IndexError\|KeyError' "mutation_$NAME.log" 2>/dev/null || true)
    if [ "$TRACE" -gt 0 ]; then
        echo "  $NAME: RUNTIME-INVALID" && R=$((R+1)) && continue
    fi
    [ "$S" -ne 0 ] && echo "  $NAME: DETECTED" && SCORE=$((SCORE+1)) && continue
    echo "  $NAME: NOT DETECTED"
done
echo ""; echo "Score: $SCORE/6 Invalid: $I Runtime: $R Not_applied: $NA Timeouts: $T"
rm -rf "$TMPDIR"
[ "$SCORE" -eq 6 ] && [ "$I" -eq 0 ] && [ "$R" -eq 0 ] && [ "$NA" -eq 0 ] && exit 0 || exit 1
