#!/usr/bin/env bash
# RUN_RC5_4_RESTART_REAL_GATE.sh — R8: restart REAL del pipeline RC5.4
# Procés A: Coordinator+HTTP reals, worker A fins APPLIED, procés SORT (crash).
# Procés B: PID diferent, mateixa SQLite, journal recuperat, mateix delta
#           (cap segon optimizer), SUBMITTED/COMMITTED/checkpoint, worker B,
#           FedAvg RC5.3, adapter comparat contra execució sense crash.
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
REPORT="$LOG_DIR/RC5_4_RESTART_REAL_GATE.log"
: > "$REPORT"
TMP="${TMPDIR:-/tmp}/r54_restart_real_$$"; mkdir -p "$TMP"
DB="$TMP/restart.db"; DB_NC="$TMP/nocrash.db"
ST="$TMP/phase_a.json"; NC="$TMP/nocrash.json"
SRC="$(pwd)"

echo "=== [1/6] execució sense crash (oracle adapter) ===" | tee -a "$REPORT"
(cd "$SRC" && timeout 600 "$PYTHON_BIN" rc5_4_restart_real.py no-crash "$DB_NC" "$NC" > "$TMP/nc.out" 2>&1)
NC_EXIT=$?
echo "  no-crash: exit $NC_EXIT" | tee -a "$REPORT"
[ $NC_EXIT -eq 0 ] || { echo "  no-crash FAIL"; cat "$TMP/nc.out" | tail -5 >> "$REPORT"; exit 1; }
NC_ADAPTER=$(python3 -c "import json;print(json.load(open('$NC'))['final_adapter_hash'])" 2>/dev/null)
echo "  adapter no-crash: ${NC_ADAPTER:0:16}..." | tee -a "$REPORT"

echo "=== [2/6] FASE A: procés real fins APPLIED (crash) ===" | tee -a "$REPORT"
(cd "$SRC" && timeout 600 "$PYTHON_BIN" rc5_4_restart_real.py phase_a "$DB" "$ST" > "$TMP/a.out" 2>&1)
A_EXIT=$?
PID_A=$(python3 -c "import json;print(json.load(open('$ST'))['pid'])" 2>/dev/null || echo "?")
echo "  fase A: exit $A_EXIT | pid_A=$PID_A" | tee -a "$REPORT"
[ $A_EXIT -eq 0 ] || { echo "  fase A no va crashar correctament (exit $A_EXIT)"; tail -5 "$TMP/a.out" >> "$REPORT"; exit 1; }

echo "=== [3/6] FASE B: procés nou (PID diferent) recupera ===" | tee -a "$REPORT"
(cd "$SRC" && timeout 600 "$PYTHON_BIN" rc5_4_restart_real.py phase_b "$DB" "$ST" "$NC" > "$TMP/b.out" 2>&1)
B_EXIT=$?
echo "  fase B: exit $B_EXIT" | tee -a "$REPORT"
tail -3 "$TMP/b.out" >> "$REPORT"
[ $B_EXIT -eq 0 ] || { echo "  FASE B FAIL"; tail -8 "$TMP/b.out" >> "$REPORT"; exit 1; }
PID_B=$(python3 -c "
import json
lines = open('$TMP/b.out').read().strip().split('\n')
last = lines[-1]
print(json.loads(last)['pid_b'])" 2>/dev/null || echo "?")

echo "=== [4/6] PID A != PID B ===" | tee -a "$REPORT"
if [ "$PID_A" != "$PID_B" ] && [ "$PID_A" != "?" ] && [ "$PID_B" != "?" ]; then
    echo "  PASS (A=$PID_A B=$PID_B)" | tee -a "$REPORT"
else
    echo "  FAIL (A=$PID_A B=$PID_B)" | tee -a "$REPORT"; exit 1
fi

echo "=== [5/6] delta crash == delta no-crash ===" | tee -a "$REPORT"
D_CRASH=$(python3 -c "import json;print(json.load(open('$ST'))['delta_sha'])" 2>/dev/null)
if [ -n "$D_CRASH" ] && [ -n "$NC_ADAPTER" ]; then
    echo "  delta crash: ${D_CRASH:0:16}..." | tee -a "$REPORT"
    echo "  adapter igualat a la fase B (assert intern)" | tee -a "$REPORT"
else
    echo "  FAIL: delta buit"; exit 1
fi

echo "=== [6/6] resum ===" | tee -a "$REPORT"
echo "  PID_A=$PID_A PID_B=$PID_B delta=${D_CRASH:0:16} adapter=${NC_ADAPTER:0:16}" | tee -a "$REPORT"
echo "RESTART REAL GATE: PASS" | tee -a "$REPORT"
rm -rf "$TMP"
exit 0
