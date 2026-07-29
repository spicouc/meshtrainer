#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 1 R2 — MUTATION GATE"
echo "============================================"
SRC="$(pwd)"; TMPDIR="/tmp/rc5_p1r2_$$"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile rc5_2_phase1_tests.py rc5_2_numerical_models.py rc5_2_lora.py rc5_2_numerical_profile.py && echo "  py_compile: PASS"
set +e; timeout 300 python3 rc5_2_phase1_tests.py > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/ 2>/dev/null || true
SCORE=0; TOTAL=6; INVALID=0; RUNTIME_INVALID=0; NOT_APPLIED=0; TIMEOUT_COUNT=0
MUTATION_SCRIPT='import sys; sys.path.insert(0,"."); import rc5_2_phase1_tests as _p
for n,f in _p.TESTS:
 if any(t in n for t in targets): print(f"--- {n} ---",flush=True); f()
sys.exit(1 if _p.FAIL > 0 else 0)'
# Target tests per mutant
SELECTED='{1:["N07","N06","N14","N15"],2:["N05","N06","N21"],3:["N03","N04","N11","N12"],4:["N10","N11"],5:["N07","N14","N15"],6:["N14","N15","N16","N17"]}'

for i in 1 2 3 4 5 6; do
    WORKDIR="$TMPDIR/mut_$i"; rm -rf "$WORKDIR" && mkdir -p "$WORKDIR"
    cp "$TMPDIR"/*.py "$WORKDIR"/; cd "$WORKDIR"
    ok=0;
    case $i in
        1) NAME="MUT-P1-01-randn-grad"
           sed -i 's/self.optimizer.zero_grad(set_to_none=True)/self.optimizer.zero_grad(set_to_none=True); #/' rc5_2_numerical_models.py
           # Replace backward call with random gradient
           sed -i 's/cut_activation.backward(cut_gradient)/cut_activation.backward(torch.randn_like(cut_activation))/' rc5_2_numerical_models.py
           grep -c 'randn_like' rc5_2_numerical_models.py || ok=1 ;;
        2) NAME="MUT-P1-02-ignore-labels"
           # Make loss ignore real labels
           sed -i 's/shift_labels.view(-1)/torch.zeros_like(shift_labels.view(-1))/' rc5_2_numerical_models.py
           grep -c 'zeros_like(shift_labels' rc5_2_numerical_models.py || ok=1 ;;
        3) NAME="MUT-P1-03-bypass-worker"
           # Replace just the two local layer forward calls with detach+return (differentiable)
           sed -i 's/for layer in self.local_layers:\n            x = layer(x, src_mask=causal_mask)/x = x.detach().requires_grad_(True)/' rc5_2_numerical_models.py
           grep -c 'detach().requires_grad_' rc5_2_numerical_models.py || ok=1 ;;
        4) NAME="MUT-P1-04-extra-param-in-opt"
           sed -i 's/\[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight\]/[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight, next(self.local_layers[0].self_attn.out_proj.parameters())]/' rc5_2_numerical_models.py
           grep -c 'out_proj.parameters()' rc5_2_numerical_models.py || ok=1 ;;
        5) NAME="MUT-P1-05-alter-gradient"
           # Alter one element of cut gradient
           sed -i 's/torch.autograd.grad(loss, cut_leaf, retain_graph=False)/g=torch.autograd.grad(loss, cut_leaf, retain_graph=False); g[0][0,0,0]=g[0][0,0,0]+1; g/' rc5_2_numerical_models.py
           grep -c 'g\[0\]\[0,0,0\]+=1' rc5_2_numerical_models.py || ok=1 ;;
        6) NAME="MUT-P1-06-relu-worker-lora"
           # Add ReLU between LoRA A and B only in WorkerNumericalModel
           sed -i '/class WorkerNumericalModel/,/return x/s/lora_out = self.lora_B(self.lora_A(x)) \* self.scaling/lora_out = self.lora_B(torch.relu(self.lora_A(x))) * self.scaling/' rc5_2_lora.py
           grep -c 'torch.relu(self.lora_A' rc5_2_lora.py || ok=1 ;;
    esac
    [ $ok -ne 0 ] && echo "  $NAME: NOT APPLIED" && NOT_APPLIED=$((NOT_APPLIED+1)) && RESULTS+="$NAME: NOT APPLIED"$'\n' && continue
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ $C -ne 0 ] && echo "  $NAME: INVALID" && INVALID=$((INVALID+1)) && RESULTS+="$NAME: INVALID"$'\n' && continue
    # Count mutation occurrences
    set +e; timeout 120 python3 -c "targets=$SELECTED; $MUTATION_SCRIPT" "$i" > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ $S -eq 124 ] && echo "  $NAME: TIMEOUT" && TIMEOUT_COUNT=$((TIMEOUT_COUNT+1)) && continue
    if [ $S -ne 0 ]; then
        # Check if exit was due to assertion fail (FAIL>0 in log) or crash
        CRASH=$(grep -c "Traceback\|RemoteDisconnected\|Error\|RUNTIME" "mutation_$NAME.log" 2>/dev/null || true)
        ASSERT_FAIL=$(grep -c "❌\|^FAIL>" "mutation_$NAME.log" 2>/dev/null || true)
        if [ $CRASH -gt 0 ] && [ $ASSERT_FAIL -eq 0 ]; then
            echo "  $NAME: RUNTIME-INVALID" && RUNTIME_INVALID=$((RUNTIME_INVALID+1)) && continue
        fi
        echo "  $NAME: DETECTED" && SCORE=$((SCORE+1)) && continue
    fi
    echo "  $NAME: NOT DETECTED"
done
echo ""; echo "============================================"
echo "Score: $SCORE/$TOTAL, Invalid: $INVALID, Runtime: $RUNTIME_INVALID, Not applied: $NOT_APPLIED, Timeouts: $TIMEOUT_COUNT"
echo "============================================"
rm -rf "$TMPDIR"
[ "$SCORE" -eq "$TOTAL" ] && [ "$INVALID" -eq 0 ] && [ "$RUNTIME_INVALID" -eq 0 ] && [ "$NOT_APPLIED" -eq 0 ] && exit 0 || exit 1
