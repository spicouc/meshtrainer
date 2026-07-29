#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 1 — MUTATION GATE"
echo "============================================"
SRC="$(pwd)"; TMPDIR="/tmp/rc5_p1_$$"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile rc5_2_phase1_tests.py rc5_2_numerical_models.py rc5_2_lora.py rc5_2_numerical_profile.py
echo "  py_compile: PASS"
set +e; timeout 600 python3 rc5_2_phase1_tests.py > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/ 2>/dev/null || true
SCORE=0; TOTAL=6; RESULTS=""; INVALID=0; NOT_APPLIED=0; TIMEOUT_COUNT=0

for i in 1 2 3 4 5 6; do
    WORKDIR="$TMPDIR/mut_$i"; rm -rf "$WORKDIR" && mkdir -p "$WORKDIR"
    cp "$TMPDIR"/*.py "$WORKDIR"/; cd "$WORKDIR"
    case $i in
        1) NAME="MUT-P1-01-skip-optimizer"
           sed -i 's/self.optimizer.step()/# self.optimizer.step()  # mut: skip optimizer/' rc5_2_numerical_models.py ;;
        2) NAME="MUT-P1-02-ignore-labels"
           sed -i 's/labels = tokens.clone()/labels = torch.full_like(tokens, -100)/' rc5_2_phase1_tests.py ;;
        3) NAME="MUT-P1-03-bypass-worker"
           sed -i 's/x = layer(x, src_mask=causal_mask)/x = x  # bypass/' rc5_2_numerical_models.py ;;
        4) NAME="MUT-P1-04-extra-param-in-opt"
           sed -i 's/\[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight\]/[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight, next(self.local_layers[0].self_attn.out_proj.parameters())]/' rc5_2_numerical_models.py ;;
        5) NAME="MUT-P1-05-zero-grad"
           # Zero out gradients before optimizer step in worker_backward
           sed -i 's/self.optimizer.step()/self.lora_wrapper.lora_A.weight.grad.zero_(); self.lora_wrapper.lora_B.weight.grad.zero_(); self.optimizer.step()/' rc5_2_numerical_models.py ;;
        6) NAME="MUT-P1-06-no-worker-lora"
           # Remove LoRA from WorkerNumericalModel only (not MonolithicNumericalModel)
           sed -i '/class WorkerNumericalModel/,/self.local_layers\[0\]\.linear1 = self.lora_wrapper/{s/self.local_layers\[0\]\.linear1 = self.lora_wrapper/# mut: no LoRA in worker/}' rc5_2_numerical_models.py ;;
    esac
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ $C -ne 0 ] && echo "  $NAME: INVALID" && RESULTS+="$NAME: INVALID"$'\n' && INVALID=$((INVALID+1)) && continue
    set +e; timeout 120 python3 -c '
import sys; sys.path.insert(0, ".")
import rc5_2_phase1_tests as _p1t
selected = {1:["N14","N15","N16"],2:["N06","N05"],3:["N03","N04"],4:["N10"],5:["N14","N15","N16"],6:["N14","N15","N16"]}
targets = selected.get(int(sys.argv[1]), [])
for n,f in _p1t.TESTS:
    if any(t in n for t in targets):
        print(f"--- {n} ---", flush=True); f()
sys.exit(1 if _p1t.FAIL > 0 else 0)
' "$i" > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ $S -eq 124 ] && echo "  $NAME: TIMEOUT" && RESULTS+="$NAME: TIMEOUT"$'\n' && TIMEOUT_COUNT=$((TIMEOUT_COUNT+1))
    [ $S -ne 0 ] && [ $S -ne 124 ] && echo "  $NAME: DETECTED" && RESULTS+="$NAME: DETECTED"$'\n' && SCORE=$((SCORE+1))
    [ $S -eq 0 ] && echo "  $NAME: NOT DETECTED" && RESULTS+="$NAME: NOT DETECTED"$'\n'
done
echo ""; echo "============================================"
echo "$RESULTS"; echo "Score: $SCORE/$TOTAL, Invalid: $INVALID, Timeouts: $TIMEOUT_COUNT"
echo "============================================"
rm -rf "$TMPDIR"; [ "$SCORE" -eq "$TOTAL" ] && [ "$INVALID" -eq 0 ] && exit 0 || exit 1
