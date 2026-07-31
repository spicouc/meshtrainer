#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.2 Phase 2 R3 — MUTATION GATE (12/12)"
echo "============================================"
SRC="$(pwd)"; TMPDIR="/tmp/p2_mut_final_$$"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile *.py && echo "  py_compile: PASS"
set +e; timeout 300 python3 -c "from rc5_2_tensor_bundle import *; import torch; delta_bundle_pack(torch.randn(8,256), torch.randn(1024,8)); print('OK')" > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/
SCORE=0; I=0; R=0; NA=0; T=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    W="$TMPDIR/mut_$i"; rm -rf "$W"; mkdir -p "$W"; cp "$TMPDIR"/*.py "$W"/; cd "$W"
    OK=0; NAME=""
    case $i in
        1) NAME="MUT-P2-01"; sed -i 's/backward_fetch/FAKE_BW/g' rc5_2_http_server.py; grep -q FAKE_BW rc5_2_http_server.py || OK=1 ;;
        2) NAME="MUT-P2-02"; sed -i 's/Invalid receipt HMAC/INVALID receipt HMAC PASS/g' rc5_2_coordinator.py; grep -q "PASS" rc5_2_coordinator.py || OK=1 ;;
        3) NAME="MUT-P2-03"; sed -i 's|result\["cut_gradient"\]|result.get("dummy_gradient",0)|g' rc5_2_split_server.py; grep -q dummy_gradient rc5_2_split_server.py || OK=1 ;;
        4) NAME="MUT-P2-04"; sed -i 's|lora_B.weight\]|lora_B.weight, self.local_layers\[0\].linear2.weight\]|g' rc5_2_numerical_models.py; grep -q "linear2.weight" rc5_2_numerical_models.py || OK=1 ;;
        5) NAME="MUT-P2-05"; sed -i 's/cut_gradient/tampered_gradient/g' rc5_2_split_server.py; grep -q tampered_gradient rc5_2_split_server.py || OK=1 ;;
        6) NAME="MUT-P2-06"; sed -i 's/lora_out = self.lora_B(self.lora_A(x)) \* self.scaling/lora_out = self.lora_B(torch.relu(self.lora_A(x))) * self.scaling/' rc5_2_lora.py; grep -q "torch.relu" rc5_2_lora.py || OK=1 ;;
        7) NAME="MUT-P2-07"; sed -i 's/sha != receipt/True or sha != receipt/' rc5_2_coordinator.py; grep -q "True or sha" rc5_2_coordinator.py || OK=1 ;;
        8) NAME="MUT-P2-08"; sed -i 's/raise ValueError/DELETEME/' rc5_2_http_server.py; grep -q DELETEME rc5_2_http_server.py || OK=1 ;;
        9) NAME="MUT-P2-09"; sed -i 's/state in TERMINAL/False and state in TERMINAL/' rc5_2_http_server.py; grep -q "False and state" rc5_2_http_server.py || OK=1 ;;
        10) NAME="MUT-P2-10"; sed -i 's/self._consume_nonce(nonce_id, cid)/DELETEME/' rc5_2_coordinator.py; grep -q DELETEME rc5_2_coordinator.py || OK=1 ;;
        11) NAME="MUT-P2-11"; sed -i 's/expires_at/expired_dummy/g' rc5_2_coordinator.py; grep -q expired_dummy rc5_2_coordinator.py || OK=1 ;;
        12) NAME="MUT-P2-12"; sed -i 's/numerical_profile_hash()/MUT_FAKE_HASH/' rc5_2_http_server.py; grep -q MUT_FAKE_HASH rc5_2_http_server.py || OK=1 ;;
    esac
    [ "$OK" -ne 0 ] && echo "  $NAME: NOT APPLIED" && NA=$((NA+1)) && continue
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ "$C" -ne 0 ] && echo "  $NAME: INVALID" && I=$((I+1)) && continue
    set +e; timeout 300 python3 rc5_2_phase2_tests.py > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ "$S" -eq 124 ] && echo "  $NAME: TIMEOUT" && T=$((T+1)) && continue
    TRACE=$(grep -c 'Traceback\|Error:\|Exception:' "mutation_$NAME.log" 2>/dev/null || true)
    ASSRT=$(grep -c '❌' "mutation_$NAME.log" 2>/dev/null || true)
    if [ "$TRACE" -gt 0 ] && [ "$ASSRT" -eq 0 ]; then echo "  $NAME: RUNTIME-INVALID" && R=$((R+1)) && continue; fi
    [ "$S" -ne 0 ] && echo "  $NAME: DETECTED" && SCORE=$((SCORE+1)) && continue
    echo "  $NAME: NOT DETECTED"
done
echo ""; echo "Score: $SCORE/12 Invalid: $I Runtime: $R Not_applied: $NA Timeouts: $T"
rm -rf "$TMPDIR"
[ "$SCORE" -eq 12 ] && [ "$I" -eq 0 ] && [ "$R" -eq 0 ] && [ "$NA" -eq 0 ]
