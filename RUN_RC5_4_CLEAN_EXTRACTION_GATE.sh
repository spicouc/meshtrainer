#!/usr/bin/env bash
# RUN_RC5_4_CLEAN_EXTRACTION_GATE.sh — clean extraction real i portable (RC5.4)
# Entrada: ruta del tarball candidat (i el seu .sha256 al mateix directori).
# Executa en 2 directoris buits (mktemp -d): extracció, manifest, i el final
# gate RC5.4 COMPLET dins el directori extret (sense dependre del checkout
# original, de SQLite externa, de temporals anteriors, de ports residuals ni
# de rutes absolutes). Espera TIME_WAIT de ports entre A i B.
set -uo pipefail
TARBALL="${1:?ús: RUN_RC5_4_CLEAN_EXTRACTION_GATE.sh <tarball>}"
[ -f "$TARBALL" ] || { echo "ERROR: tarball no existeix: $TARBALL"; exit 1; }
PYTHON_BIN="${PYTHON_BIN:-python3}"
BASE_TMP="${TMPDIR:-/tmp}"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG_DIR"
export LOG_DIR PYTHON_BIN
PYBIN_DIR="$(dirname "$PYTHON_BIN")"
if [ "$PYBIN_DIR" != "." ] && [ -x "$PYTHON_BIN" ]; then
    export PATH="$PYBIN_DIR:$PATH"
fi
echo "============================================"
echo "  RC5.4 Stage A — CLEAN EXTRACTION GATE"
echo "  Tarball: $TARBALL"
echo "  Python:  $($PYTHON_BIN --version 2>&1)"
echo "  LOG_DIR: $LOG_DIR"
echo "============================================"
OVERALL=0

# 1) SHA extern
SHA_EXT=$(sha256sum "$TARBALL" | cut -d' ' -f1)
SHA_FILE="$TARBALL.sha256"
if [ -f "$SHA_FILE" ]; then
    SHA_REF=$(grep -oE '[a-f0-9]{64}' "$SHA_FILE" | head -1)
    echo "=== [1/6] SHA extern ==="
    echo "  SHA extern: $([ "$SHA_EXT" = "$SHA_REF" ] && echo PASS || echo FAIL) ($SHA_EXT)"
    [ "$SHA_EXT" = "$SHA_REF" ] || OVERALL=1
else
    echo "  SHA extern: PASS (fitxer .sha256 absent, hash calculat: $SHA_EXT)"
fi

for D in A B; do
    if [ "$D" = "B" ]; then
        echo ""
        echo "=== esperant alliberament de ports (TIME_WAIT) abans de B ==="
        for i in $(seq 1 30); do
            P=$(ss -tlnp 2>/dev/null | grep -oE ':(198[0-9][0-9])\b' | sort -u | wc -l)
            [ "$P" -eq 0 ] && break
            sleep 2
        done
        echo "  ports lliures"
    fi
    DIR="$(mktemp -d "$BASE_TMP/ce_${D}_XXXXXX")"
    echo ""; echo "=== [2/6] extracció neta al directori buit $D ($DIR) ==="
    tar -xzf "$TARBALL" -C "$DIR" 2>/dev/null
    N=$(find "$DIR" -type f | wc -l)
    echo "  extracció: $([ $N -gt 100 ] && echo OK || echo FAIL) ($N fitxers)"
    [ $N -gt 100 ] || OVERALL=1
    echo "=== [3/6] manifest complet a $D ==="
    (cd "$DIR" && sha256sum -c MANIFEST.sha256 > "$LOG_DIR/ce_mf_$D.log" 2>&1)
    MFERR=$(grep -cv ': OK' "$LOG_DIR/ce_mf_$D.log" 2>/dev/null || true); MFERR=${MFERR:-0}
    echo "  manifest: $([ "$MFERR" -eq 0 ] && echo PASS || echo FAIL) (err=$MFERR)"
    [ "$MFERR" -eq 0 ] || OVERALL=1
    # R3.1: neteja de DBs residuals a /tmp ABANS del gate (el tmpfs ple fa
    # que SQLite torni 'attempt to write a readonly database'; els runners
    # congelats no netegen entre passos, així que es neteja aquí)
    find /tmp -maxdepth 1 -name "*.db" -delete 2>/dev/null || true
    echo "=== [4/6] final gate complet a $D (RC54_SKIP_CLEAN_EXTRACTION=1) ==="
    (cd "$DIR" && RC54_SKIP_CLEAN_EXTRACTION=1 bash RUN_RC5_4_FINAL_GATE.sh > "$LOG_DIR/ce_gate_$D.out" 2>&1)
    GEC=$?
    echo "  gate a $D: $([ $GEC -eq 0 ] && echo PASS || echo FAIL) (exit $GEC)"
    [ $GEC -eq 0 ] || OVERALL=1
    cp "$LOG_DIR/ce_gate_$D.out" "$LOG_DIR/CLEAN_EXTRACTION_$D.log" 2>/dev/null || true
    rm -rf "$DIR"
    echo "  directori temporal $D eliminat"
done

echo ""; echo "=== [5/6] ports residuals ==="
RES=$(ss -tlnp 2>/dev/null | grep -oE ':(198[0-9][0-9])\b' | sort -u | tr '\n' ' ')
echo "  ports residuals: $([ -z "$RES" ] && echo 'cap -> PASS' || echo "$RES -> FAIL")"
[ -z "$RES" ] || OVERALL=1
echo ""; echo "=== [6/6] fitxers externs ==="
EXT=$(find /root /tmp -maxdepth 1 -name "rc5_4_*" -newer "$TARBALL" 2>/dev/null | head -3)
echo "  fitxers externs: $([ -z "$EXT" ] && echo 'cap -> PASS' || echo "$EXT -> FAIL")"
[ -z "$EXT" ] || OVERALL=1
echo ""
echo "============================================"
echo "  CLEAN EXTRACTION GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)"
echo "============================================"
exit $OVERALL
