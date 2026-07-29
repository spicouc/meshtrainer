#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 1 — MUTATION GATE"
echo "============================================"
SRC="$(pwd)"; TMPDIR="/tmp/rc5_p1_$$"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile rc5_2_phase1_tests.py rc5_2_numerical_models.py rc5_2_lora.py rc5_2_numerical_profile.py
echo "  py_compile: PASS"
set +e; timeout 120 python3 rc5_2_phase1_tests.py > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/ 2>/dev/null || true
SCORE=0; TOTAL=6; RESULTS=""; INVALID=0; NOT_APPLIED=0; TIMEOUT_COUNT=0

for i in 1 2 3 4 5 6; do
    WORKDIR="$TMPDIR/mut_$i"; rm -rf "$WORKDIR" && mkdir -p "$WORKDIR"
    cp "$TMPDIR"/*.py "$WORKDIR"/; cd "$WORKDIR"
    case $i in
        1) NAME="MUT-P1-01-randn-grad"
           sed -i 's/cut_activation.backward(cut_gradient)/cut_activation.backward(torch.randn_like(cut_activation))/' rc5_2_numerical_models.py ;;
        2) NAME="MUT-P1-02-ignore-labels"
           sed -i 's/shift_labels.view(-1)/torch.zeros_like(shift_labels.view(-1))/' rc5_2_numerical_models.py ;;
        3) NAME="MUT-P1-03-bypass-worker"
           sed -i 's/x = layer(x, src_mask=causal_mask)/x = x  # bypass/' rc5_2_numerical_models.py ;;
        4) NAME="MUT-P1-04-base-in-optimizer"
           sed -i 's/\[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight\]/list(self.parameters())/' rc5_2_numerical_models.py ;;
        5) NAME="MUT-P1-05-alter-gradient"
           sed -i 's/cut_gradient = torch.autograd.grad(loss, x, retain_graph=True, create_graph=False)[0].detach().clone()/cg = torch.autograd.grad(loss, x, retain_graph=True, create_graph=False)[0].detach().clone(); cg[0,0,0] += 0.1; cut_gradient = cg/' rc5_2_numerical_models.py ;;
        6) NAME="MUT-P1-06-relu-lora"
           sed -i 's/lora_out = self.lora_B(self.lora_A(x)) \* self.scaling/lora_out = self.lora_B(torch.relu(self.lora_A(x))) * self.scaling/' rc5_2_lora.py ;;
    esac
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ $C -ne 0 ] && echo "  $NAME: INVALID" && RESULTS+="$NAME: INVALID"$'\n' && INVALID=$((INVALID+1)) && continue
    set +e; timeout 60 python3 rc5_2_phase1_tests.py > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ $S -eq 124 ] && echo "  $NAME: TIMEOUT" && RESULTS+="$NAME: TIMEOUT"$'\n' && TIMEOUT_COUNT=$((TIMEOUT_COUNT+1))
    [ $S -ne 0 ] && [ $S -ne 124 ] && echo "  $NAME: DETECTED" && RESULTS+="$NAME: DETECTED"$'\n' && SCORE=$((SCORE+1))
    [ $S -eq 0 ] && echo "  $NAME: NOT DETECTED" && RESULTS+="$NAME: NOT DETECTED"$'\n'
done
echo ""; echo "============================================"
echo "$RESULTS"; echo "Score: $SCORE/$TOTAL, Invalid: $INVALID, Timeouts: $TIMEOUT_COUNT"
echo "============================================"
rm -rf "$TMPDIR"; [ "$SCORE" -eq "$TOTAL" ] && [ "$INVALID" -eq 0 ] && exit 0 || exit 1
