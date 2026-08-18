#!/usr/bin/env bash
# RUN_RC5_4_MUTATION_GATE.sh — 37 mutants MUT-R4-01..37 (PATTERN/SUBST applied_count=1)
# Per mutant es registren: mutant, fitxer, patró, substitució, ocurrències,
# applied_count, py_compile, suite executada, exit code, traceback count,
# assertio...
# assertion exacta que falla, classificació.
set -uo pipefail
SRC="$(pwd)"; BASE_TMP="${TMPDIR:-/tmp}"; TMPDIR="$BASE_TMP/r54_mut_$$"
mkdir -p "$TMPDIR"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR
REPORT="$LOG_DIR/RC5_4_MUTATION_GATE.log"
: > "$REPORT"
OVERALL=0
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN

echo "=== BASELINE ===" | tee -a "$REPORT"
"$PYTHON_BIN" -m py_compile rc5_4_tests.py rc5_4_adversarial_tests.py rc5_4_leases.py rc5_4_integration.py && echo "  py_compile: PASS" | tee -a "$REPORT"
timeout 300 "$PYTHON_BIN" rc5_4_tests.py > /dev/null 2>&1; S1=$?
timeout 300 "$PYTHON_BIN" rc5_4_adversarial_tests.py > /dev/null 2>&1; S2=$?
S=$((S1 + S2))
echo "Baseline: $([ $S -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$REPORT"
[ $S -eq 0 ] || { echo "  mutation gate invalid: baseline FAIL"; exit 1; }

