#!/usr/bin/env python3
"""backend_isolation.py — TEST D'ISOLAMENT DE BACKENDS (R2.2, punt 2).

Prova que el core genèric NO depèn de cap plugin (especialment Qwen):

  BACKEND-ISO-01: dummy funciona en un entorn SENSE transformers/peft/torch
                  (el core + dummy només necessiten numpy).
  BACKEND-ISO-02: backend inexistent -> error controlat (ValueError clar).
  BACKEND-ISO-03: qwen3 seleccionat sense dependències -> missatge EXPLÍCIT
                  de dependència del plugin, no error d'import del core.

Ús: python3 backend_isolation.py   (python del SISTEMA, no el venv Qwen)

Aquest script s'executa amb el Python del sistema (sense torch/transformers).
"""
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


def main():
    print("=" * 60)
    print(" BACKEND ISOLATION — el core no depèn de Qwen")
    print(f" python: {sys.version.split()[0]}")
    print(f" numpy:  {'SI' if has_module('numpy') else 'NO'}")
    print(f" torch:  {'SI' if has_module('torch') else 'NO'}")
    print(f" transformers: {'SI' if has_module('transformers') else 'NO'}")
    print(f" peft:   {'SI' if has_module('peft') else 'NO'}")
    print("=" * 60)

    # ── prerequisit: aquest entorn NO ha de tenir les deps de Qwen ──
    # (si les té, avisem però seguim: el que cal provar és que dummy no les
    #  IMPORTI; si l'entorn les té, la prova d'absència d'import és més feble)
    env_has_qwen_deps = has_module("torch") or has_module("transformers") or has_module("peft")
    if env_has_qwen_deps:
        print("  [entorn amb deps Qwen: SI — prova d'import feble]")
    else:
        print("  [entorn amb deps Qwen: NO — aïllament real]")

    # ── BACKEND-ISO-02: backend inexistent -> error controlat ──
    print("\n=== BACKEND-ISO-02: backend inexistent ===")
    sys.path.insert(0, HERE)
    from model_worker import load_backend
    try:
        load_backend("noexisteix")
        check("ISO-02 backend inexistent -> error controlat", False, "no va llançar")
    except ValueError as e:
        check("ISO-02 backend inexistent -> error controlat", True,
              f"ValueError: {str(e)[:60]}")
    except Exception as e:  # noqa: BLE001
        check("ISO-02 backend inexistent -> error controlat", False,
              f"tipus incorrecte: {type(e).__name__}")

    # ── BACKEND-ISO-03: qwen3 sense deps -> missatge explícit ──
    print("\n=== BACKEND-ISO-03: qwen3 sense dependències ===")
    if env_has_qwen_deps:
        print("  [l'entorn TÉ torch/transformers: la prova d'import falla"
              " per una altra via — simulem bloquejant l'import]")
        # simulem l'absència: bloquejar els mòduls Qwen abans del load
        import importlib
        import builtins
        orig_import = builtins.__import__

        def _blocked(name, *a, **kw):
            if name.split(".")[0] in ("torch", "transformers", "peft"):
                raise ImportError(f"bloquejat per test: {name}")
            return orig_import(name, *a, **kw)

        builtins.__import__ = _blocked
        try:
            try:
                load_backend("qwen3")
                check("ISO-03 qwen3 sense deps -> missatge explícit", False,
                      "no va llançar")
            except RuntimeError as e:
                msg = str(e)
                ok = ("dependències no disponibles" in msg
                      or "plugin" in msg)
                check("ISO-03 qwen3 sense deps -> missatge explícit", ok,
                      f"RuntimeError: {msg[:70]}")
            except Exception as e:  # noqa: BLE001
                check("ISO-03 qwen3 sense deps -> missatge explícit", False,
                      f"tipus incorrecte: {type(e).__name__}: {e}")
        finally:
            builtins.__import__ = orig_import
    else:
        try:
            load_backend("qwen3")
            check("ISO-03 qwen3 sense deps -> missatge explícit", False,
                  "no va llançar (el plugin es va importar?)")
        except RuntimeError as e:
            msg = str(e)
            ok = ("dependències no disponibles" in msg or "plugin" in msg)
            check("ISO-03 qwen3 sense deps -> missatge explícit", ok,
                  f"RuntimeError: {msg[:70]}")
        except ImportError as e:
            check("ISO-03 qwen3 sense deps -> missatge explícit", False,
                  f"ImportError cru (no embolicat): {e}")

    # ── BACKEND-ISO-01: dummy funciona sense deps Qwen ──
    print("\n=== BACKEND-ISO-01: dummy sense transformers/peft/torch ===")
    if env_has_qwen_deps:
        print("  [avís: l'entorn té deps Qwen; provem l'ABSÈNCIA d'import]")
    sys.path.insert(0, HERE)
    # 1) el mòdul dummy NO ha d'importar torch/transformers/peft
    import importlib
    dummy_mod = importlib.import_module("backends.dummy_backend")
    src = open(os.path.join(HERE, "backends", "dummy_backend.py")).read()
    imports_qwen = any(f"import {m}" in src or f"from {m}" in src
                       for m in ("torch", "transformers", "peft"))
    check("ISO-01 dummy: codi sense imports torch/transformers/peft",
          not imports_qwen)
    # 2) instanciar + load_model + adapter_to_bundle (operacions bàsiques)
    try:
        bk = dummy_mod.DummyBackend("dummy", db_path="/tmp/iso_dummy.db",
                                    seq_len=16)
        bk.load_model()
        b = bk.adapter_to_bundle()
        h = bk.base_model_hash()
        check("ISO-01 dummy: load_model + adapter_to_bundle + base_hash OK",
              len(b) > 0 and len(h) == 64)
        bk.close()
    except Exception as e:  # noqa: BLE001
        check("ISO-01 dummy: operacions bàsiques", False, f"{type(e).__name__}: {e}")

    # 3) e2e dummy amb el mateix intèrpret (subprocess) si no hi ha deps Qwen
    if not env_has_qwen_deps:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "generic_distributed_run.py"),
             "--backend", "dummy", "--num-examples", "2", "--seq-len", "16"],
            capture_output=True, text=True, timeout=300, cwd=HERE)
        out = r.stdout + r.stderr
        # R2.3: el dummy e2e ara té 17/17 checks (2 nous de round/assignments)
        ok = r.returncode == 0 and ("15/15 PASS" in out or "17/17 PASS" in out)
        check("ISO-01 dummy: e2e complet sense deps Qwen", ok,
              f"exit={r.returncode} {out.strip()[-80:]}")
    else:
        print("  [e2e dummy saltat: l'entorn té deps Qwen — la prova"
              " d'absència d'import és la vàlida aquí]")

    npass = sum(1 for _, ok in CHECKS if ok)
    print(f"\n=== BACKEND ISOLATION: {npass}/{len(CHECKS)} PASS ===")
    sys.exit(0 if npass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
