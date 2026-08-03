#!/usr/bin/env bash
# RC5.3 Stage B R6 — COMBINED FINAL GATE (20 passos)
# Requisits R6:
#  - dependency check amb set +e, exit code capturat, continuació fins al resum
#  - dependency FAIL -> OVERALL=1
#  - manifest FAIL -> OVERALL=1; NO "ALL FILES OK" si hi ha fitxers no coberts
#  - controlled-failure (RC5.3 i RC5.2) amb substitució EXACTA + py_compile PASS
#    + cap traceback; qualsevol traceback -> FAIL
#  - clean extraction NOT EXECUTED -> FAIL, excepte RC53_SKIP_CLEAN_EXTRACTION=1
#  - cap subgate no executat pot comptar com a PASS
#  - resum SEMPRE imprès al final
set -uo pipefail
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/RC5_3_FINAL_GATE.log"
: > "$LOG"
OVERALL=0

echo "============================================"
echo "  RC5.3 Stage B R6 — COMBINED FINAL GATE"
echo "  LOG_DIR: $LOG_DIR"
echo "============================================"

run_sub() {
    local label="$1" cmd="$2" timeout="$3"
    echo ""; echo "=== $label ===" >> "$LOG"
    timeout "$timeout" bash -c "$cmd" >> "$LOG" 2>&1; local ec=$?
    if [ $ec -eq 124 ]; then echo "  $label: TIMEOUT" | tee -a "$LOG"; OVERALL=1
    elif [ $ec -eq 0 ]; then echo "  $label: PASS" | tee -a "$LOG"
    else echo "  $label: FAIL" | tee -a "$LOG"; OVERALL=1; fi
}

# --- 1) dependency check (set +e, continuar sempre) ---
echo ""; echo "=== [1/20] dependency check ===" | tee -a "$LOG"
python3 - <<'PY' >> "$LOG" 2>&1
import os, sys
need = ["rc5_3_multiworker.py","rc5_3_tests.py","rc5_3_adversarial_tests.py",
        "rc5_2_http_server.py","rc5_2_worker_runtime.py",
        "rc5_2_tensor_bundle.py","rc5_2_tensor_envelope.py","rc5_2_receipt.py",
        "rc5_2_coordinator.py","rc5_2_artifacts.py","rc5_2_canonical.py",
        "rc5_2_numerical_models.py","rc5_2_numerical_profile.py","rc5_2_lora.py",
        "rc5_2_jsonrpc.py","rc5_2_numerical_adapter.py","rc5_2_state.py",
        "RUN_RC5_2_PHASE2_TESTS.sh","RUN_RC5_2_PHASE2_ADVERSARIAL_GATE.sh",
        "RUN_RC5_2_PHASE2_MUTATION_GATE.sh","RUN_RC5_2_PHASE1_TESTS.sh",
        "RUN_RC5_2_PHASE1_MUTATION_GATE.sh","RUN_RC5_1_TESTS.sh","RUN_RC5_1_MUTATION_GATE.sh",
        "RUN_RC5_3_TESTS.sh","RUN_RC5_3_MUTATION_GATE.sh","RUN_RC5_3_ADVERSARIAL_GATE.sh",
        "RUN_RC5_3_CONTROLLED_FAILURE.sh","RUN_RC5_2_CONTROLLED_FAILURE.sh",
        "ENVIRONMENT.txt","requirements-runtime.txt","MANIFEST.sha256"]
missing = [f for f in need if not os.path.exists(f)]
if missing:
    print("MISSING:", missing); sys.exit(1)
print("deps: OK")
PY
DEP=$?
if [ $DEP -eq 0 ]; then echo "  dependency check: PASS" | tee -a "$LOG"
else echo "  dependency check: FAIL (exit $DEP)" | tee -a "$LOG"; OVERALL=1; fi

# --- 2) py_compile ---
echo ""; echo "=== [2/20] py_compile ===" | tee -a "$LOG"
python3 -m py_compile *.py >> "$LOG" 2>&1; PC=$?
[ $PC -eq 0 ] && echo "  py_compile: PASS" | tee -a "$LOG" || { echo "  py_compile: FAIL" | tee -a "$LOG"; OVERALL=1; }

# --- 3/4) RC5.3 normal x2 ---
run_sub "[3/20] RC5.3 normal Run 1" "bash RUN_RC5_3_TESTS.sh" 1200
run_sub "[4/20] RC5.3 normal Run 2" "python3 rc5_3_tests.py" 600
# --- 5) RC5.3 adversarial ---
run_sub "[5/20] RC5.3 adversarial" "bash RUN_RC5_3_ADVERSARIAL_GATE.sh" 700
# --- 6) RC5.3 mutation ---
run_sub "[6/20] RC5.3 mutation" "bash RUN_RC5_3_MUTATION_GATE.sh" 5000

# --- 7/8) RC5.2 normal x2 ---
run_sub "[7/20] RC5.2 normal Run 1" "python3 rc5_2_phase2_tests.py" 400
run_sub "[8/20] RC5.2 normal Run 2" "python3 rc5_2_phase2_tests.py" 400
# --- 9) RC5.2 adversarial ---
run_sub "[9/20] RC5.2 adversarial" "bash RUN_RC5_2_PHASE2_ADVERSARIAL_GATE.sh" 500
# --- 10) RC5.2 mutation ---
run_sub "[10/20] RC5.2 mutation" "bash RUN_RC5_2_PHASE2_MUTATION_GATE.sh" 5000

