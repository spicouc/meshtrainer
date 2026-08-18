#!/usr/bin/env bash
# Controlled-failure RC5.3 (ordre R6): substitueix EXACTAMENT la condició de
# W1 per False, verifica 1 substitució + py_compile + cap traceback, executa la
# suite i exigeix 104/105 amb W1 com a única fallada. Original intacte.
set -uo pipefail
SRC="$(pwd)"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
echo "=== CONTROLLED-FAILURE RC5.3 ==="
echo "LOG_DIR: $LOG_DIR"

# 1. còpia temporal
CFD="$LOG_DIR/cf_r53_$$"
rm -rf "$CFD"; mkdir -p "$CFD"
cp -r "$SRC"/. "$CFD"/
cd "$CFD"

# 2. substitució EXACTA (sense sed): patró literal únic
"$PYTHON_BIN" - <<'PY'
src = open("rc5_3_tests.py").read()
old = 'check("W1: two workers completed", ra["worker_id"] == "wa" and rb["worker_id"] == "wb")'
new = 'check("W1: two workers completed", False)'
n = src.count(old)
assert n == 1, f"esperat 1, trobat {n}"
src2 = src.replace(old, new, 1)
open("rc5_3_tests.py", "w").write(src2)
print(f"Substitucions aplicades: {n}")
PY
APP=$(grep -c 'check("W1: two workers completed", False)' rc5_3_tests.py)
echo "applied_count: $APP (ha de ser 1)"
[ "$APP" -ne 1 ] && echo "CF-R53: FAIL (applied_count=$APP)" && exit 1

# 3. exactament una línia funcional modificada
DIFFLINES=$(diff "$SRC/rc5_3_tests.py" "$CFD/rc5_3_tests.py" | grep -c '^[<>]')
echo "línies que difereixen: $DIFFLINES (ha de ser 2)"
[ "$DIFFLINES" -ne 2 ] && echo "CF-R53: FAIL (diff=$DIFFLINES)" && exit 1

# 4. py_compile PASS + cap SyntaxError
"$PYTHON_BIN" -m py_compile rc5_3_tests.py 2>"$LOG_DIR/cf_r53_pycompile.err"
C=$?
echo "py_compile exit: $C (ha de ser 0)"
[ $C -ne 0 ] && echo "CF-R53: FAIL (py_compile)" && exit 1
[ -s "$LOG_DIR/cf_r53_pycompile.err" ] && echo "CF-R53: FAIL (SyntaxError)" && exit 1

# 5. executar la suite
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export TMPDIR="${TMPDIR:-/tmp}"
PYBIN_DIR="$(dirname "$PYTHON_BIN")"
if [ "$PYBIN_DIR" != "." ] && [ -x "$PYTHON_BIN" ]; then
    export PATH="$PYBIN_DIR:$PATH"
fi
timeout 590 "$PYTHON_BIN" rc5_3_tests.py > "$LOG_DIR/RC5_3_CONTROLLED_FAILURE.log" 2>&1
S=$?
echo "suite exit: $S (ha de ser no-zero)"
[ $S -eq 0 ] && echo "CF-R53: FAIL (suite exit 0)" && exit 1

# 6. exactament 1 FAIL, l'assertion W1, cap traceback
FAILS=$(grep -c '  ❌' "$LOG_DIR/RC5_3_CONTROLLED_FAILURE.log")
TRACE=$(grep -c 'Traceback\|SyntaxError' "$LOG_DIR/RC5_3_CONTROLLED_FAILURE.log")
W1FAIL=$(grep -c '❌ W1: two workers completed' "$LOG_DIR/RC5_3_CONTROLLED_FAILURE.log")
RES=$(grep -oP 'Resultat: \K[0-9]+/[0-9]+ PASS, [0-9]+ FAIL' "$LOG_DIR/RC5_3_CONTROLLED_FAILURE.log" | head -1)
echo "FAILs: $FAILS | W1: $W1FAIL | tracebacks: $TRACE | $RES"
[ "$FAILS" -ne 1 ] && echo "CF-R53: FAIL (FAILs=$FAILS)" && exit 1
[ "$W1FAIL" -ne 1 ] && echo "CF-R53: FAIL (W1 no és la fallada)" && exit 1
[ "$TRACE" -ne 0 ] && echo "CF-R53: FAIL (tracebacks=$TRACE)" && exit 1
echo "$RES" | grep -q "104/105 PASS, 1 FAIL" || { echo "CF-R53: FAIL (resultat inesperat)"; exit 1; }

# 7. paquet original intacte: cap fitxer auditat (manifest) modificat.
#    (funciona tant dins un repo git com en un directori extret sense .git)
cd "$SRC"
NOK=$(sha256sum -c MANIFEST.sha256 2>&1 | grep -cv ": OK")
[ "$NOK" -eq 0 ] || { echo "CF-R53: FAIL (manifest trencat, $NOK no-OK)"; exit 1; }

echo "controlled-failure RC5.3: PASS"
echo "  assertion detectora: W1"
echo "  resultat: $RES"
