"""Puente Python → motor C++ v2 PoC (importlib; NO registra en engine_registry)."""
from __future__ import annotations

import glob
import importlib.util
import os

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))


def v2_engine_paths() -> list[str]:
    names = (
        "algorithm_cpp_v2.cp314-win_amd64.pyd",
        "algorithm_cpp_v2.cp313-win_amd64.pyd",
        "algorithm_cpp_v2.pyd",
    )
    out: list[str] = []
    for folder in (
        _ENGINE_DIR,
        os.path.join(_ENGINE_DIR, "cpp_v2", "build", "Release"),
        os.path.join(_ENGINE_DIR, "cpp_v2", "build"),
    ):
        for name in names:
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                out.append(p)
    return out


_cpp_v2_mod = None


def load_cpp_v2():
    """Carga algorithm_cpp_v2.pyd vía importlib (patrón LAB SIMULATOR)."""
    global _cpp_v2_mod
    if _cpp_v2_mod is not None:
        return _cpp_v2_mod

    # Windows: dependencias del .pyd (MSVC runtime / vecinos) requieren
    # add_dll_directory; sin esto falla con "DLL load failed".
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(_ENGINE_DIR)
        except OSError:
            pass
        build_release = os.path.join(_ENGINE_DIR, "cpp_v2", "build", "Release")
        if os.path.isdir(build_release):
            try:
                os.add_dll_directory(build_release)
            except OSError:
                pass
        # CUDA Toolkit instalado por winget no siempre actualiza PATH de la
        # sesión Python. Registrar sus DLL dinámicas evita depender de una
        # GPU/versión específica al cargar el módulo opcional.
        cuda_roots = [os.environ.get("CUDA_PATH", "")]
        cuda_roots.extend(
            glob.glob(
                r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*"
            )
        )
        for root in cuda_roots:
            cuda_bin = os.path.join(root, "bin")
            if os.path.isdir(cuda_bin):
                try:
                    os.add_dll_directory(cuda_bin)
                except OSError:
                    pass

    last_err: Exception | None = None
    for path in v2_engine_paths():
        try:
            spec = importlib.util.spec_from_file_location("algorithm_cpp_v2", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _cpp_v2_mod = mod
                return mod
        except Exception as exc:  # noqa: BLE001 — probar siguiente artefacto
            last_err = exc
            continue
    hint = (
        "algorithm_cpp_v2 no compilado. Ejecuta:\n"
        "  modules\\nesting_engine\\build_cpp_v2_engine.ps1"
    )
    if last_err is not None:
        raise ImportError(f"{hint}\nÚltimo error: {last_err}") from last_err
    raise ImportError(hint)


def engine_name() -> str:
    return getattr(load_cpp_v2(), "ENGINE_NAME", "cpp_v2_unknown")


def echo_rings(rings):
    return load_cpp_v2().echo_rings(rings)


def polygons_overlap(rings_a, rings_b) -> bool:
    return bool(load_cpp_v2().polygons_overlap(rings_a, rings_b))


def compute_nfp_outer(rings_a, rings_b):
    return load_cpp_v2().compute_nfp_outer(rings_a, rings_b)


def compute_nfp_outer_cached(
    rings_a,
    rings_b,
    *,
    angle_a_deg: float = 0.0,
    angle_b_deg: float = 0.0,
    kerf_mm: float = 0.0,
):
    """NFP outer con caché L1 canónica del módulo v2."""
    return load_cpp_v2().compute_nfp_outer_cached(
        rings_a,
        rings_b,
        float(angle_a_deg),
        float(angle_b_deg),
        float(kerf_mm),
    )


def nfp_cache_stats() -> dict:
    """Hits, misses, evictions, entries, capacity y hit_rate del caché L1."""
    return dict(load_cpp_v2().nfp_cache_stats())


def reset_nfp_cache() -> None:
    """Limpia entradas y contadores del caché NFP L1."""
    load_cpp_v2().reset_nfp_cache()


def set_nfp_cache_capacity(capacity: int) -> None:
    load_cpp_v2().set_nfp_cache_capacity(int(capacity))


def run_nfp_cache_workload(
    piezas,
    *,
    iterations: int = 1,
    kerf_mm: float = 0.0,
) -> dict:
    """Mide lookups NFP con geometría canónica preparada una vez por lote."""
    return dict(
        load_cpp_v2().run_nfp_cache_workload(
            piezas,
            int(iterations),
            float(kerf_mm),
        )
    )


def cuda_raster_filter_available() -> bool:
    """True si el build v2 incluye CUDA y detecta una GPU utilizable."""
    return bool(load_cpp_v2().cuda_raster_filter_available())


def cuda_raster_filter_status() -> str:
    """Diagnóstico breve de disponibilidad CUDA."""
    return str(load_cpp_v2().cuda_raster_filter_status())


def create_cuda_raster_session(
    fixed_inner,
    fixed_w: int,
    fixed_h: int,
    *,
    prefer_cuda: bool = True,
):
    """Sesión raster que reutiliza la máscara fija entre lotes de semillas."""
    return load_cpp_v2().CudaRasterSession(
        fixed_inner,
        int(fixed_w),
        int(fixed_h),
        bool(prefer_cuda),
    )


def cuda_raster_safe_reject_batch(
    fixed_inner,
    fixed_w: int,
    fixed_h: int,
    candidate_inner,
    candidate_w: int,
    candidate_h: int,
    offsets,
    *,
    prefer_cuda: bool = True,
) -> dict:
    """Filtro raster fail-open: solo rechazos demostrados; Clipper valida el resto."""
    return dict(
        load_cpp_v2().cuda_raster_safe_reject_batch(
            fixed_inner,
            int(fixed_w),
            int(fixed_h),
            candidate_inner,
            int(candidate_w),
            int(candidate_h),
            offsets,
            bool(prefer_cuda),
        )
    )


def cuda_raster_screen_population(
    fixed_inner,
    fixed_w: int,
    fixed_h: int,
    candidate_inner,
    candidate_w: int,
    candidate_h: int,
    offset_batches,
    *,
    prefer_cuda: bool = True,
) -> dict:
    """
    Criba una población de semillas en una sola llamada C++.

    `offset_batches` es list[list[tuple[int,int]]] (una lista de offsets por
    semilla). Sube la máscara fija y la candidata una vez; solo transfiere
    offsets/resultados por lote. No sustituye Clipper2.
    """
    return dict(
        load_cpp_v2().cuda_raster_screen_population(
            fixed_inner,
            int(fixed_w),
            int(fixed_h),
            candidate_inner,
            int(candidate_w),
            int(candidate_h),
            offset_batches,
            bool(prefer_cuda),
        )
    )


def empaquetar_una_hoja_poc(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.15,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_rings=None,
):
    """Empaque PoC S0. `piezas` = list[dict] nativo (rings/marks), no Shapely."""
    return load_cpp_v2().empaquetar_una_hoja_poc(
        piezas,
        w_placa,
        h_placa,
        kerf_override,
        margin_override,
        opt_override,
        corner_override,
        limite_rings,
    )