# --- 11/12) Phase 1 x2 ---
run_sub "[11/20] Phase 1 Run 1" "python3 rc5_2_phase1_tests.py" 400
run_sub "[12/20] Phase 1 Run 2" "python3 rc5_2_phase1_tests.py" 400
# --- 13) Phase 1 mutation ---
run_sub "[13/20] Phase 1 mutation" "bash RUN_RC5_2_PHASE1_MUTATION_GATE.sh" 2500

# --- 14/15) RC5.1 x2 ---
run_sub "[14/20] RC5.1 Run 1" "bash RUN_RC5_1_TESTS.sh" 700
run_sub "[15/20] RC5.1 Run 2" "bash RUN_RC5_1_TESTS.sh" 700
# --- 16) RC5.1 mutation ---
run_sub "[16/20] RC5.1 mutation" "bash RUN_RC5_1_MUTATION_GATE.sh" 2500

# --- 17) restart subprocess ---
echo ""; echo "=== [17/20] restart subprocess ===" | tee -a "$LOG"
timeout 400 python3 -c "
import rc5_3_tests as t
t.test_process_restart_real()
print('restart subprocess: PASS')
" >> "$LOG" 2>&1; RS=$?
[ $RS -eq 0 ] && echo "  restart subprocess: PASS" | tee -a "$LOG" || { echo "  restart subprocess: FAIL" | tee -a "$LOG"; OVERALL=1; }

# --- 18) controlled-failure RC5.3 + RC5.2 (REAL, substitució exacta) ---
echo ""; echo "=== [18/20] controlled-failure RC5.3 ===" | tee -a "$LOG"
bash RUN_RC5_3_CONTROLLED_FAILURE.sh >> "$LOG" 2>&1; CF3=$?
if [ $CF3 -eq 0 ]; then echo "  controlled-failure RC5.3: PASS" | tee -a "$LOG"
else echo "  controlled-failure RC5.3: FAIL (exit $CF3)" | tee -a "$LOG"; OVERALL=1; fi

echo ""; echo "=== [18b/20] controlled-failure RC5.2 ===" | tee -a "$LOG"
bash RUN_RC5_2_CONTROLLED_FAILURE.sh >> "$LOG" 2>&1; CF2=$?
if [ $CF2 -eq 0 ]; then echo "  controlled-failure RC5.2: PASS" | tee -a "$LOG"
else echo "  controlled-failure RC5.2: FAIL (exit $CF2)" | tee -a "$LOG"; OVERALL=1; fi

# --- 19) manifest (cobertura COMPLETA: tots els fitxers regulars menys MANIFEST.sha256) ---
echo ""; echo "=== [19/20] manifest ===" | tee -a "$LOG"
sha256sum -c MANIFEST.sha256 >> "$LOG" 2>&1; MF=$?
python3 - <<'PY' >> "$LOG" 2>&1
import os, sys
covered = set()
if os.path.exists("MANIFEST.sha256"):
    for line in open("MANIFEST.sha256"):
        line = line.strip()
        if line:
            covered.add(line.split()[-1])
regular = set()
for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in (".git", ".venv", "__pycache__")]
    for f in files:
        p = os.path.join(root, f)
        if os.path.isfile(p):
            regular.add(p)
regular.discard("./MANIFEST.sha256")
regular.discard("MANIFEST.sha256")
uncovered = sorted(regular - covered)
if uncovered:
    print("UNCOVERED FILES:", len(uncovered))
    for u in uncovered[:30]: print("  ", u)
    sys.exit(1)
print(f"coverage: {len(covered)} manifest == {len(regular)} regulars (sense MANIFEST.sha256)")
PY
MF2=$?
if [ $MF -eq 0 ] && [ $MF2 -eq 0 ]; then
    echo "  manifest: PASS (ALL FILES OK, cobertura completa)" | tee -a "$LOG"
else
    echo "  manifest: FAIL (sha=$MF coverage=$MF2)" | tee -a "$LOG"; OVERALL=1
fi

# --- 20) clean extraction ---
echo ""; echo "=== [20/20] clean extraction ===" | tee -a "$LOG"
TARBALL=$(ls meshtrainer_rc53_stageb_r6_final_*.tar.gz 2>/dev/null | head -1 || true)
if [ -n "$TARBALL" ]; then
    EX_DIR="${TMPDIR:-/tmp}/rc53_extract_$$"
    rm -rf "$EX_DIR"; mkdir -p "$EX_DIR"
    tar -xzf "$TARBALL" -C "$EX_DIR"
    (cd "$EX_DIR" && sha256sum -c MANIFEST.sha256 > /dev/null 2>&1); EX=$?
    rm -rf "$EX_DIR"
    [ $EX -eq 0 ] && echo "  clean extraction: PASS" | tee -a "$LOG" || { echo "  clean extraction: FAIL" | tee -a "$LOG"; OVERALL=1; }
elif [ "${RC53_SKIP_CLEAN_EXTRACTION:-0}" = "1" ]; then
    echo "  clean extraction: SKIPPED (mode delegat RC53_SKIP_CLEAN_EXTRACTION=1)" | tee -a "$LOG"
else
    echo "  clean extraction: NOT EXECUTED (no tarball i no mode delegat)" | tee -a "$LOG"
    OVERALL=1
fi

# --- RESUM (SEMPRE) ---
echo ""; echo "============================================" | tee -a "$LOG"
echo "  RC5.3 COMBINED GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)" | tee -a "$LOG"
echo "============================================" | tee -a "$LOG"
exit $OVERALL
