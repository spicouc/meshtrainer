#!/usr/bin/env bash
# Controlled-failure RC5.2 (ordre R6): substitueix EXACTAMENT la condició de
# J4 per False, verifica applied_count=1 + py_compile + 1 assertion falla +
# cap traceback + exit no-zero. Original intacte.
set -uo pipefail
SRC="$(pwd)"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
echo "=== CONTROLLED-FAILURE RC5.2 ==="
echo "LOG_DIR: $LOG_DIR"

CFD="$LOG_DIR/cf_r52_$$"
rm -rf "$CFD"; mkdir -p "$CFD"
cp -r "$SRC"/. "$CFD"/
cd "$CFD"

# substitució EXACTA (sense sed)
python3 - <<'PY'
src = open("rc5_2_phase2_tests.py").read()
old = 'check("J4: open returns state", r["state"] == "SERVER_ACTIVATION_READY")'
new = 'check("J4: open returns state", False)'
n = src.count(old)
assert n == 1, f"esperat 1, trobat {n}"
src2 = src.replace(old, new, 1)
open("rc5_2_phase2_tests.py", "w").write(src2)
print(f"Substitucions aplicades: {n}")
PY
APP=$(grep -c 'check("J4: open returns state", False)' rc5_2_phase2_tests.py)
echo "applied_count: $APP (ha de ser 1)"
[ "$APP" -ne 1 ] && echo "CF-R52: FAIL (applied_count=$APP)" && exit 1

DIFFLINES=$(diff "$SRC/rc5_2_phase2_tests.py" "$CFD/rc5_2_phase2_tests.py" | grep -c '^[<>]')
echo "línies que difereixen: $DIFFLINES (ha de ser 2)"
[ "$DIFFLINES" -ne 2 ] && echo "CF-R52: FAIL (diff=$DIFFLINES)" && exit 1

python3 -m py_compile rc5_2_phase2_tests.py 2>"$LOG_DIR/cf_r52_pycompile.err"
C=$?
echo "py_compile exit: $C (ha de ser 0)"
[ $C -ne 0 ] && echo "CF-R52: FAIL (py_compile)" && exit 1
[ -s "$LOG_DIR/cf_r52_pycompile.err" ] && echo "CF-R52: FAIL (SyntaxError)" && exit 1

export PATH=/root/meshtrainer/.venv/bin:$PATH TMPDIR=/root/tmp_r53
timeout 300 python3 rc5_2_phase2_tests.py > "$LOG_DIR/RC5_2_CONTROLLED_FAILURE.log" 2>&1
S=$?
echo "suite exit: $S (ha de ser no-zero)"
[ $S -eq 0 ] && echo "CF-R52: FAIL (suite exit 0)" && exit 1

FAILS=$(grep -c '  ❌' "$LOG_DIR/RC5_2_CONTROLLED_FAILURE.log")
TRACE=$(grep -c 'Traceback\|SyntaxError' "$LOG_DIR/RC5_2_CONTROLLED_FAILURE.log")
J4FAIL=$(grep -c '❌ J4: open returns state' "$LOG_DIR/RC5_2_CONTROLLED_FAILURE.log")
RES=$(grep -oP 'Resultat: \K[0-9]+/[0-9]+ PASS, [0-9]+ FAIL' "$LOG_DIR/RC5_2_CONTROLLED_FAILURE.log" | head -1)
echo "FAILs: $FAILS | J4: $J4FAIL | tracebacks: $TRACE | $RES"
[ "$FAILS" -ne 1 ] && echo "CF-R52: FAIL (FAILs=$FAILS)" && exit 1
[ "$J4FAIL" -ne 1 ] && echo "CF-R52: FAIL (J4 no és la fallada)" && exit 1
[ "$TRACE" -ne 0 ] && echo "CF-R52: FAIL (tracebacks=$TRACE)" && exit 1

cd "$SRC"
git diff --quiet || { echo "CF-R52: FAIL (fitxers tracked modificats)"; exit 1; }
NOK=$(sha256sum -c MANIFEST.sha256 2>&1 | grep -cv ": OK")
[ "$NOK" -eq 0 ] || { echo "CF-R52: FAIL (manifest trencat, $NOK no-OK)"; exit 1; }

echo "controlled-failure RC5.2: PASS"
echo "  assertion detectora: J4"
echo "  resultat: $RES"
