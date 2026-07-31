#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.3 Stage B — MUTATION GATE (12/12)"
echo "============================================"
SRC="$(pwd)"; TMPDIR="/tmp/r53_mut_$$"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile *.py && echo "  py_compile: PASS"
set +e; timeout 300 python3 rc5_3_tests.py > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/
SCORE=0; I=0; R=0; NA=0; T=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    W="$TMPDIR/mut_$i"; rm -rf "$W"; mkdir -p "$W"; cp "$TMPDIR"/*.py "$W"/; cd "$W"
    OK=0; NAME=""
    case $i in
        1) NAME="MUT-R3-01-superseded"; sed -i "s/status='ACTIVE' AND run_id/status IN ('ACTIVE','SUPERSEDED') AND run_id/" rc5_3_multiworker.py; grep -q "SUPERSEDED') AND run_id" rc5_3_multiworker.py || OK=1 ;;
        2) NAME="MUT-R3-02-ignore-ett"; sed -i "s/r\[\"ett\"\] \/ total_ett/1.0 \/ len(rows)/" rc5_3_multiworker.py; grep -q "1.0 / len(rows)" rc5_3_multiworker.py || OK=1 ;;
        3) NAME="MUT-R3-03-omit-active"; sed -i "s/status='ACTIVE' AND run_id/status='VALIDATED' AND run_id/" rc5_3_multiworker.py; grep -q "VALIDATED' AND run_id" rc5_3_multiworker.py || OK=1 ;;
        4) NAME="MUT-R3-04-apply-twice"; sed -i "s/post_A, post_B = pre_A + dA, pre_B + dB/post_A, post_B = pre_A + dA + dA, pre_B + dB + dB/" rc5_3_multiworker.py; grep -q "dA + dA" rc5_3_multiworker.py || OK=1 ;;
        5) NAME="MUT-R3-05-stale-adapter"; sed -i "s/raise ValueError(\"Stale base adapter/if False: pass  # stale-ok\n                    raise ValueError(\"Stale base adapter/" rc5_3_multiworker.py; grep -q "stale-ok" rc5_3_multiworker.py || OK=1 ;;
        6) NAME="MUT-R3-06-ignore-model-hash"; sed -i "s/if p.get(hk, \"\") != a\[hk\]:/if hk != \"worker_model_hash\" and p.get(hk, \"\") != a[hk]:/" rc5_3_multiworker.py; grep -q "hk != \"worker_model_hash\"" rc5_3_multiworker.py || OK=1 ;;
        7) NAME="MUT-R3-07-ignore-budget"; sed -i "s/if strict_budget and hashes.get(\"max_micro_batch\", mb) > mb:/if False: pass  # ignore-budget/" rc5_3_multiworker.py; grep -q "ignore-budget" rc5_3_multiworker.py || OK=1 ;;
        8) NAME="MUT-R3-08-round2-old-adapter"; sed -i "s/h2 = dict(HASHES); h2\[\"base_adapter_hash\"\] = closed\[\"adapter_hash\"\]/h2 = dict(HASHES)/" rc5_3_tests.py; grep -q "h2 = dict(HASHES)$" rc5_3_tests.py || OK=1 ;;
        9) NAME="MUT-R3-09-cross-worker"; sed -i "s/AND assignment_id=? AND worker_id=? AND status='ACTIVE' AND cid!=?/AND status='ACTIVE' AND cid!=?/" rc5_3_multiworker.py; grep -q "AND status='ACTIVE' AND cid!=?" rc5_3_multiworker.py || OK=1 ;;
        10) NAME="MUT-R3-10-close-early"; sed -i "s/if active < mandatory:/if False: pass  # close-early/" rc5_3_multiworker.py; grep -q "close-early" rc5_3_multiworker.py || OK=1 ;;
        11) NAME="MUT-R3-11-order-dep"; sed -i "s/avg_A += w \* tensors/avg_A += w * 0.5 * tensors/; s/avg_B += w \* tensors/avg_B += w * 0.5 * tensors/" rc5_3_multiworker.py; grep -q "0.5 \* tensors" rc5_3_multiworker.py || OK=1 ;;
        12) NAME="MUT-R3-12-dup-assignment"; sed -i "s/INSERT OR REPLACE INTO assignments/INSERT INTO assignments/" rc5_3_multiworker.py; grep -q "INSERT INTO assignments" rc5_3_multiworker.py || OK=1 ;;
    esac
    [ "$OK" -ne 0 ] && echo "  $NAME: NOT APPLIED" && NA=$((NA+1)) && continue
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ "$C" -ne 0 ] && echo "  $NAME: INVALID" && I=$((I+1)) && continue
    set +e; timeout 300 python3 rc5_3_tests.py > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ "$S" -eq 124 ] && echo "  $NAME: TIMEOUT" && T=$((T+1)) && continue
    TRACE=$(grep -c 'Traceback\|NameError\|AttributeError\|KeyError\|RuntimeError' "mutation_$NAME.log" 2>/dev/null || true)
    if [ "$TRACE" -gt 0 ]; then echo "  $NAME: RUNTIME-INVALID" && R=$((R+1)) && continue; fi
    [ "$S" -ne 0 ] && echo "  $NAME: DETECTED" && SCORE=$((SCORE+1)) && continue
    echo "  $NAME: NOT DETECTED"
done
echo ""; echo "Score: $SCORE/12 Invalid: $I Runtime: $R Not_applied: $NA Timeouts: $T"
rm -rf "$TMPDIR"
[ "$SCORE" -eq 12 ] && [ "$I" -eq 0 ] && [ "$R" -eq 0 ] && [ "$NA" -eq 0 ]
