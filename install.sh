#!/usr/bin/env bash
# MeshTrainer installer — Phase 3 R1 (v1.4.0)
# Simple, auditable, idempotent. No root required, no silent system changes.
# PREPARA TOT EL NECESSARI PER ENTRENAR (R1-03):
#   - Python compatible
#   - .venv (o reutilitza PYTHON_BIN si es passa explícit)
#   - requirements-app.txt (FastAPI/API)
#   - requirements-training.txt (torch/transformers/peft — backends)
# NO descarrega models sense consentiment.
set -euo pipefail

# ── helpers ────────────────────────────────────────────────────────────────
info() { printf '\033[1;34m[meshtrainer]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# ── resolució de paths (el script pot córrer des de qualsevol lloc) ────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. detecta Python compatible ───────────────────────────────────────────
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    die "Python no trobat ('$PYTHON_BIN'). Necessites Python 3.11+."
fi
PY_VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
info "Python detectat: $PY_VER ($("$PYTHON_BIN" --version 2>&1 | cut -d' ' -f2))"
case "$PY_VER" in
    3.11*|3.12*|3.13*) ok "Python compatible (3.11+)" ;;
    *) die "Python $PY_VER no compatible — cal 3.11+." ;;
esac

# ── 2. venv: crea .venv, o reutilitza PYTHON_BIN si és explícit ────────────
VENV_DIR="$SCRIPT_DIR/.venv"
if [ "${PYTHON_BIN}" != "python3" ]; then
    # PYTHON_BIN explícit (ex: entorn ja preparat): no creem .venv nou
    VENV_PY="$PYTHON_BIN"
    ok "Reutilitzant Python explícit: $VENV_PY"
elif [ ! -x "$VENV_DIR/bin/python" ]; then
    info "Creant entorn virtual a .venv …"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    VENV_PY="$VENV_DIR/bin/python"
    ok "Entorn virtual creat"
else
    VENV_PY="$VENV_DIR/bin/python"
    ok "Entorn virtual ja existeix (idempotent)"
fi

# ── 3. instal·la requirements (app + training, idempotent) ─────────────────
REQS=()
[ -f requirements-app.txt ] && REQS+=("requirements-app.txt")
[ -f requirements-training.txt ] && REQS+=("requirements-training.txt")
if [ "${#REQS[@]}" = "0" ] && [ -f requirements.txt ]; then
    REQS+=("requirements.txt")
fi
if [ "${#REQS[@]}" = "0" ]; then
    die "No trobo requirements-app.txt / requirements-training.txt / requirements.txt."
fi
for R in "${REQS[@]}"; do
    info "Instal·lant $R …"
    "$VENV_PY" -m pip install --quiet --disable-pip-version-check -r "$R"
    ok "$R instal·lat"
done

# ── 4. valida imports crítics (app + training) ─────────────────────────────
info "Validant imports (app + training) …"
"$VENV_PY" -c "import fastapi, pydantic, uvicorn" \
    || die "Validació d'imports de l'app fallada — revisa requirements-app.txt."
if [ -f requirements-training.txt ]; then
    "$VENV_PY" -c "import torch, transformers, peft" \
        || die "Validació d'imports de training fallada — revisa requirements-training.txt (instal·la amb --skip-training si no vols entrenar encara)."
    ok "Imports de training OK (torch/transformers/peft)"
fi
ok "Imports validats"

# ── 5. crea directoris runtime (mai al repo, sota storage/) ────────────────
mkdir -p "$SCRIPT_DIR/app_storage/logs" "$SCRIPT_DIR/app_storage/uploads"
ok "Directoris runtime creats (app_storage/logs, app_storage/uploads)"

# ── 6. NO descarrega models · NO toca firewall · NO canvis de sistema ──────
info "Nota: els models NO es descarreguen automàticament."
info "       La Web UI els gestiona de manera explícita i amb consentiment."

echo ""
echo "=================================================="
echo "  MeshTrainer installed successfully."
echo "  Start:  ./meshtrainer start"
echo "=================================================="
