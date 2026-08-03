#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 2 R12 — MUTATION GATE (12/12)"
echo "============================================"
SRC="$(pwd)"; BASE_TMP="${TMPDIR:-/tmp}"; TMPDIR="$BASE_TMP/p2_mut_r12_$$"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile *.py && echo "  py_compile: PASS"
set +e; timeout 300 python3 -c "from rc5_2_tensor_bundle import *; import torch; delta_bundle_pack(torch.randn(8,256), torch.randn(1024,8)); print('OK')" > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/
SCORE=0; I=0; R=0; NA=0; T=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    # free disk: each mutant run creates temp DBs; clean between mutants
    find "$BASE_TMP" -maxdepth 1 -name "r9_*.db" -delete 2>/dev/null || true
    W="$TMPDIR/mut_$i"; rm -rf "$W"; mkdir -p "$W"; cp "$TMPDIR"/*.py "$W"/; cd "$W"
    OK=0; NAME=""
    case $i in
        1) NAME="MUT-P2-01"; sed -i 's/cut_gradient.detach().clone()/torch.randn_like(cut_gradient)/' rc5_2_http_server.py; grep -q "randn_like(cut_gradient)" rc5_2_http_server.py || OK=1 ;;
        2) NAME="MUT-P2-02"; sed -i 's/labels = tokens.clone()/labels = torch.full_like(tokens, -100)/' rc5_2_http_server.py; grep -q "full_like" rc5_2_http_server.py || OK=1 ;;
        3) NAME="MUT-P2-03"; sed -i 's/self.worker.worker_forward(activation, mask)/activation.detach().requires_grad_(True)/' rc5_2_worker_runtime.py; grep -q "requires_grad_(True)" rc5_2_worker_runtime.py || OK=1 ;;
        4) NAME="MUT-P2-04"; sed -i 's/self.lora_wrapper.lora_B.weight\]/self.lora_wrapper.lora_B.weight, self.local_layers\[0\].linear2.weight\]/g' rc5_2_numerical_models.py; grep -q "linear2.weight" rc5_2_numerical_models.py || OK=1 ;;
        5) NAME="MUT-P2-05"; sed -i 's/ctx\["cut_gradient"\] = cut_gradient.detach().clone()/ctx["cut_gradient"] = cut_gradient.detach().clone(); ctx["cut_gradient"][0,0,0] = ctx["cut_gradient"][0,0,0] + 0.1/' rc5_2_http_server.py; grep -q "0,0,0\] + 0.1" rc5_2_http_server.py || OK=1 ;;
        6) NAME="MUT-P2-06"; sed -i 's/lora_out = self.lora_B(self.lora_A(x)) \* self.scaling/lora_out = self.lora_B(torch.relu(self.lora_A(x))) * self.scaling/' rc5_2_lora.py; grep -q "torch.relu" rc5_2_lora.py || OK=1 ;;
        7) NAME="MUT-P2-07"; sed -i 's/if sha != receipt\["delta_bundle_sha256"\]:/if False and sha != receipt["delta_bundle_sha256"]:  # mut07 accept any sha/' rc5_2_coordinator.py; grep -q "mut07 accept any sha" rc5_2_coordinator.py || OK=1 ;;
        8) NAME="MUT-P2-08"; sed -i 's/if r2: raise ValueError/if False: pass  # accept diff/' rc5_2_http_server.py; grep -q "accept diff" rc5_2_http_server.py || OK=1 ;;
        9) NAME="MUT-P2-09"; sed -i 's/s in TERMINAL/False and s in TERMINAL/' rc5_2_http_server.py; grep -q "False and s in TERMINAL" rc5_2_http_server.py || OK=1 ;;
        10) NAME="MUT-P2-10"; sed -i 's/self._consume_nonce(receipt\["receipt_nonce"\], cid)/print("PREMATURE_NONCE")/' rc5_2_coordinator.py; grep -q "PREMATURE_NONCE" rc5_2_coordinator.py || OK=1 ;;
        11) NAME="MUT-P2-11"; sed -i 's/if receipt.get("expires_at", 0) < time.time():/if False:  # accept expired/' rc5_2_coordinator.py; grep -q "accept expired" rc5_2_coordinator.py || OK=1 ;;
        12) NAME="MUT-P2-12"; sed -i 's/if p\["numerical_profile_hash"\] != numerical_profile_hash():/if False:  # skip profile hash/' rc5_2_http_server.py; grep -q "skip profile hash" rc5_2_http_server.py || OK=1 ;;
    esac
    [ "$OK" -ne 0 ] && echo "  $NAME: NOT APPLIED" && NA=$((NA+1)) && continue
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ "$C" -ne 0 ] && echo "  $NAME: INVALID" && I=$((I+1)) && continue
    set +e; timeout 300 python3 rc5_2_phase2_tests.py > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ "$S" -eq 124 ] && echo "  $NAME: TIMEOUT" && T=$((T+1)) && continue
    # R12: ANY traceback -> RUNTIME-INVALID (only clean assertion failures count as DETECTED)
    TRACE=$(grep -c 'Traceback\|NameError\|AttributeError\|KeyError\|RuntimeError' "mutation_$NAME.log" 2>/dev/null || true)
    if [ "$TRACE" -gt 0 ]; then echo "  $NAME: RUNTIME-INVALID" && R=$((R+1)) && continue; fi
    [ "$S" -ne 0 ] && echo "  $NAME: DETECTED" && SCORE=$((SCORE+1)) && continue
    echo "  $NAME: NOT DETECTED"
done
echo ""; echo "Score: $SCORE/12 Invalid: $I Runtime: $R Not_applied: $NA Timeouts: $T"
rm -rf "$TMPDIR"
[ "$SCORE" -eq 12 ] && [ "$I" -eq 0 ] && [ "$R" -eq 0 ] && [ "$NA" -eq 0 ] && [ "$T" -eq 0 ]
