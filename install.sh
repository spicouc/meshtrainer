#!/usr/bin/env bash
# MeshTrainer installer — Phase 3 (v1.4.0)
# Simple, auditable, idempotent. No root required, no silent system changes.
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

# ── 2. crea .venv (idempotent) ──────────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    info "Creant entorn virtual a .venv …"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    ok "Entorn virtual creat"
else
    ok "Entorn virtual ja existeix (idempotent)"
fi
VENV_PY="$VENV_DIR/bin/python"

# ── 3. instal·la requirements (idempotent, offline-friendly si ja hi són) ──
if [ -f requirements-app.txt ]; then
    info "Instal·lant requirements-app.txt …"
    "$VENV_PY" -m pip install --quiet --disable-pip-version-check -r requirements-app.txt
    ok "Requirements instal·lats"
elif [ -f requirements.txt ]; then
    info "Instal·lant requirements.txt …"
    "$VENV_PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt
    ok "Requirements instal·lats"
else
    die "No trobo requirements-app.txt ni requirements.txt."
fi

# ── 4. valida imports crítics ───────────────────────────────────────────────
info "Validant imports …"
"$VENV_PY" -c "import fastapi, pydantic, uvicorn; print('imports OK:', 'fastapi', fastapi.__version__, '/ pydantic', pydantic.__version__)" \
    || die "Validació d'imports fallada — reinstal·la requirements."
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
