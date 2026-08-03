#!/usr/bin/env bash
set -euo pipefail
echo "============================================"
echo "  RC5.3 Stage B R5 — MUTATION GATE (12/12)"
echo "============================================"
SRC="$(pwd)"; BASE_TMP="${TMPDIR:-/tmp}"; TMPDIR="$BASE_TMP/r53_mut_r5_$$"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
REPORT="$LOG_DIR/RC5_3_MUTATION_GATE.log"
: > "$REPORT"
echo ""; echo "=== BASELINE ==="
python3 -m py_compile *.py && echo "  py_compile: PASS"
set +e; timeout 400 python3 rc5_3_tests.py > /dev/null 2>&1; S=$?; set -e
[ $S -ne 0 ] && echo "  Baseline: FAIL" && exit 1; echo "  Baseline: PASS"
mkdir -p "$TMPDIR"; cp "$SRC"/*.py "$TMPDIR"/ 2>/dev/null
cp -r "$SRC"/mutants_r53 "$TMPDIR"/ 2>/dev/null || true
SCORE=0; I=0; R=0; NA=0; T=0; ND=0
for i in $(seq -w 1 12); do
    # free disk: each mutant run creates ~1.5G of temp DBs (created inside
    # $TMPDIR because TMPDIR is exported); clean between mutants
    find "$TMPDIR" -maxdepth 1 -name "r53r_*.db" -delete 2>/dev/null || true
    find "$BASE_TMP" -maxdepth 1 -name "r53r_*.db" -delete 2>/dev/null || true
    W="$TMPDIR/mut_$i"; rm -rf "$W"; mkdir -p "$W"
    cp "$TMPDIR"/*.py "$W"/ 2>/dev/null || true
    cp "$TMPDIR/mutants_r53"/*.py "$W"/ 2>/dev/null || true
    cd "$W"
    NAME="MUT-R3-$i"
    SCRIPT=$(ls mut_r3_${i}_*.py 2>/dev/null | head -1)
    if [ -z "$SCRIPT" ]; then echo "  $NAME: NOT APPLIED (no script)" | tee -a "$REPORT"; NA=$((NA+1)); continue; fi
    # read PATTERN/SUBST from the mutant script (mandatory non-empty) via Python
    IFS=$'\n' read -r -d '' PATTERN SUBST < <(python3 - "$SCRIPT" <<'PY'
import ast, sys
src = open(sys.argv[1]).read()
tree = ast.parse(src)
p = s = ""
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant):
                if t.id == "PATTERN": p = node.value.value
                if t.id == "SUBST": s = node.value.value
print(p); print(s)
PY
) || true
    if [ -z "$PATTERN" ] || [ -z "$SUBST" ]; then
        echo "  $NAME: INVALID (empty pattern or substitution)" | tee -a "$REPORT"; I=$((I+1)); continue
    fi
    # applied_count = occurrences of SUBST in the file AFTER mutation
    set +e; python3 "$SCRIPT" 2>/dev/null; OK=$?; set -e
    [ $OK -ne 0 ] && echo "  $NAME: NOT APPLIED (script error)" | tee -a "$REPORT" && NA=$((NA+1)) && continue
    APPLIED=$(grep -cF "$SUBST" rc5_3_multiworker.py 2>/dev/null || echo 0)
    if [ "$APPLIED" -ne 1 ]; then
        echo "  $NAME: NOT APPLIED (applied_count=$APPLIED)" | tee -a "$REPORT"; NA=$((NA+1)); continue
    fi
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; C=$?; set -e
    [ $C -ne 0 ] && echo "  $NAME: INVALID (py_compile)" | tee -a "$REPORT" && I=$((I+1)) && continue
    set +e; timeout 400 python3 rc5_3_tests.py > "mutation_$NAME.log" 2>&1; S=$?; set -e
    [ $S -eq 124 ] && echo "  $NAME: TIMEOUT" | tee -a "$REPORT" && T=$((T+1)) && continue
    TRACE=$(grep -c 'Traceback\|NameError\|AttributeError\|KeyError\|RuntimeError' "mutation_$NAME.log" 2>/dev/null || true)
    ASSRT=$(grep -oP '  ❌ \K[^:]+' "mutation_$NAME.log" 2>/dev/null | head -1 || echo "")
    if [ "$TRACE" -gt 0 ]; then
        echo "  $NAME: RUNTIME-INVALID (tracebacks=$TRACE)" | tee -a "$REPORT"; R=$((R+1)); continue
    fi
    if [ $S -ne 0 ]; then
        echo "  $NAME: DETECTED" | tee -a "$REPORT"
        echo "      file: rc5_3_multiworker.py" >> "$REPORT"
        echo "      pattern: $PATTERN" >> "$REPORT"
        echo "      substitution: $SUBST" >> "$REPORT"
        echo "      applied_count: $APPLIED  assertion: $ASSRT  exit: $S  tracebacks: $TRACE" >> "$REPORT"
        SCORE=$((SCORE+1)); continue
    fi
    echo "  $NAME: NOT DETECTED (applied_count=$APPLIED)" | tee -a "$REPORT"; ND=$((ND+1))
done
echo ""; echo "Score: $SCORE/12 Invalid: $I Runtime: $R Not_applied: $NA Timeouts: $T" | tee -a "$REPORT"
echo "Not detected: $ND" | tee -a "$REPORT"
rm -rf "$TMPDIR"
[ $SCORE -eq 12 ] && [ $I -eq 0 ] && [ $R -eq 0 ] && [ $NA -eq 0 ] && [ $T -eq 0 ] && [ $ND -eq 0 ]
