#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  RC5.1 R7 — REAL MUTATION GATE"
echo "============================================"
SRC="$(pwd)"
TMPDIR="/tmp/rc5_mutation_$$"
mkdir -p "$TMPDIR"

MUTATIONS=(
    "MUT-01-zero-delta"
    "MUT-02-no-superseded"
    "MUT-03-no-sha-bytes"
    "MUT-04-transitions-open"
    "MUT-05-ownership-open"
)

RESULTS=""
SCORE=0
TOTAL=5

echo "Copying source files..."
for MUT in "${MUTATIONS[@]}"; do
    WORKDIR="$TMPDIR/$MUT"
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    cp "$SRC"/*.py "$WORKDIR"/ 2>/dev/null || true
done

for MUT in "${MUTATIONS[@]}"; do
    echo ""
    echo "--- $MUT ---"
    WORKDIR="$TMPDIR/$MUT"
    cd "$WORKDIR"

    case "$MUT" in
        MUT-01-zero-delta)
            sed -i 's/lora_delta = lora_after - lora_before/lora_delta = torch.zeros_like(lora_after)/' rc5_split_server.py
            ;;
        MUT-02-no-superseded)
            sed -i '/SUPERSEDED.*WHERE.*ACTIVE/d' rc5_coordinator_ext.py
            ;;
        MUT-03-no-sha-bytes)
            sed -i '/if delta_sha_actual != delta_sha256/d' rc5_coordinator_ext.py
            sed -i '/if delta_sha_actual != receipt.get.*delta_sha256/d' rc5_coordinator_ext.py
            ;;
        MUT-04-transitions-open)
            # Replace the validate_transition body to always return True
            sed -i 's/if new_state not in allowed:/if False:/' rc5_split_server.py
            ;;
        MUT-05-ownership-open)
            sed -i 's/if unit\["worker_id"\] != worker_id:/if False:/' rc5_split_server.py
            ;;
    esac

    echo "  Running normal tests..."
    find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    set +e
    timeout 180 python3 rc5_tests.py > "mutation_${MUT}.log" 2>&1
    STATUS_NORM=$?
    set -e
    # Run R6 tests which detect MUT-01 (zero delta) and MUT-04 (transitions)
    find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    set +e
    timeout 180 python3 r6_tests.py >> "mutation_${MUT}.log" 2>&1
    STATUS_R6=$?
    set -e

    # A mutation is DETECTED if EITHER suite fails
    if [ "$STATUS_NORM" -eq 0 ] && [ "$STATUS_R6" -eq 0 ]; then
        echo "  ❌ $MUT: NOT DETECTED"
        RESULTS+="$MUT: NOT DETECTED"$'\n'
    elif [ "$STATUS_NORM" -eq 124 ] || [ "$STATUS_R6" -eq 124 ]; then
        echo "  ❌ $MUT: TIMEOUT"
        RESULTS+="$MUT: TIMEOUT"$'\n'
    else
        echo "  ✅ $MUT: DETECTED (normal=$STATUS_NORM, r6=$STATUS_R6)"
        RESULTS+="$MUT: DETECTED"$'\n'
        SCORE=$((SCORE + 1))
    fi
done

echo ""
echo "============================================"
echo "$RESULTS"
echo "Mutation score: $SCORE/$TOTAL"
echo "============================================"

rm -rf "$TMPDIR"
[ "$SCORE" -eq "$TOTAL" ] && exit 0 || exit 1
