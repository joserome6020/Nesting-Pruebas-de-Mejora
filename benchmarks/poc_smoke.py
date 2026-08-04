"""Smoke tests del PoC C++ v2: round-trip, overlap, NFP y pack mínimo."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    from modules.nesting_engine.algorithm_bridge_v2 import (
        compute_nfp_outer,
        compute_nfp_outer_cached,
        cuda_raster_safe_reject_batch,
        echo_rings,
        empaquetar_una_hoja_poc,
        engine_name,
        nfp_cache_stats,
        polygons_overlap,
        reset_nfp_cache,
        set_nfp_cache_capacity,
    )

    print("engine:", engine_name())
    a = [[(0.0, 0.0), (50.0, 0.0), (50.0, 40.0), (0.0, 40.0)]]
    b = [[(10.0, 10.0), (30.0, 10.0), (30.0, 25.0), (10.0, 25.0)]]
    echoed = echo_rings(a)
    assert echoed and len(echoed[0]) >= 3, "echo_rings falló"
    assert polygons_overlap(a, a) is True
    assert polygons_overlap(a, [[(1000.0, 1000.0), (1010.0, 1000.0), (1010.0, 1010.0)]]) is False
    nfp = compute_nfp_outer(a, b)
    assert nfp and len(nfp[0]) >= 3, "compute_nfp_outer vacío"

    reset_nfp_cache()
    cached_nfp = compute_nfp_outer_cached(a, b, kerf_mm=6.35)
    assert cached_nfp and len(cached_nfp[0]) >= 3, "NFP cache miss devolvió vacío"
    # Misma geometría con otro punto inicial y orientación: debe acertar cache.
    a_reordered = [[(50.0, 0.0), (50.0, 40.0), (0.0, 40.0), (0.0, 0.0)]]
    b_reversed = [[(10.0, 25.0), (30.0, 25.0), (30.0, 10.0), (10.0, 10.0)]]
    compute_nfp_outer_cached(a_reordered, b_reversed, kerf_mm=6.35)
    cache = nfp_cache_stats()
    assert cache["misses"] == 1 and cache["hits"] == 1, f"cache canónica inválida: {cache}"
    compute_nfp_outer_cached(a, b, kerf_mm=7.0)
    cache = nfp_cache_stats()
    assert cache["misses"] == 2, f"kerf ausente de llave: {cache}"
    print(f"nfp_cache hits={cache['hits']} misses={cache['misses']}")
    set_nfp_cache_capacity(1)
    reset_nfp_cache()
    compute_nfp_outer_cached(a, b, kerf_mm=6.35)
    compute_nfp_outer_cached(a, b, kerf_mm=7.0)
    cache = nfp_cache_stats()
    assert cache["entries"] == 1 and cache["evictions"] == 1, f"evicción L1 inválida: {cache}"
    set_nfp_cache_capacity(4096)
    reset_nfp_cache()

    # Filtro raster: colisión probada → reject; resto queda para Clipper2.
    raster = cuda_raster_safe_reject_batch(
        [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        4,
        4,
        [1],
        1,
        1,
        [(1, 1), (0, 0)],
        prefer_cuda=False,
    )
    assert raster["rejected"] == [True, False], f"raster conservador inválido: {raster}"

    piezas = [
        {
            "nombre": "A",
            "area": 2000.0,
            "calibre": "11",
            "material": "A36",
            "rings": a,
            "marks": [],
        },
        {
            "nombre": "B",
            "area": 300.0,
            "calibre": "11",
            "material": "A36",
            "rings": [[(0.0, 0.0), (20.0, 0.0), (20.0, 15.0), (0.0, 15.0)]],
            "marks": [],
        },
    ]
    hoja, restos = empaquetar_una_hoja_poc(piezas, 200.0, 100.0, 0.25, 0.1)
    placed = len((hoja or {}).get("piezas") or [])
    print(f"pack placed={placed} restos={len(restos)} efi={hoja.get('eficiencia')}")
    assert placed >= 1, "PoC no colocó ninguna pieza"
    print("poc_smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
