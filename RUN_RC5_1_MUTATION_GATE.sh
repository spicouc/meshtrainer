#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  RC5.1 R8 — REAL MUTATION GATE"
echo "============================================"
SRC="$(pwd)"
TMPDIR="/tmp/rc5_mutation_$$"
BASELINE_FAIL=0

echo ""
echo "=== BASELINE ==="
python3 -m py_compile rc5_tests.py r6_tests.py rc5_coordinator_ext.py rc5_split_server.py rc5_db.py rc5_receipt.py 2>&1
echo "  py_compile: PASS"

set +e
timeout 180 python3 rc5_tests.py > /dev/null 2>&1
STATUS1=$?
timeout 180 python3 r6_tests.py > /dev/null 2>&1
STATUS2=$?
set -e

if [ $STATUS1 -ne 0 ]; then echo "Baseline normal: FAIL"; BASELINE_FAIL=1; fi
if [ $STATUS2 -ne 0 ]; then echo "Baseline R6: FAIL"; BASELINE_FAIL=1; fi
if [ $BASELINE_FAIL -ne 0 ]; then
    echo "  Baseline: FAIL — mutation gate invalid"
    exit 1
fi
echo "  Baseline normal: PASS"
echo "  Baseline R6: PASS"
echo "  Baseline: PASS"

mkdir -p "$TMPDIR"
cp "$SRC"/*.py "$TMPDIR"/ 2>/dev/null || true

RESULTS=""
SCORE=0
INVALID=0
NOT_APPLIED=0
TIMEOUT_COUNT=0
TOTAL=5

for i in 1 2 3 4 5; do
    WORKDIR="$TMPDIR/mut_$i"
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    cp "$TMPDIR"/*.py "$WORKDIR"/ 2>/dev/null || true
    cd "$WORKDIR"

    case $i in
        1)
            NAME="MUT-01-zero-delta"
            sed -i 's/lora_delta = lora_after - lora_before/lora_delta = torch.zeros_like(lora_after)/' rc5_split_server.py
            ;;
        2)
            NAME="MUT-02-no-superseded"
            # Replace SUPERSEDED SQL with pass (syntactically valid in a function body)
            sed -i "s/SET status='SUPERSEDED'/SET status='RECEIVED' -- mutant: no SUPERSEDED/" rc5_coordinator_ext.py
            ;;
        3)
            NAME="MUT-03-no-sha-bytes"
            # Use if False guard, syntactically valid
            sed -i 's/if delta_sha_actual != delta_sha256:/if False and delta_sha_actual != delta_sha256:/' rc5_coordinator_ext.py
            sed -i 's/if delta_sha_actual != receipt.get("delta_sha256", "")/if False and delta_sha_actual != receipt.get("delta_sha256", "")/' rc5_coordinator_ext.py
            ;;
        4)
            NAME="MUT-04-transitions-open"
            sed -i 's/if new_state not in allowed:/if False:/' rc5_split_server.py
            ;;
        5)
            NAME="MUT-05-ownership-open"
            sed -i 's/if unit\["worker_id"\] != worker_id:/if False:/' rc5_split_server.py
            ;;
    esac

    # Validate mutation applied (exactly once)
    case $i in
        1) COUNT=$(grep -c "torch.zeros_like" rc5_split_server.py || true) ;;
        2) COUNT=$(grep -c "mutant: UPDATE" rc5_coordinator_ext.py || true) ;;
        3) COUNT=$(grep -c "if False and" rc5_coordinator_ext.py || true) ;;
        4) COUNT=$(grep -c "if False:" rc5_split_server.py || true) ;;
        5) COUNT=$(grep -c "if False:" rc5_split_server.py || true) ;;
    esac
    if [ "$COUNT" -eq 0 ]; then
        echo "  $NAME: NOT APPLIED (pattern not found)"
        RESULTS+="$NAME: NOT APPLIED"$'\n'
        NOT_APPLIED=$((NOT_APPLIED + 1))
        continue
    fi

    # Validate compilation
    set +e
    python3 -m py_compile *.py 2>"$NAME.py_compile.err"
    COMPILE_STATUS=$?
    set -e
    if [ "$COMPILE_STATUS" -ne 0 ]; then
        echo "  $NAME: INVALID (compile error)"
        RESULTS+="$NAME: INVALID"$'\n'
        INVALID=$((INVALID + 1))
        continue
    fi

    # Run tests
    set +e
    timeout 180 python3 rc5_tests.py > "mutation_$NAME.log" 2>&1
    STATUS_NORM=$?
    find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    timeout 180 python3 r6_tests.py >> "mutation_$NAME.log" 2>&1
    STATUS_R6=$?
    set -e

    if [ $STATUS_NORM -eq 124 ] || [ $STATUS_R6 -eq 124 ]; then
        echo "  $NAME: TIMEOUT"
        RESULTS+="$NAME: TIMEOUT"$'\n'
        TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
    elif [ $STATUS_NORM -ne 0 ] || [ $STATUS_R6 -ne 0 ]; then
        echo "  $NAME: DETECTED (normal=$STATUS_NORM, r6=$STATUS_R6)"
        RESULTS+="$NAME: DETECTED"$'\n'
        SCORE=$((SCORE + 1))
    else
        echo "  $NAME: NOT DETECTED"
        RESULTS+="$NAME: NOT DETECTED"$'\n'
    fi
done

echo ""
echo "============================================"
echo "$RESULTS"
echo "Mutation score: $SCORE/$TOTAL"
echo "Invalid mutants: $INVALID"
echo "Not applied: $NOT_APPLIED"
echo "Timeouts: $TIMEOUT_COUNT"
echo "============================================"

rm -rf "$TMPDIR"
[ "$SCORE" -eq "$TOTAL" ] && [ "$INVALID" -eq 0 ] && [ "$NOT_APPLIED" -eq 0 ] && exit 0 || exit 1