DETECTED=0; INVALID=0; RUNTIME=0; NOT_APPLIED=0; TIMEOUTS=0
for M in mutants_r54/mut_r4_*.py; do
    NAME=$(basename "$M" .py)
    echo ""; echo "=== $NAME ===" | tee -a "$REPORT"
    W="$TMPDIR/$NAME"; mkdir -p "$W"
    cp rc5_4_tests.py rc5_4_adversarial_tests.py rc5_4_leases.py rc5_4_integration.py rc5_4_http_server.py rc5_4_http_lease_tests.py "$W"/
    # dependències del pipeline real (RC5.2/RC5.3) — la suite HTTP les importa
    cp rc5_2_*.py rc5_3_*.py "$W"/ 2>/dev/null || true
    # free disk between mutants (temp DBs)
    find "$TMPDIR" -maxdepth 1 -name "*.db" -delete 2>/dev/null || true
    PATTERN=$(grep -oP '(?<=PATTERN: ).*' "$M" | head -1)
    SUBST=$(grep -oP '(?<=SUBST: ).*' "$M" | head -1)
    echo "  mutant: $NAME | fitxer: $(basename "$M")" | tee -a "$REPORT"
    echo "  patró: $PATTERN" | tee -a "$REPORT"
    echo "  substitució: $SUBST" | tee -a "$REPORT"
    if [ -z "$PATTERN" ] || [ -z "$SUBST" ]; then
        echo "  $NAME: INVALID (patró/substitució buida)" | tee -a "$REPORT"; INVALID=$((INVALID+1)); continue
    fi
    COUNT=$(grep -oF "$PATTERN" "$W/rc5_4_leases.py" "$W/rc5_4_tests.py" "$W/rc5_4_adversarial_tests.py" "$W/rc5_4_integration.py" "$W/rc5_4_http_server.py" 2>/dev/null | wc -l)
    APPLIED_OUT=$("$PYTHON_BIN" - "$W/rc5_4_leases.py" "$W/rc5_4_tests.py" "$W/rc5_4_adversarial_tests.py" "$W/rc5_4_integration.py" "$W/rc5_4_http_server.py" "$PATTERN" "$SUBST" <<'PYEOF'
import sys
files = sys.argv[1:-2]
pat, subst = sys.argv[-2], sys.argv[-1]
# interpret literal '\n' in PATTERN/SUBST lines as real newlines
pat = pat.replace('\\n', '\n')
subst = subst.replace('\\n', '\n')
applied = 0
for f in files:
    try:
        s = open(f, encoding='utf-8').read()
    except FileNotFoundError:
        continue
    if pat in s:
        s2 = s.replace(pat, subst, 1)
        if s2 != s:
            open(f, 'w', encoding='utf-8').write(s2)
            applied += 1
print(applied)
PYEOF
)
    if [ -z "$APPLIED_OUT" ]; then APPLIED=0; else APPLIED=$(echo "$APPLIED_OUT" | tail -1); fi
    if [ "$APPLIED" -ne 1 ]; then
        echo "  $NAME: NOT APPLIED (applied_count=$APPLIED)" | tee -a "$REPORT"; NOT_APPLIED=$((NOT_APPLIED+1)); continue
    fi
    # py_compile del paquet mutat
    (cd "$W" && "$PYTHON_BIN" -m py_compile rc5_4_leases.py rc5_4_tests.py rc5_4_adversarial_tests.py rc5_4_integration.py rc5_4_http_server.py rc5_4_http_lease_tests.py 2>pycompile.err)
    PC=$?
    echo "  py_compile: $([ $PC -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$REPORT"
    if [ $PC -ne 0 ]; then
        echo "  $NAME: INVALID (py_compile FAIL)" | tee -a "$REPORT"; INVALID=$((INVALID+1)); continue
    fi
    # suites: crash matrix, adversarial, HTTP (seqüencial, sense paral·lelitzar)
    (cd "$W" && timeout 300 "$PYTHON_BIN" rc5_4_tests.py > run_crash.log 2>&1); RS_CRASH=$?
    (cd "$W" && timeout 300 "$PYTHON_BIN" rc5_4_adversarial_tests.py > run_adv.log 2>&1); RS_ADV=$?
    (cd "$W" && timeout 300 "$PYTHON_BIN" rc5_4_http_lease_tests.py > run_http.log 2>&1); RS_HTTP=$?
    # R3.1 classificació: primer TIMEOUT (qualsevol == 124), NO convertir
    # posteriorment 124 en 1
    if [ $RS_CRASH -eq 124 ] || [ $RS_ADV -eq 124 ] || [ $RS_HTTP -eq 124 ]; then
        echo "  suite executada: crash=$RS_CRASH adv=$RS_ADV http=$RS_HTTP" | tee -a "$REPORT"
        echo "  $NAME: TIMEOUT (exit 124)" | tee -a "$REPORT"; TIMEOUTS=$((TIMEOUTS+1)); rm -rf "$W"; continue
    fi
    TBC=$(grep -c "Traceback" "$W/run_crash.log" 2>/dev/null); TBC=${TBC:-0}
    TBA=$(grep -c "Traceback" "$W/run_adv.log" 2>/dev/null); TBA=${TBA:-0}
    TBH=$(grep -c "Traceback" "$W/run_http.log" 2>/dev/null); TBH=${TBH:-0}
    TB=$((TBC + TBA + TBH))
    RS=0
    [ $RS_CRASH -ne 0 ] && RS=1
    [ $RS_ADV -ne 0 ] && RS=1
    [ $RS_HTTP -ne 0 ] && RS=1
    echo "  suite executada: crash=$RS_CRASH adv=$RS_ADV http=$RS_HTTP | tracebacks: $TB ($TBC+$TBA+$TBH)" | tee -a "$REPORT"
    if [ "$TB" -gt 0 ]; then
        echo "  $NAME: RUNTIME-INVALID (tracebacks=$TB)" | tee -a "$REPORT"; RUNTIME=$((RUNTIME+1)); rm -rf "$W"; continue
    fi
    if [ $RS -ne 0 ]; then
        # assertion detectora concreta (mai el resum de la suite): busca a
        # TOTES les suites, incloent run_http.log per als mutants del dispatcher
        ASRT=$(grep -hm1 "^FAIL " "$W/run_crash.log" "$W/run_adv.log" "$W/run_http.log" 2>/dev/null | head -1 | sed 's/^[^:]*://')
        [ -z "$ASRT" ] && ASRT=$(grep -hm1 "FAIL " "$W/run_crash.log" "$W/run_adv.log" "$W/run_http.log" 2>/dev/null | head -1 | sed 's/^[^:]*://')
        echo "  assertion detectora: $ASRT" | tee -a "$REPORT"
        echo "  $NAME: DETECTED" | tee -a "$REPORT"; DETECTED=$((DETECTED+1))
    elif [ $RS -eq 0 ]; then
        echo "  assertion detectora: cap (suite exit 0)" | tee -a "$REPORT"
        echo "  $NAME: NOT DETECTED (suite exit 0)" | tee -a "$REPORT"
    fi
    rm -rf "$W"
done

echo ""; echo "=== RESUM ===" | tee -a "$REPORT"
echo "Score: $DETECTED/37 Invalid: $INVALID Runtime: $RUNTIME Not_applied: $NOT_APPLIED Timeouts: $TIMEOUTS" | tee -a "$REPORT"
[ $DETECTED -eq 37 ] && [ $INVALID -eq 0 ] && [ $RUNTIME -eq 0 ] && [ $NOT_APPLIED -eq 0 ] && [ $TIMEOUTS -eq 0 ] || OVERALL=1
echo "Mutation gate RC5.4: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$REPORT"
exit $OVERALL
