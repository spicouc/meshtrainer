"""Descobriment de backends des del registry real del core (model_worker.BACKENDS).

La UI/API no hardcodeja Qwen/MiniCPM: llegeix el registry lazy del core i
prova la disponibilitat de dependències al runtime.
"""
from __future__ import annotations

import importlib
import os
import sys

from app.config import HIDDEN_BACKENDS
from app.models.schemas import BackendInfo

# Rutes del repo (el core viu al directori pare de app/)
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

DISPLAY = {
    "qwen3": "Qwen3",
    "minicpm5": "MiniCPM5",
    "dummy": "Dummy (test)",
}
DEPENDENCIES = {
    "qwen3": ["torch", "transformers", "peft"],
    "minicpm5": ["torch", "transformers", "peft"],
    "dummy": [],
}
# ── Phase 3 (punt 26 + R1-07): paths de models configurables, MAI hardcoded
_SNAPSHOTS = {"qwen3": "qwen3_0_6b_snapshot", "minicpm5": "minicpm5_1b_snapshot"}


def _model_default(backend: str) -> str:
    """Path per defecte del model: env > MODELS_DIR > relatiu al repo.

    MAI escaneja /root ni cap directori del laboratori (R1-07).
    """
    import os
    from app.config import MODELS_DIR
    env_map = {"qwen3": "QWEN3_MODEL", "minicpm5": "MINICPM5_MODEL"}
    env = os.environ.get(env_map.get(backend, ""))
    if env:
        return env
    name = _SNAPSHOTS.get(backend, f"{backend}_snapshot")
    repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    cands = [
        os.path.join(MODELS_DIR, name),
        os.path.join(repo, name),
    ]
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


MODEL_DEFAULTS = {
    "qwen3": _model_default("qwen3"),
    "minicpm5": _model_default("minicpm5"),
    "dummy": "dummy",
}


def _importable(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def discover_backends() -> list[BackendInfo]:
    """Llegeix el registry del core (BACKENDS) i avalua disponibilitat."""
    # Import del registry real del core (lazy, sense carregar cap plugin).
    import model_worker
    out: list[BackendInfo] = []
    for name in sorted(model_worker.BACKENDS):
        if name in HIDDEN_BACKENDS:
            continue
        deps = DEPENDENCIES.get(name, [])
        available = all(_importable(d) for d in deps)
        out.append(BackendInfo(
            id=name,
            display_name=DISPLAY.get(name, name),
            capabilities=["lora", "seq_len_configurable", "fedavg"],
            required_dependencies=deps,
            available=available,
            model_constraints={"default_model": MODEL_DEFAULTS.get(name, "")},
        ))
    return out


def backend_available(name: str) -> bool:
    return any(b.id == name and b.available for b in discover_backends())


def discover_models(backend_id: str) -> list[dict]:
    """Llista snapshots locals (directoris amb config.json + weights).

    Només cerca a: env (QWEN3_MODEL/MINICPM5_MODEL), MODELS_DIR i el
    directori relatiu al repo. MAI /root (R1-07).
    """
    import os
    from app.config import MODELS_DIR
    base = MODEL_DEFAULTS.get(backend_id, "")
    models = []
    candidates = []
    if base:
        candidates.append(base)
    # snapshots al directori de models de l'usuari (configurable)
    name = _SNAPSHOTS.get(backend_id, "")
    if name:
        candidates.append(os.path.join(MODELS_DIR, name))
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        cfg = os.path.join(path, "config.json")
        exists = os.path.isdir(path) and os.path.isfile(cfg)
        models.append({
            "model_id": os.path.basename(path) if path != base else base,
            "backend_id": backend_id,
            "local_path": path,
            "exists": exists,
        })
    return models
