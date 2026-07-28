#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  RC5.1 R9 — REAL MUTATION GATE"
echo "============================================"
SRC="$(pwd)"
TMPDIR="/tmp/rc5_mutation_$$"

echo ""
echo "=== BASELINE ==="
python3 -m py_compile rc5_tests.py r6_tests.py rc5_coordinator_ext.py rc5_split_server.py rc5_db.py rc5_receipt.py 2>&1
echo "  py_compile: PASS"

set +e
timeout 180 python3 rc5_tests.py > /dev/null 2>&1; S1=$?
timeout 180 python3 r6_tests.py > /dev/null 2>&1; S2=$?
set -e

if [ $S1 -ne 0 ] || [ $S2 -ne 0 ]; then
    echo "  Baseline normal: $([ $S1 -eq 0 ] && echo PASS || echo FAIL)"
    echo "  Baseline R6: $([ $S2 -eq 0 ] && echo PASS || echo FAIL)"
    echo "  Baseline: FAIL — mutation gate invalid"
    exit 1
fi
echo "  Baseline normal: PASS"
echo "  Baseline R6: PASS"
echo "  Baseline: PASS"

mkdir -p "$TMPDIR"
cp "$SRC"/*.py "$TMPDIR"/ 2>/dev/null || true

RESULTS=""
SCORE=0; INVALID=0; NOT_APPLIED=0; TIMEOUT_COUNT=0; TOTAL=5
RUNTIME_INVALID=0

for i in 1 2 3 4 5; do
    WORKDIR="$TMPDIR/mut_$i"
    rm -rf "$WORKDIR" && mkdir -p "$WORKDIR"
    cp "$TMPDIR"/*.py "$WORKDIR"/ 2>/dev/null || true
    cd "$WORKDIR"

    case $i in
        1) NAME="MUT-01-zero-delta"
           sed -i 's/lora_delta = lora_after - lora_before/lora_delta = torch.zeros_like(lora_after)/' rc5_split_server.py ;;
        2) NAME="MUT-02-no-superseded"
           # Python multiline replacement: turn the SUPERSEDED SQL block into "pass"
           python3 -c "
import re
with open('rc5_coordinator_ext.py') as f: c = f.read()
# Replace the entire SUPERSEDED block (conn.execute + args) with pass
old = r\"\"\"coord\\._rc5_db\\.conn\\.execute\\(\\\\s*\"UPDATE rc5_contribution SET status='SUPERSEDED'.*?\\)\"\"\"
c = re.sub(old, 'pass  # mutant: previous ACTIVE is not superseded', c, flags=re.DOTALL)
with open('rc5_coordinator_ext.py','w') as f: f.write(c)
" ;;
        3) NAME="MUT-03-no-sha-bytes"
           sed -i 's/if delta_sha_actual != delta_sha256:/if False and delta_sha_actual != delta_sha256:/' rc5_coordinator_ext.py
           sed -i 's/if delta_sha_actual != receipt.get("delta_sha256", "")/if False and delta_sha_actual != receipt.get("delta_sha256", "")/' rc5_coordinator_ext.py ;;
        4) NAME="MUT-04-transitions-open"
           sed -i 's/if new_state not in allowed:/if False:/' rc5_split_server.py ;;
        5) NAME="MUT-05-ownership-open"
           sed -i 's/if unit\["worker_id"\] != worker_id:/if False:/' rc5_split_server.py ;;
    esac

    # Check applied
    case $i in
        1) COUNT=$(grep -c "torch.zeros_like" rc5_split_server.py || true) ;;
        2) COUNT=$(grep -c "mutant: previous ACTIVE" rc5_coordinator_ext.py || true) ;;
        3) COUNT=$(grep -c "if False and" rc5_coordinator_ext.py || true) ;;
        4) COUNT=$(grep -c "if False:" rc5_split_server.py || true) ;;
        5) COUNT=$(grep -c "if False:" rc5_split_server.py || true) ;;
    esac
    [ "$COUNT" -eq 0 ] && { echo "  $NAME: NOT APPLIED"; RESULTS+="$NAME: NOT APPLIED"$'\n'; NOT_APPLIED=$((NOT_APPLIED+1)); continue; }

    # Compile check
    set +e; python3 -m py_compile *.py 2>"$NAME.compile.err"; COMPILE_STATUS=$?; set -e
    [ "$COMPILE_STATUS" -ne 0 ] && { echo "  $NAME: INVALID"; RESULTS+="$NAME: INVALID"$'\n'; INVALID=$((INVALID+1)); continue; }

    # Run tests
    set +e
    timeout 180 python3 rc5_tests.py > "mutation_$NAME.log" 2>&1; STATUS_NORM=$?
    find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    timeout 180 python3 r6_tests.py >> "mutation_$NAME.log" 2>&1; STATUS_R6=$?
    set -e

    # Check for runtime-invalid (crash vs detection)
    if [ $STATUS_NORM -eq 124 ] || [ $STATUS_R6 -eq 124 ]; then
        echo "  $NAME: TIMEOUT"; RESULTS+="$NAME: TIMEOUT"$'\n'; TIMEOUT_COUNT=$((TIMEOUT_COUNT+1))
    elif [ $STATUS_NORM -ne 0 ] || [ $STATUS_R6 -ne 0 ]; then
        # Check if failure was a crash (RemoteDisconnected, traceback) vs assertion fail
        CRASH=$(grep -c "RemoteDisconnected\|Traceback\|IndentationError\|SyntaxError" "mutation_$NAME.log" 2>/dev/null || true)
        if [ "$CRASH" -gt 0 ]; then
            echo "  $NAME: RUNTIME-INVALID (crash instead of assertion fail)"
            RESULTS+="$NAME: RUNTIME-INVALID"$'\n'; RUNTIME_INVALID=$((RUNTIME_INVALID+1))
        else
            echo "  $NAME: DETECTED (normal=$STATUS_NORM, r6=$STATUS_R6)"
            RESULTS+="$NAME: DETECTED"$'\n'; SCORE=$((SCORE+1))
        fi
    else
        echo "  $NAME: NOT DETECTED"; RESULTS+="$NAME: NOT DETECTED"$'\n'
    fi
done

echo ""; echo "============================================"
echo "$RESULTS"
echo "Mutation score: $SCORE/$TOTAL"
echo "Invalid mutants: $INVALID"
echo "Runtime-invalid mutants: $RUNTIME_INVALID"
echo "Not applied: $NOT_APPLIED"
echo "Timeouts: $TIMEOUT_COUNT"
echo "============================================"

rm -rf "$TMPDIR"
[ "$SCORE" -eq "$TOTAL" ] && [ "$INVALID" -eq 0 ] && [ "$RUNTIME_INVALID" -eq 0 ] && [ "$NOT_APPLIED" -eq 0 ] && exit 0 || exit 1
