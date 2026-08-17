"""Configuració de l'app MeshTrainer (Fase 1).

Tot configurable per entorn; defaults segurs (bind local, token opcional).
"""
import os
import secrets

APP_ENV = os.environ.get("APP_ENV", "production")   # production | development | test
APP_DB_PATH = os.environ.get("APP_DB_PATH",
                             os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "meshtrainer_app.db"))
APP_STORAGE_DIR = os.environ.get("APP_STORAGE_DIR",
                                 os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "..", "app_storage"))
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8000"))
CORE_SERVER_HOST = os.environ.get("CORE_SERVER_HOST", "127.0.0.1")
CORE_SERVER_PORT_BASE = int(os.environ.get("CORE_SERVER_PORT_BASE", "19862"))

# Token ADMIN per al core (es genera per run al JobRunner; aquest és el token
# de l'API de l'app per a operacions d'escriptura).
APP_API_TOKEN = os.environ.get("APP_API_TOKEN", "")

# Backends amagats per mode (dummy només development/test).
HIDDEN_BACKENDS = {"dummy"} if APP_ENV == "production" else set()


def default_api_token() -> str:
    """Token per defecte per a l'API local (persistent entre reinicis si no
    es configura; en producció real cal APP_API_TOKEN)."""
    if APP_API_TOKEN:
        return APP_API_TOKEN
    tok_path = os.path.join(os.path.dirname(APP_DB_PATH), ".app_api_token")
    if os.path.exists(tok_path):
        with open(tok_path) as f:
            return f.read().strip()
    tok = secrets.token_hex(16)
    os.makedirs(os.path.dirname(tok_path), exist_ok=True)
    with open(tok_path, "w") as f:
        f.write(tok)
    try:
        os.chmod(tok_path, 0o600)
    except Exception:
        pass
    return tok


def storage_subdir(name: str) -> str:
    d = os.path.join(APP_STORAGE_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d
