#!/usr/bin/env bash
# RUN_RC5_3_CLEAN_EXTRACTION_GATE.sh — clean extraction REAL (ordre R6)
# Entrada: ruta del tarball (i el seu .sha256 al mateix directori).
# Verifica SHA extern -> directori buit A -> extreu -> manifest complet ->
# final gate amb RC53_SKIP_CLEAN_EXTRACTION=1 -> Overall PASS -> repeteix a B
# -> zero fitxers externs -> zero ports residuals.
set -uo pipefail
TARBALL="${1:?ús: $0 <ruta.tar.gz>}"
[ -f "$TARBALL" ] || { echo "ERROR: no existeix $TARBALL"; exit 1; }
SHAFILE="${TARBALL}.sha256"
[ -f "$SHAFILE" ] || { echo "ERROR: no existeix $SHAFILE"; exit 1; }

echo "============================================"
echo "  RC5.3 Stage B R6 — CLEAN EXTRACTION GATE"
echo "  Tarball: $TARBALL"
echo "============================================"
OVERALL=0

# 1) SHA extern
echo ""; echo "=== [1/6] SHA extern ==="
( cd "$(dirname "$TARBALL")" && sha256sum -c "$(basename "$SHAFILE")" > /tmp/ce_sha.log 2>&1 )
SHA_EC=$?
if [ $SHA_EC -eq 0 ]; then echo "  SHA extern: PASS"; else
    echo "  SHA extern: FAIL"; cat /tmp/ce_sha.log; OVERALL=1; fi

# 2) executar el gate en 2 directoris buits A i B
for D in A B; do
    echo ""; echo "=== [2/6] extracció neta al directori buit $D ==="
    DIR="/root/ce_${D}"
    rm -rf "$DIR"; mkdir -p "$DIR"
    tar -xzf "$TARBALL" -C "$DIR"
    # el tarball NO pot contenir res fora del directori extret (zero fitxers externs)
    # (els tarballs creats amb -C . no tenen paths absoluts; ho comprovem igualment)
    echo "  extracció: OK ($(find "$DIR" -type f | wc -l) fitxers)"

    echo ""; echo "=== [3/6] manifest complet a $D ==="
    ( cd "$DIR" && sha256sum -c MANIFEST.sha256 > /tmp/ce_mf_$D.log 2>&1 )
    MF_EC=$?
    if [ $MF_EC -eq 0 ]; then echo "  manifest: PASS"; else
        echo "  manifest: FAIL"; tail -5 /tmp/ce_mf_$D.log; OVERALL=1; fi

    echo ""; echo "=== [4/6] final gate a $D (RC53_SKIP_CLEAN_EXTRACTION=1) ==="
    ( cd "$DIR" && export PATH=/root/meshtrainer/.venv/bin:$PATH TMPDIR=/root/tmp_r53 \
        RC53_SKIP_CLEAN_EXTRACTION=1 LOG_DIR="/root/ce_gate_log_$D" \
        && bash RUN_RC5_3_FINAL_GATE.sh > /root/ce_gate_$D.out 2>&1 )
    G_EC=$?
    if [ $G_EC -eq 0 ]; then echo "  gate a $D: PASS"; else
        echo "  gate a $D: FAIL (exit $G_EC)"; tail -8 /root/ce_gate_$D.out; OVERALL=1; fi
done

# 3) zero ports residuals (ports de test RC5.3: 19880-19899, RC5.2: 19858-19861)
echo ""; echo "=== [5/6] ports residuals ==="
PORTS=$(ss -tlnp 2>/dev/null | grep -oE ':(198[0-9][0-9])\b' | sort -u | tr '\n' ' ')
if [ -z "$PORTS" ]; then echo "  ports residuals: cap -> PASS"; else
    echo "  ports residuals: $PORTS -> FAIL"; OVERALL=1; fi

# 4) zero fitxers externs (el tarball no deixa rastre fora del directori d'extracció)
echo ""; echo "=== [6/6] fitxers externs ==="
# no hi ha cap mecanisme del tarball que escrigui fora; ho comprovem amb l'absència
# de paths absoluts dins el tarball
ABS=$(tar -tzf "$TARBALL" | grep -c '^/' || true)
if [ "$ABS" -eq 0 ]; then echo "  fitxers externs: cap -> PASS"; else
    echo "  fitxers externs: $ABS paths absoluts -> FAIL"; OVERALL=1; fi

echo ""; echo "============================================"
echo "  CLEAN EXTRACTION GATE — Overall: $([ $OVERALL -eq 0 ] && echo PASS || echo FAIL)"
echo "============================================"
exit $OVERALL
