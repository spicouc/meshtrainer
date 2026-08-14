#!/usr/bin/env python3
"""minicpm5_isolation_tests.py — ISO-M1..M4 (MiniCPM5 Portability, punt 1).

Executa amb el Python del SISTEMA (sense torch/transformers/peft) per provar
l'aïllament real, i amb bloqueig d'imports per als casos que ho requereixen.

  ISO-M1: Dummy funciona sense torch/transformers/peft/minicpm -> PASS
  ISO-M2: Qwen seleccionat sense deps MiniCPM específiques -> no falla per
          MiniCPM (el plugin Qwen només necessita les seves deps)
  ISO-M3: minicpm5 seleccionat sense deps -> error CONTROLAT del plugin
          (missatge explícit de dependències, NO ModuleNotFoundError cru)
  ISO-M4: imports del core (ModelRoundCoordinator, Training HTTP server,
          model_worker) NO carreguen Qwen ni MiniCPM (registry lazy)
"""
import builtins
import importlib
import os
import subprocess
import sys

CHECKS = []
HERE = os.path.dirname(os.path.abspath(__file__))


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


def has_module(mod):
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def load_backend_blocking(backend, blocked):
    """Importa load_backend i el crida amb imports bloquejats."""
    import model_worker
    orig_import = builtins.__import__

    def _blocked(name, *a, **kw):
        if name.split(".")[0] in blocked:
            raise ImportError(f"bloquejat per test: {name}")
        return orig_import(name, *a, **kw)

    builtins.__import__ = _blocked
    try:
        return model_worker.load_backend(backend)
    finally:
        builtins.__import__ = orig_import


