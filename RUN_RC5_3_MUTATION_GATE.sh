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
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/ 2>/dev/null
cp -r "$SRC"/mutants_r53 "$TMPDIR"/ 2>/dev/null || true
SCORE=0; I=0; R=0; NA=0; T=0
for i in $(seq -w 1 12); do
    W="$TMPDIR/mut_$i"; rm -rf "$W"; mkdir -p "$W"
    cp "$TMPDIR"/*.py "$W"/ 2>/dev/null || true
    cp "$TMPDIR/mutants_r53"/*.py "$W"/ 2>/dev/null || true
    cd "$W"
    NAME="MUT-R3-$i"
    SCRIPT=$(ls mut_r3_${i}_*.py 2>/dev/null | head -1)
    if [ -z "$SCRIPT" ]; then echo "  $NAME: NOT APPLIED"; NA=$((NA+1)); continue; fi
    set +e; python3 "$SCRIPT" 2>/dev/null; OK=$?; set -e
    [ $OK -ne 0 ] && echo "  $NAME: NOT APPLIED" && NA=$((NA+1)) && continue
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ $C -ne 0 ] && echo "  $NAME: INVALID" && I=$((I+1)) && continue
    set +e; timeout 300 python3 rc5_3_tests.py > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ $S -eq 124 ] && echo "  $NAME: TIMEOUT" && T=$((T+1)) && continue
    TRACE=$(grep -c 'Traceback\|NameError\|AttributeError\|KeyError\|RuntimeError' "mutation_$NAME.log" 2>/dev/null || true)
    [ "$TRACE" -gt 0 ] && echo "  $NAME: RUNTIME-INVALID" && R=$((R+1)) && continue
    [ $S -ne 0 ] && echo "  $NAME: DETECTED" && SCORE=$((SCORE+1)) && continue
    echo "  $NAME: NOT DETECTED"
done
echo ""; echo "Score: $SCORE/12 Invalid: $I Runtime: $R Not_applied: $NA Timeouts: $T"
rm -rf "$TMPDIR"
[ $SCORE -eq 12 ] && [ $I -eq 0 ] && [ $R -eq 0 ] && [ $NA -eq 0 ]
