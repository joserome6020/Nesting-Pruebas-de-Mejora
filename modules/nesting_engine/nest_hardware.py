"""Presupuesto de CPU/RAM por máquina para motores de nesting.

No satura a ciegas: deja margen para UI/OS y limita por RAM.
Cada PC del proyecto se auto-escala (i9≠laptop).
"""
from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def hardware_nest_budget() -> dict:
    """
    Returns:
      logical_cpus, nest_threads, ultra_population,
      force_parallel_seeds, plate_pool_workers
    """
    # Override manual opcional (p.ej. ARGA_NEST_THREADS=16)
    override = _env_int("ARGA_NEST_THREADS")
    logical = os.cpu_count() or 4
    if logical < 1:
        logical = 4

    # Reserva UI/OS: 2 en máquinas grandes, 1 en chicas.
    reserve = 2 if logical >= 16 else 1
    threads = override if override and override > 0 else max(2, logical - reserve)

    ram_gb = _ram_gb()
    # ~0.45 GB de pico por worker Ultra pesado (observado ~0.5–0.7 GB/proceso).
    max_by_ram = max(4, int(ram_gb / 0.45)) if ram_gb > 0 else threads
    threads = max(2, min(int(threads), int(max_by_ram), 48))

    # Ultra: población ≈ hilos útiles (tope 48; mínimo perfil 12).
    ultra_pop = max(12, min(48, threads))

    # FORCE: cada semilla = 1 pack C++ completo (caro con anillos/PIP).
    # Default 4: best-of-N sin llegar a 7 (reloj ≈ semilla más lenta).
    # Override: ARGA_FORCE_SEEDS=1..8 (p.ej. 7 si hace falta más efi).
    force_override = _env_int("ARGA_FORCE_SEEDS")
    if force_override is not None and force_override > 0:
        force_seeds = max(1, min(8, force_override))
    else:
        force_seeds = 4

    # Jobs multi-placa: no reventar RAM (cada placa Ultra es cara).
    plate_pool = 1 if threads < 8 else max(1, min(6, threads // 8))

    return {
        "logical_cpus": int(logical),
        "ram_gb": float(ram_gb),
        "nest_threads": int(threads),
        "ultra_population": int(ultra_pop),
        "force_parallel_seeds": int(force_seeds),
        "plate_pool_workers": int(plate_pool),
    }


def apply_nest_thread_env(budget: dict | None = None) -> None:
    """Alinea env leído por C++ (ARGA_NEST_OMP_THREADS) al presupuesto."""
    b = budget or hardware_nest_budget()
    n = str(int(b["nest_threads"]))
    os.environ["ARGA_NEST_OMP_THREADS"] = n


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _ram_gb() -> float:
    try:
        import psutil  # type: ignore

        return float(psutil.virtual_memory().total) / (1024.0 ** 3)
    except Exception:
        pass
    # Windows fallback
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return float(stat.ullTotalPhys) / (1024.0 ** 3)
    except Exception:
        pass
    return 8.0