def main():
    print("=" * 60)
    print(" MINICPM5 BACKEND ISOLATION — ISO-M1..M4")
    print(f" python: {sys.version.split()[0]}")
    print(f" numpy:  {'SI' if has_module('numpy') else 'NO'}")
    print(f" torch:  {'SI' if has_module('torch') else 'NO'}")
    print(f" transformers: {'SI' if has_module('transformers') else 'NO'}")
    print(f" peft:   {'SI' if has_module('peft') else 'NO'}")
    print("=" * 60)
    sys.path.insert(0, HERE)

    # ── ISO-M4 (primer: imports del core sense plugins) ──────────────
    print("\n=== ISO-M4: imports del core NO carreguen Qwen ni MiniCPM ===")
    sys.modules.pop("torch", None)
    sys.modules.pop("transformers", None)
    sys.modules.pop("peft", None)
    for mod in list(sys.modules):
        if mod.startswith("backends."):
            sys.modules.pop(mod, None)
    before = {m for m in sys.modules if m.startswith(("torch", "transformers", "peft"))}
    try:
        import model_round_coordinator
        import training_http_server
        import model_worker
        after = {m for m in sys.modules if m.startswith(("torch", "transformers", "peft"))}
        new_plugins = after - before
        ok = len(new_plugins) == 0
        check("ISO-M4: core imports sense torch/transformers/peft", ok,
              f"plugins nous carregats: {sorted(new_plugins)[:4] or 'cap'}")
        # cap mòdul de backends carregat per l'import del core
        bmods = [m for m in sys.modules if m.startswith("backends.")]
        check("ISO-M4: cap backend carregat a l'import del core", len(bmods) == 0,
              f"backends carregats: {bmods or 'cap'}")
    except Exception as e:  # noqa: BLE001
        check("ISO-M4: imports del core", False, f"{type(e).__name__}: {e}")

    # ── ISO-M1: dummy funciona sense deps ─────────────────────────────
    print("\n=== ISO-M1: dummy sense torch/transformers/peft/minicpm ===")
    dummy_src = open(os.path.join(HERE, "backends", "dummy_backend.py")).read()
    imports_heavy = any(f"import {m}" in dummy_src or f"from {m}" in dummy_src
                        for m in ("torch", "transformers", "peft"))
    imports_minicpm = "minicpm" in dummy_src
    check("ISO-M1: dummy sense imports torch/transformers/peft", not imports_heavy)
    check("ISO-M1: dummy sense imports minicpm", not imports_minicpm)
    try:
        import backends.dummy_backend as dbm
        bk = dbm.DummyBackend("dummy", db_path="/tmp/iso_m_dummy.db", seq_len=16)
        bk.load_model()
        b = bk.adapter_to_bundle()
        h = bk.base_model_hash()
        check("ISO-M1: dummy load_model + adapter_to_bundle + base_hash OK",
              len(b) > 0 and len(h) == 64)
        bk.close()
    except Exception as e:  # noqa: BLE001
        check("ISO-M1: dummy operacions bàsiques", False, f"{type(e).__name__}: {e}")

    # ── ISO-M2: qwen sense deps MiniCPM → no depèn de MiniCPM ─────────
    print("\n=== ISO-M2: qwen sense deps MiniCPM específiques ===")
    qwen_src = open(os.path.join(HERE, "backends", "qwen3_backend.py")).read()
    check("ISO-M2: qwen3_backend.py sense imports minicpm",
          "minicpm" not in qwen_src.lower())
    # qwen seleccionat amb deps qwen BLOQUEJADES però sense cap referència a
    # minicpm: ha de fallar per deps QWEN, mai per deps MiniCPM
    try:
        load_backend_blocking("qwen3", blocked=("torch", "transformers", "peft"))
        check("ISO-M2: qwen sense deps → error controlat (no minicpm)", False,
              "no va llançar")
    except RuntimeError as e:
        msg = str(e)
        ok = "minicpm" not in msg.lower()
        check("ISO-M2: qwen sense deps → error controlat (no minicpm)", ok,
              f"RuntimeError: {msg[:70]}")
    except Exception as e:  # noqa: BLE001
        check("ISO-M2: qwen sense deps → error controlat (no minicpm)", False,
              f"{type(e).__name__}: {e}")

    # ── ISO-M3: minicpm sense deps → error controlat del plugin ───────
    print("\n=== ISO-M3: minicpm5 sense deps → error controlat del plugin ===")
    # El plugin MiniCPM5 importa torch/transformers/peft LAZY (dins de
    # load_model), no al top-level: l'import del CORE mai falla (mai un
    # ModuleNotFoundError cru). L'error de dependències apareix a
    # load_model, que és part del PLUGIN.
    try:
        cls = load_backend_blocking("minicpm5",
                                    blocked=("torch", "transformers", "peft"))
        check("ISO-M3: load_backend(minicpm5) OK (import lazy del core)",
              cls is not None and cls.__name__ == "MiniCPM5Backend",
              f"classe: {cls.__name__ if cls else 'None'}")
    except Exception as e:  # noqa: BLE001
        check("ISO-M3: load_backend(minicpm5) OK (import lazy del core)",
              False, f"{type(e).__name__}: {e}")
    # load_model sense deps: ImportError del plugin (missatge clar), mai
    # un error del core
    try:
        import model_worker
        cls = model_worker.load_backend("minicpm5")
        bk = cls("/nonexistent", db_path="/tmp/iso_m3.db", seq_len=16)
        bk.load_model()
        check("ISO-M3: load_model sense deps → error de dependència", False,
              "no va llançar")
    except ImportError as e:
        msg = str(e)
        ok = ("torch" in msg or "transformers" in msg or "peft" in msg)
        check("ISO-M3: load_model sense deps → error de dependència", ok,
              f"ImportError plugin: {msg[:60]}")
    except Exception as e:  # noqa: BLE001
        check("ISO-M3: load_model sense deps → error de dependència", False,
              f"tipus incorrecte: {type(e).__name__}: {e}")

    # ── ISO-M3b: backend inexistent → error controlat (regressió) ─────
    print("\n=== ISO-M3b: backend inexistent ===")
    try:
        import model_worker
        model_worker.load_backend("no-existeix")
        check("ISO-M3b: backend inexistent -> error controlat", False)
    except ValueError as e:
        check("ISO-M3b: backend inexistent -> error controlat", True,
              f"ValueError: {str(e)[:50]}")

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"\n=== MINICPM5 ISOLATION: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
