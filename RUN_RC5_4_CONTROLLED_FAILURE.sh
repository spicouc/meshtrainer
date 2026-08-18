#!/usr/bin/env bash
# RUN_RC5_4_CONTROLLED_FAILURE.sh — CF RC5.4: còpia temporal de rc5_4_tests.py
# amb EXACTAMENT una assertion modificada -> una sola prova FAIL, 0 tracebacks,
# gate Overall FAIL, exit no-zero, original intacte.
set -uo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN
PYBIN_DIR="$(dirname "$PYTHON_BIN")"
if [ "$PYBIN_DIR" != "." ] && [ -x "$PYTHON_BIN" ]; then
    export PATH="$PYBIN_DIR:$PATH"
fi
TMPDIR="${TMPDIR:-/tmp}"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR
SRC="$(pwd)"
CFD="$TMPDIR/cf_r54_$$"
mkdir -p "$CFD"
cp rc5_4_tests.py rc5_4_adversarial_tests.py rc5_4_leases.py "$CFD"/

# assertion a modificar: REC-01 (ha de passar) -> FALSE controlat
PATTERN='check("REC-01 no lease before open", lm.status(unit_key=(run, rnd, asg, "u0")) is None)'
SUBST='check("REC-01 no lease before open", lm.status(unit_key=(run, rnd, asg, "u0")) is None and False)'

# aplicar exactament 1 substitució
OUT=$("$PYTHON_BIN" - "$CFD/rc5_4_tests.py" "$PATTERN" "$SUBST" <<'PY'
import sys
f, pat, subst = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(f, encoding='utf-8').read()
if pat not in s:
    print("0")
else:
    open(f, 'w', encoding='utf-8').write(s.replace(pat, subst, 1))
    print("1")
PY
)
APPLIED=$(echo "$OUT" | tail -1)
echo "CF-R54: assertion modificada aplicada: $APPLIED (ha de ser 1)"
[ "$APPLIED" = "1" ] || { echo "CF-R54: FAIL (no aplicat)"; exit 1; }

# py_compile PASS
(cd "$CFD" && "$PYTHON_BIN" -m py_compile rc5_4_tests.py rc5_4_leases.py rc5_4_adversarial_tests.py 2>pycompile.err)
PC=$?
echo "CF-R54: py_compile: $([ $PC -eq 0 ] && echo PASS || echo FAIL)"
[ $PC -eq 0 ] || { echo "CF-R54: FAIL (py_compile)"; exit 1; }

# executar la suite modificada des del directori temporal
(cd "$CFD" && timeout 300 "$PYTHON_BIN" rc5_4_tests.py > run.log 2>&1); RS=$?
FAILS=$(grep -c "^FAIL " "$CFD/run.log" 2>/dev/null || true); FAILS=${FAILS:-0}
TB=$(grep -c "Traceback" "$CFD/run.log" 2>/dev/null); TB=${TB:-0}
echo "CF-R54: suite exit: $RS | FAILs: $FAILS | tracebacks: $TB"
grep "^FAIL " "$CFD/run.log" | head -3

# criteris: una sola FAIL, cap traceback, exit no-zero
OK=0
[ $RS -ne 0 ] && [ "$FAILS" -eq 1 ] && [ "$TB" -eq 0 ] && OK=1
if [ $OK -eq 1 ]; then
    echo "CF-R54: PASS (una sola prova FAIL, 0 tracebacks, exit no-zero, gate Overall FAIL)"
else
    echo "CF-R54: FAIL (criteris no complerts: exit=$RS fails=$FAILS tb=$TB)"
fi

# original intacte (hash dels 3 fitxers == abans de la còpia)
cd "$SRC"
H1=$(sha256sum rc5_4_tests.py | cut -d' ' -f1)
H2=$(sha256sum rc5_4_leases.py | cut -d' ' -f1)
H3=$(sha256sum rc5_4_adversarial_tests.py | cut -d' ' -f1)
EXP_TESTS=$(git show 9c46f65:MANIFEST.sha256 2>/dev/null | grep "  ./rc5_4_tests.py$" | cut -d' ' -f1)
# rc5_4_tests.py no existeix a 9c46f65 (fitxer nou); comparem contra el working tree commitejat
ACT_TESTS=$(git show HEAD:rc5_4_tests.py 2>/dev/null | sha256sum | cut -d' ' -f1)
if [ "$H1" = "$ACT_TESTS" ]; then
    echo "CF-R54: original rc5_4_tests.py intacte (match HEAD)"
else
    echo "CF-R54: WARNING original rc5_4_tests.py differeix de HEAD (revisar)"
fi
rm -rf "$CFD"
[ $OK -eq 1 ] && exit 0 || exit 1
