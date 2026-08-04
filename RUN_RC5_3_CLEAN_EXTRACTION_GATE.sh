#!/usr/bin/env bash
# RUN_RC5_3_CLEAN_EXTRACTION_GATE.sh — clean extraction REAL i PORTABLE (R6.1)
# Entrada: ruta del tarball (i el seu .sha256 al mateix directori).
# Sense rutes fixes: usa ${PYTHON_BIN:-python3}, ${TMPDIR:-/tmp}, mktemp -d,
# ${LOG_DIR:-$(mktemp -d)}. Funciona com a usuari no-root amb Python extern.
set -uo pipefail
TARBALL="${1:?ús: $0 <ruta.tar.gz>}"
[ -f "$TARBALL" ] || { echo "ERROR: no existeix $TARBALL"; exit 1; }
SHAFILE="${TARBALL}.sha256"
[ -f "$SHAFILE" ] || { echo "ERROR: no existeix $SHAFILE"; exit 1; }
PYTHON_BIN="${PYTHON_BIN:-python3}"
BASE_TMP="${TMPDIR:-/tmp}"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$LOG_DIR"
export LOG_DIR PYTHON_BIN

echo "============================================"
echo "  RC5.3 Stage B R6.1 — CLEAN EXTRACTION GATE"
echo "  Tarball: $TARBALL"
echo "  Python:  $($PYTHON_BIN --version 2>&1)"
echo "  LOG_DIR: $LOG_DIR"
echo "============================================"
OVERALL=0

# 1) SHA extern
echo ""; echo "=== [1/6] SHA extern ==="
( cd "$(dirname "$TARBALL")" && sha256sum -c "$(basename "$SHAFILE")" > "$LOG_DIR/ce_sha.log" 2>&1 )
SHA_EC=$?
if [ $SHA_EC -eq 0 ]; then echo "  SHA extern: PASS"; else
    echo "  SHA extern: FAIL"; cat "$LOG_DIR/ce_sha.log"; OVERALL=1; fi

# 2) executar el gate en 2 directoris buits A i B (mktemp -d)
for D in A B; do
    echo ""; echo "=== [2/6] extracció neta al directori buit $D ==="
    DIR="$(mktemp -d "$BASE_TMP/ce_${D}_XXXXXX")"
    tar -xzf "$TARBALL" -C "$DIR"
    echo "  extracció: OK ($(find "$DIR" -type f | wc -l) fitxers)"

    echo ""; echo "=== [3/6] manifest complet a $D ==="
    ( cd "$DIR" && sha256sum -c MANIFEST.sha256 > "$LOG_DIR/ce_mf_$D.log" 2>&1 )
    MF_EC=$?
    if [ $MF_EC -eq 0 ]; then echo "  manifest: PASS"; else
        echo "  manifest: FAIL"; tail -5 "$LOG_DIR/ce_mf_$D.log"; OVERALL=1; fi

    echo ""; echo "=== [4/6] final gate a $D (RC53_SKIP_CLEAN_EXTRACTION=1) ==="
    ( cd "$DIR" && RC53_SKIP_CLEAN_EXTRACTION=1 LOG_DIR="$LOG_DIR/ce_gate_$D" \
        PYTHON_BIN="$PYTHON_BIN" TMPDIR="$BASE_TMP" \
        bash RUN_RC5_3_FINAL_GATE.sh > "$LOG_DIR/ce_gate_$D.out" 2>&1 )
    G_EC=$?
    if [ $G_EC -eq 0 ]; then echo "  gate a $D: PASS"; else
        echo "  gate a $D: FAIL (exit $G_EC)"; tail -8 "$LOG_DIR/ce_gate_$D.out"; OVERALL=1; fi

    # guardar evidència i esborrar el directori temporal
    cp "$LOG_DIR/ce_gate_$D/RC5_3_FINAL_GATE.log" "$LOG_DIR/CLEAN_EXTRACTION_$D.log" 2>/dev/null || true
    cp "$LOG_DIR/ce_mf_$D.log" "$LOG_DIR/CE_MANIFEST_$D.log" 2>/dev/null || true
    rm -rf "$DIR"
    echo "  directori temporal $D eliminat"
done

# 3) zero ports residuals (ports de test RC5.3: 19880-19899, RC5.2: 19858-19861)
echo ""; echo "=== [5/6] ports residuals ==="
PORTS=$(ss -tlnp 2>/dev/null | grep -oE ':(198[0-9][0-9])\b' | sort -u | tr '\n' ' ')
if [ -z "$PORTS" ]; then echo "  ports residuals: cap -> PASS"; else
    echo "  ports residuals: $PORTS -> FAIL"; OVERALL=1; fi

# 4) zero fitxers externs (cap path absolut dins el tarball)
echo ""; echo "=== [6/6] fitxers externs ==="
ABS=$(tar -tzf "$TARBALL" | grep -c '^/' || true)
if [ "$ABS" -eq 0 ]; then echo "  fitxers externs: cap -> PASS"; else
    echo "  fitxers externs: $ABS paths absoluts -> FAIL"; OVERALL=1; fi

echo ""; echo "============================================"
echo "  CLEAN EXTRACTION GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)"
echo "============================================"
exit $OVERALL
