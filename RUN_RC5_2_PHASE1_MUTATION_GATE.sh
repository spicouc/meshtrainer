#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 1 R2 — MUTATION GATE"
echo "============================================"
SRC="$(pwd)"; TMPDIR="/tmp/p1r2_mutation_$$"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile rc5_2_phase1_tests.py rc5_2_numerical_models.py rc5_2_lora.py rc5_2_numerical_profile.py && echo "  py_compile: PASS"
set +e; timeout 300 python3 rc5_2_phase1_tests.py > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/ 2>/dev/null || true
SCORE=0; I=0; R=0; NA=0; T=0

for i in 1 2 3 4 5 6; do
    W="$TMPDIR/mut_$i"; rm -rf "$W" && mkdir -p "$W"
    cp "$TMPDIR"/*.py "$W"/; cd "$W"
    OK=0; NAME=""
    case $i in
        1) NAME="MUT-P1-01-randn-grad"
           sed -i 's/cut_activation.backward(cut_gradient)/cut_activation.backward(torch.randn_like(cut_activation))/' rc5_2_numerical_models.py
           grep -qc 'randn_like' rc5_2_numerical_models.py || OK=1 ;;
        2) NAME="MUT-P1-02-ignore-labels"
           # Replace labels argument in loss call only
           sed -i 's/shift_labels.view(-1)/(100+torch.zeros_like(shift_labels)).view(-1)/' rc5_2_numerical_models.py
           grep -qc 'zeros_like(shift_labels)' rc5_2_numerical_models.py || OK=1 ;;
        3) NAME="MUT-P1-03-bypass-worker"
           # Replace 'for layer' + its body line with a single identity line (worker only)
           sed -i '/def worker_forward/,/return x/{/for layer in self.local_layers:/{N;s/for layer.*causal_mask)$/x = x.detach().requires_grad_(True) # mut: bypass/}}' rc5_2_numerical_models.py
           grep -qc 'mut: bypass' rc5_2_numerical_models.py || OK=1 ;;
        4) NAME="MUT-P1-04-extra-param"
           sed -i 's/\[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight\]/[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight, next(self.local_layers[0].self_attn.out_proj.parameters())]/' rc5_2_numerical_models.py
           grep -qc 'out_proj.parameters' rc5_2_numerical_models.py || OK=1 ;;
        5) NAME="MUT-P1-05-alter-gradient"
           # Alter one element of cut gradient (full line replacement)
           sed -i 's/cut_gradient = torch.autograd.grad(loss, cut_leaf, retain_graph=False)\[0\].detach().clone()/cg = torch.autograd.grad(loss, cut_leaf, retain_graph=False)[0].detach().clone(); cg[0,0,0] = cg[0,0,0] + 0.1; cut_gradient = cg/' rc5_2_numerical_models.py
           grep -qc 'cg\[0,0,0\] = cg\[0,0,0\]' rc5_2_numerical_models.py || OK=1 ;;
        6) NAME="MUT-P1-06-zero-scaling"
           # Remove LoRA scaling (equivalent to alpha=0, producing zero LoRA contribution)
           sed -i 's/\* self.scaling/* 0.0  # mut: zero scaling/' rc5_2_lora.py
           grep -qc 'mut: zero scaling' rc5_2_lora.py || OK=1 ;;
    esac
    [ "$OK" -ne 0 ] && echo "  $NAME: NOT APPLIED" && NA=$((NA+1)) && continue
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ "$C" -ne 0 ] && echo "  $NAME: INVALID" && I=$((I+1)) && continue
    set +e; timeout 120 python3 rc5_2_phase1_tests.py > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ "$S" -eq 124 ] && echo "  $NAME: TIMEOUT" && T=$((T+1)) && continue
    CRASH=$(grep -c 'Traceback\|Error\|Exception' "mutation_$NAME.log" 2>/dev/null || true)
    ASSRT=$(grep -c '❌' "mutation_$NAME.log" 2>/dev/null || true)
    if [ "$CRASH" -gt 0 ] && [ "$ASSRT" -eq 0 ]; then
        echo "  $NAME: RUNTIME-INVALID" && R=$((R+1)) && continue
    fi
    [ "$S" -ne 0 ] && echo "  $NAME: DETECTED" && SCORE=$((SCORE+1)) && continue
    echo "  $NAME: NOT DETECTED"
done
echo ""; echo "Score: $SCORE/6 Invalid: $I Runtime: $R Not_applied: $NA Timeouts: $T"
rm -rf "$TMPDIR"
[ "$SCORE" -eq 6 ] && [ "$I" -eq 0 ] && [ "$R" -eq 0 ] && [ "$NA" -eq 0 ] && exit 0 || exit 1
