"""Hardware detection + guidance + auto-config (Phase 3, punts 6-8).

Només llegeix info de sistema (/proc, shutil, platform) — no modifica res.
La GPU es detecta com a info, però SIEMPRE es marca EXPERIMENTAL / NOT
CERTIFIED (no hi ha gate GPU certificat al core b146723).
"""
import os
import platform
import shutil
import subprocess


# ── lectors bàsics ─────────────────────────────────────────────────────────
def _read_proc(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def _meminfo() -> dict:
    """Memòria en bytes des de /proc/meminfo (total/available/swap)."""
    info = {"total": 0, "available": 0, "swap_total": 0, "swap_available": 0}
    for line in _read_proc("/proc/meminfo").splitlines():
        parts = line.split(":")
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        val_kb = parts[1].strip().split()[0]
        try:
            kb = int(val_kb)
        except ValueError:
            continue
        if key == "MemTotal":
            info["total"] = kb * 1024
        elif key == "MemAvailable":
            info["available"] = kb * 1024
        elif key == "SwapTotal":
            info["swap_total"] = kb * 1024
        elif key == "SwapFree":
            info["swap_available"] = kb * 1024
    return info


def _cpu_info() -> dict:
    """CPU: model + cores/threads des de /proc/cpuinfo + os.cpu_count."""
    model = ""
    for line in _read_proc("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    return {
        "model": model or platform.processor() or "unknown",
        "cores_physical": max(1, os.cpu_count() or 1),
        "threads": max(1, os.cpu_count() or 1),
    }


def _gpu_info() -> dict:
    """GPU: present/absent + vendor + VRAM (best effort). MAI certifica res."""
    gpu = {"present": False, "vendor": None, "vram_bytes": None, "detail": None}
    # nvidia-smi (CUDA)
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            name, vram_mib = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
            gpu.update(present=True, vendor="nvidia", detail=name,
                       vram_bytes=int(float(vram_mib)) * 1024 * 1024)
            return gpu
    except Exception:
        pass
    # rocm-smi (AMD)
    try:
        out = subprocess.run(["rocm-smi", "--showproductname", "--showmeminfo", "vram"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            gpu.update(present=True, vendor="amd",
                       detail=out.stdout.strip().splitlines()[0][:80])
            return gpu
    except Exception:
        pass
    return gpu


# ── API pública ────────────────────────────────────────────────────────────
def detect_hardware() -> dict:
    """Snapshot complet del sistema (Phase 3, punt 6)."""
    mem = _meminfo()
    cpu = _cpu_info()
    gpu = _gpu_info()
    disk_free = shutil.disk_usage(os.path.abspath(os.curdir)).free
    return {
        "cpu": cpu,
        "memory": {"total_bytes": mem["total"],
                   "available_bytes": mem["available"],
                   "swap_total_bytes": mem["swap_total"],
                   "swap_available_bytes": mem["swap_available"]},
        "disk": {"available_bytes": disk_free},
        "gpu": gpu,
        "os": platform.system() + " " + platform.release(),
        "python": platform.python_version(),
        "device": "cuda" if gpu["vendor"] == "nvidia" else (
            "rocm" if gpu["vendor"] == "amd" else "cpu"),
    }


# ── guidance (punt 7): COMFORTABLE / LIMITED / NOT RECOMMENDED ─────────────
# Llindars conservadors basats en les certificacions reals (f32, CPU):
#   Qwen3-0.6B:      ~2.4 GB per worker carregat + ~1 GB sistema
#   MiniCPM5-1B:     ~4.2 GB per worker carregat + ~1 GB sistema
_MODEL_PROFILES = {
    "qwen3": {"label": "Qwen3-0.6B", "per_worker_gb": 2.6, "certified": True},
    "minicpm5": {"label": "MiniCPM5-1B", "per_worker_gb": 4.4, "certified": True},
}


def model_capability(backend_id: str, available_gb: float) -> dict:
    """Capacitat recomanada d'un model segons RAM disponible (punt 7)."""
    prof = _MODEL_PROFILES.get(backend_id)
    if not prof:
        return {"label": backend_id, "class": "LIMITED", "reason": "desconegut"}
    workers = 2
    need = prof["per_worker_gb"] * workers + 1.5
    if available_gb >= need:
        cls = "COMFORTABLE"
    elif available_gb >= prof["per_worker_gb"] + 1.5:
        cls = "LIMITED"
    else:
        cls = "NOT RECOMMENDED"
    return {"label": prof["label"], "class": cls, "certified": prof["certified"],
            "est_workers": 2 if cls == "COMFORTABLE" else (1 if cls == "LIMITED" else 0)}


def recommended_config(backend_id: str, available_gb: float) -> dict:
    """Configuració recomanada per backend/model segons hardware (punt 8).

    Valors conservadors; l'usuari pot sobreescriure'ls amb Custom.
    """
    prof = _MODEL_PROFILES.get(backend_id, {"per_worker_gb": 2.6})
    cap = model_capability(backend_id, available_gb)
    if cap["class"] == "COMFORTABLE":
        workers, concurrency = 2, 2
    elif cap["class"] == "LIMITED":
        workers, concurrency = 1, 1
    else:
        workers, concurrency = 0, 0
    return {
        "backend_id": backend_id,
        "model": prof["label"],
        "workers": workers,
        "concurrency": concurrency,
        "max_seq_len": 64,          # valor certificat al core (seq_len 64)
        "lora_preset": "safe",
        "memory_warning": (None if workers > 0 else
                           "Not enough available memory for this model."),
    }
