"""Configuració de l'app MeshTrainer (Fase 1 + Phase 3).

Tot configurable per entorn; defaults segurs (bind local, token opcional).

Fonts de configuració (per ordre de precedència):
1. Variables d'entorn (APP_HOST, APP_PORT, APP_STORAGE_DIR, ...)
2. Fitxer de configuració de l'usuari: ~/.config/meshtrainer/config.toml
3. Defaults segurs (Phase 3: local-by-default)

El fitxer de l'usuari es llegeix amb tomllib (stdlib, Python 3.11+); no
introdueix dependències noves. Si és invàlid, s'ignora amb un avís a stderr
(sense trencar l'arrencada) i es mantenen els defaults.
"""
import os
import secrets
import sys

APP_ENV = os.environ.get("APP_ENV", "production")   # production | development | test

# ── fitxer de configuració de l'usuari (Phase 3, punt 27) ─────────────────
def _user_config() -> dict:
    """Llegeix ~/.config/meshtrainer/config.toml si existeix (idempotent)."""
    path = os.path.join(os.path.expanduser("~"), ".config", "meshtrainer", "config.toml")
    try:
        import tomllib  # Python 3.11+ stdlib
    except ImportError:
        return {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception as e:
        print(f"[meshtrainer] avís: config.toml invàlid ({e}) — s'usen defaults.", file=sys.stderr)
        return {}

_USER_CFG = _user_config()


def _cfg_get(keys: tuple, default):
    """Precedència: env → config.toml → default."""
    env_map = {
        ("host",): "APP_HOST", ("port",): "APP_PORT",
        ("storage_dir",): "APP_STORAGE_DIR", ("models_dir",): "MODELS_DIR",
        ("environment",): "APP_ENV",
    }
    env_key = env_map.get(keys)
    if env_key and os.environ.get(env_key):
        return os.environ[env_key]
    # navega el dict del toml
    node = _USER_CFG
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def _cfg_int(keys: tuple, default: int) -> int:
    v = _cfg_get(keys, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


APP_ENV = str(_cfg_get(("environment",), APP_ENV))
APP_DB_PATH = os.environ.get("APP_DB_PATH",
                             os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "meshtrainer_app.db"))
APP_STORAGE_DIR = str(_cfg_get(("storage_dir",), os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "app_storage")))
MODELS_DIR = str(_cfg_get(("models_dir",), os.path.join(
    os.path.expanduser("~"), ".cache", "meshtrainer", "models")))

# ── bind local per defecte (Phase 3, punt 28: local-by-default) ───────────
APP_HOST = str(_cfg_get(("host",), "127.0.0.1"))
APP_PORT = _cfg_int(("port",), 8000)
if APP_HOST not in ("127.0.0.1", "localhost", "::1") and APP_ENV != "test":
    print("[meshtrainer] AVÍS: exposant l'API a la xarxa "
          f"({APP_HOST}) — sense autenticació, només per a LAN de confiança.",
          file=sys.stderr)

CORE_SERVER_HOST = os.environ.get("CORE_SERVER_HOST", "127.0.0.1")
CORE_SERVER_PORT_BASE = int(os.environ.get("CORE_SERVER_PORT_BASE", "19862"))

# Token ADMIN per al core (es genera per run al JobRunner; aquest és el token
# de l'API de l'app per a operacions d'escriptura).
APP_API_TOKEN = os.environ.get("APP_API_TOKEN", "")

# Backends amagats per mode (dummy només development/test).
HIDDEN_BACKENDS = {"dummy"} if APP_ENV == "production" else set()

APP_VERSION = "1.4.0"   # Phase 3 (punt 29)


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
    d = os.path.abspath(os.path.join(APP_STORAGE_DIR, name))
    os.makedirs(d, exist_ok=True)
    return d
