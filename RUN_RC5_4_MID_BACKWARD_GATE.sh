#!/usr/bin/env bash
# RUN_RC5_4_MID_BACKWARD_GATE.sh — R9: mid-backward REAL
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
REPORT="$LOG_DIR/RC5_4_MID_BACKWARD_GATE.log"
: > "$REPORT"
TMP="${TMPDIR:-/tmp}/r54_midbwd_$$"; mkdir -p "$TMP"
DB="$TMP/midbwd.db"; OUT="$TMP/result.json"
SRC="$(pwd)"

echo "=== [1/3] execució mid-backward real ===" | tee -a "$REPORT"
(cd "$SRC" && timeout 600 "$PYTHON_BIN" rc5_4_mid_backward_real.py "$DB" "$OUT" > "$TMP/run.out" 2>&1)
RC=$?
echo "  exit: $RC" | tee -a "$REPORT"
[ $RC -eq 0 ] || { echo "  MID-BACKWARD FAIL"; tail -8 "$TMP/run.out" >> "$REPORT"; exit 1; }

echo "=== [2/3] resultats ===" | tee -a "$REPORT"
cat "$OUT" | tee -a "$REPORT"
python3 -c "
import json, sys
r = json.load(open('$OUT'))
checks = [
    ('lease A EXPIRED', r['lease_A_status'] == 'EXPIRED'),
    ('A no pot continuar', r['a_can_continue'] is False),
    ('A no pot aportar cap delta parcial', r['a_could_contribute'] is False),
    ('B adquireix nova revisió', r['B_acquired'] == 'ACTIVE'),
    ('B delta registrat', len(r['B_delta_sha']) == 16),
    ('cap delta parcial d\'A al journal', r['no_partial_A_in_journal'] is True),
]
ok = all(c[1] for c in checks)
for name, passed in checks:
    print(('  PASS ' if passed else '  FAIL ') + name)
sys.exit(0 if ok else 1)
" | tee -a "$REPORT"
RC2=${PIPESTATUS[0]}
[ $RC2 -eq 0 ] || { echo "  MID-BACKWARD FAIL (checks)"; exit 1; }

echo "MID-BACKWARD GATE: PASS" | tee -a "$REPORT"
rm -rf "$TMP"
exit 0
