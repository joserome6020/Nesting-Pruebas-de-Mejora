"""Puente Python → motores de nesting (registro + C++ legacy)."""
from __future__ import annotations

import copy
import os
from collections import Counter

from .nest_engine_context import get_active_engine_id

_ENGINE_NAME = "unknown"
_empaquetar_native = None

_CPP_REQUIRED_MSG = (
    "Motor C++ no disponible. Compila con:\n"
    "  modules\\nesting_engine\\build_cpp_engine.ps1\n"
    "Requiere: Python activo, cmake, pybind11 y MSVC Build Tools."
)


def _rings_from_shapely_polygon(poly):
    if poly is None:
        return []
    if getattr(poly, "is_empty", False):
        return []
    if poly.geom_type == "MultiPolygon":
        polys = [
            g for g in poly.geoms
            if getattr(g, "geom_type", "") == "Polygon" and not g.is_empty
        ]
        if not polys:
            return []
        poly = max(polys, key=lambda g: float(g.area))
    elif poly.geom_type != "Polygon":
        return []
    exterior = list(poly.exterior.coords)
    rings = [exterior]
    for hole in getattr(poly, "interiors", []):
        rings.append(list(hole.coords))
    return rings


def _marks_ring_coords(coords):
    """Normaliza un trazo de marcaje para el motor nativo (mín. 2 vértices)."""
    ring = [(float(x), float(y)) for x, y in coords]
    if len(ring) < 2:
        return None
    if len(ring) == 2:
        ring.append(ring[-1])
    return ring


def _marks_from_shapely(marks):
    if marks is None:
        return []
    try:
        if marks.is_empty:
            return []
    except Exception:
        return []
    out = []
    gtype = getattr(marks, "geom_type", "")
    if gtype == "LineString":
        ring = _marks_ring_coords(marks.coords)
        if ring:
            out.append(ring)
    elif gtype == "MultiLineString":
        for line in marks.geoms:
            ring = _marks_ring_coords(line.coords)
            if ring:
                out.append(ring)
    return out


def _piece_to_native(piece):
    poly = piece["poly"]
    marks = piece.get("marks")
    try:
        area = float(piece.get("area") or poly.area or 0.0)
    except Exception:
        area = 0.0
    return {
        "nombre": str(piece.get("nombre") or ""),
        "area": area,
        "calibre": str(piece.get("calibre") or ""),
        "material": str(piece.get("material") or ""),
        "rings": _rings_from_shapely_polygon(poly),
        "marks": _marks_from_shapely(marks),
    }


def _build_piece_lookup_lists(piezas):
    lookup: dict[str, list] = {}
    for p in piezas or []:
        key = str(p.get("nombre") or "")
        lookup.setdefault(key, []).append(p)
    return lookup


def _piece_from_native_rest(piece_native, lookup_lists):
    key = str(piece_native.get("nombre") or "")
    bucket = lookup_lists.get(key)
    if not bucket:
        return None
    return copy.deepcopy(bucket.pop(0))


class NestingEngineUnavailableError(RuntimeError):
    """El módulo algorithm_cpp.pyd no está compilado o no carga."""


def _resolve_native():
    global _ENGINE_NAME, _empaquetar_native
    if _empaquetar_native is not None:
        return _empaquetar_native, _ENGINE_NAME

    force = str(os.environ.get("ARGA_NESTING_ENGINE", "")).strip().lower()
    if force in {"cython", "python"}:
        raise NestingEngineUnavailableError(
            "ARGA_NESTING_ENGINE=cython/python ya no está soportado. "
            "Todo el empaquetado matemático corre en C++ (algorithm_cpp.pyd)."
        )

    try:
        from . import algorithm_cpp

        _empaquetar_native = algorithm_cpp.empaquetar_una_hoja_mc
        _ENGINE_NAME = getattr(algorithm_cpp, "ENGINE_NAME", "cpp_clipper2")
        return _empaquetar_native, _ENGINE_NAME
    except ImportError as exc:
        raise NestingEngineUnavailableError(_CPP_REQUIRED_MSG) from exc


def engine_name() -> str:
    from .engine_registry import engine_name as registry_engine_name

    try:
        return registry_engine_name(get_active_engine_id())
    except Exception:
        _resolve_native()
        return _ENGINE_NAME


def empaquetar_una_hoja_arga_base(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.3,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
):
    """Motor ARGA Base (pizarrón) — C++ packer_base."""
    try:
        from . import algorithm_cpp
    except ImportError as exc:
        raise NestingEngineUnavailableError(_CPP_REQUIRED_MSG) from exc

    if not hasattr(algorithm_cpp, "empaquetar_una_hoja_base"):
        raise NestingEngineUnavailableError(
            "algorithm_cpp.pyd desactualizado (falta empaquetar_una_hoja_base). "
            "Recompila con build_cpp_engine.ps1."
        )

    native_piezas = [_piece_to_native(p) for p in (piezas or [])]
    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    hoja_native, restos_native = algorithm_cpp.empaquetar_una_hoja_base(
        native_piezas,
        w_placa,
        h_placa,
        kerf_override,
        margin_override,
        opt_override,
        corner_override,
        limite_rings,
    )
    return _assemble_pack_result(hoja_native, restos_native, piezas)


def _assemble_pack_result(hoja_native, restos_native, piezas):
    hoja = {
        "piezas": list(hoja_native.get("piezas", [])),
        "area_usada": float(hoja_native.get("area_usada", 0.0) or 0.0),
        "eficiencia": float(hoja_native.get("eficiencia", 0.0) or 0.0),
    }

    lookup_lists = _build_piece_lookup_lists(piezas)

    restos = []
    for rn in restos_native or []:
        restored = _piece_from_native_rest(rn, lookup_lists)
        if restored is not None:
            restos.append(restored)

    if not restos:
        placed_ctr = Counter(str(x.get("nombre") or "") for x in hoja.get("piezas") or [])
        for p in piezas or []:
            nom = str(p.get("nombre") or "")
            if placed_ctr.get(nom, 0) > 0:
                placed_ctr[nom] -= 1
            else:
                restos.append(copy.deepcopy(p))

    return hoja, restos


def empaquetar_una_hoja_burke_blf(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.3,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    hill_climb_iterations=None,
):
    """Motor Burke BLF + NFP — C++ packer_burke_blf."""
    from .nest_optimization import get_engine_profile

    try:
        from . import algorithm_cpp
    except ImportError as exc:
        raise NestingEngineUnavailableError(_CPP_REQUIRED_MSG) from exc

    if not hasattr(algorithm_cpp, "empaquetar_una_hoja_burke_blf"):
        raise NestingEngineUnavailableError(
            "algorithm_cpp.pyd desactualizado (falta empaquetar_una_hoja_burke_blf). "
            "Recompila con build_cpp_engine.ps1."
        )

    profile = get_engine_profile("burke_blf")
    if hill_climb_iterations is None:
        hill_climb_iterations = int(profile.get("mc_iterations", 10))
    hill_climb_iterations = max(1, min(int(hill_climb_iterations), 50))

    native_piezas = [_piece_to_native(p) for p in (piezas or [])]
    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    hoja_native, restos_native = algorithm_cpp.empaquetar_una_hoja_burke_blf(
        native_piezas,
        w_placa,
        h_placa,
        kerf_override,
        margin_override,
        opt_override,
        corner_override,
        limite_rings,
        hill_climb_iterations,
    )
    return _assemble_pack_result(hoja_native, restos_native, piezas)


    return _assemble_pack_result(hoja_native, restos_native, piezas)


def empaquetar_una_hoja_libnest2d(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.3,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    selector_iterations=None,
):
    """Motor libnest2d-style — NfpPlacer + FirstFit (C++ packer_libnest2d)."""
    from .nest_optimization import get_engine_profile

    try:
        from . import algorithm_cpp
    except ImportError as exc:
        raise NestingEngineUnavailableError(_CPP_REQUIRED_MSG) from exc

    if not hasattr(algorithm_cpp, "empaquetar_una_hoja_libnest2d"):
        raise NestingEngineUnavailableError(
            "algorithm_cpp.pyd desactualizado (falta empaquetar_una_hoja_libnest2d). "
            "Recompila con build_cpp_engine.ps1."
        )

    profile = get_engine_profile("libnest2d")
    if selector_iterations is None:
        selector_iterations = int(profile.get("mc_iterations", 8))
    selector_iterations = max(1, min(int(selector_iterations), 50))

    native_piezas = [_piece_to_native(p) for p in (piezas or [])]
    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    hoja_native, restos_native = algorithm_cpp.empaquetar_una_hoja_libnest2d(
        native_piezas,
        w_placa,
        h_placa,
        kerf_override,
        margin_override,
        opt_override,
        corner_override,
        limite_rings,
        selector_iterations,
    )
    return _assemble_pack_result(hoja_native, restos_native, piezas)


def _svgnest_score(hoja: dict, restos: list) -> tuple:
    placed = len(hoja.get("piezas") or [])
    pending = len(restos or [])
    area = float(hoja.get("area_usada", 0.0) or 0.0)
    efi = float(hoja.get("eficiencia", 0.0) or 0.0)
    return (placed, area, -pending, efi)


def _svgnest_is_better(hoja_a: dict, restos_a: list, hoja_b: dict, restos_b: list) -> bool:
    return _svgnest_score(hoja_a, restos_a) > _svgnest_score(hoja_b, restos_b)


def empaquetar_una_hoja_svgnest_ultra(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.3,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    ga_generations=None,
    ga_population=None,
    rotation_step_deg=None,
    part_in_part=None,
    cancel_checker=None,
):
    """Motor SVGNest Ultra — GA + NFP + rotación fina + optimización continua opcional."""
    from .nest_optimization import get_engine_profile

    try:
        from . import algorithm_cpp
    except ImportError as exc:
        raise NestingEngineUnavailableError(_CPP_REQUIRED_MSG) from exc

    if not hasattr(algorithm_cpp, "empaquetar_una_hoja_svgnest_ultra"):
        raise NestingEngineUnavailableError(
            "algorithm_cpp.pyd desactualizado (falta empaquetar_una_hoja_svgnest_ultra). "
            "Recompila con build_cpp_engine.ps1."
        )

    profile = get_engine_profile("svgnest_ultra")
    pop = max(4, min(int(ga_population or profile.get("ga_population", 30)), 60))
    gens = max(1, min(int(ga_generations or profile.get("mc_iterations", 30)), 100))
    rot_step = float(rotation_step_deg or profile.get("rotation_step_deg", 15.0))
    pip = bool(profile.get("part_in_part", True) if part_in_part is None else part_in_part)
    # Continual NestFab: SOLO si el perfil lo pide. Tener cancel_checker NO basta:
    # el manager lo enlaza también en SIM-PLACA y cada placa candidata se iba
    # a un bucle de minutos (progreso/UI quietos, log sin líneas nuevas).
    continual = bool(cancel_checker) and bool(profile.get("continual_until_user_stops", False))
    stagnation_limit = int(profile.get("continual_stagnation_rounds", 0) or 0)

    native_piezas = [_piece_to_native(p) for p in (piezas or [])]
    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    def _cancelled() -> bool:
        try:
            return bool(cancel_checker and cancel_checker())
        except Exception:
            return False

    def _run_cpp(generations: int, seed: int):
        hoja_native, restos_native = algorithm_cpp.empaquetar_una_hoja_svgnest_ultra(
            native_piezas,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            opt_override,
            corner_override,
            limite_rings,
            pop,
            generations,
            rot_step,
            pip,
            seed,
        )
        return _assemble_pack_result(hoja_native, restos_native, piezas)

    if not continual:
        return _run_cpp(gens, 0)

    mejor_hoja = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    mejor_restos = list(piezas or [])
    seed = 1
    no_improve = 0
    # Batches cortos para reaccionar rápido a Cancelar (estilo NestFab Stop).
    batch = max(1, min(3, gens))

    while True:
        if _cancelled():
            break
        hoja, restos = _run_cpp(batch, seed)
        if _cancelled():
            # Conserva el mejor hallado aunque el batch actual se haya cortado a medias.
            if _svgnest_is_better(hoja, restos, mejor_hoja, mejor_restos):
                mejor_hoja, mejor_restos = hoja, restos
            break
        if _svgnest_is_better(hoja, restos, mejor_hoja, mejor_restos):
            mejor_hoja, mejor_restos = hoja, restos
            no_improve = 0
        else:
            no_improve += 1
        seed += batch
        if stagnation_limit > 0 and no_improve >= stagnation_limit:
            break

    return mejor_hoja, mejor_restos


def empaquetar_una_hoja_legacy_mc(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.3,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    mc_iterations=None,
):
    """Empaque C++ Monte Carlo legacy (referencia / diagnóstico)."""
    from .nest_optimization import get_engine_profile

    profile = get_engine_profile("arga_base")
    if mc_iterations is None:
        mc_iterations = int(profile.get("mc_iterations", 1))
    mc_iterations = max(1, min(int(mc_iterations), 50))

    fn, _engine = _resolve_native()

    native_piezas = [_piece_to_native(p) for p in (piezas or [])]
    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    try:
        hoja_native, restos_native = fn(
            native_piezas,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            opt_override,
            corner_override,
            limite_rings,
            mc_iterations,
        )
    except TypeError as exc:
        raise NestingEngineUnavailableError(
            "algorithm_cpp.pyd desactualizado (falta mc_iterations). "
            "Recompila con build_cpp_engine.ps1."
        ) from exc

    return _assemble_pack_result(hoja_native, restos_native, piezas)


def empaquetar_una_hoja_mc(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.3,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    mc_iterations=None,
    engine_id=None,
    cancel_checker=None,
):
    """Delega al motor activo vía engine_registry."""
    from .engine_registry import empaquetar_una_hoja as registry_pack

    return registry_pack(
        piezas,
        w_placa,
        h_placa,
        kerf_override=kerf_override,
        margin_override=margin_override,
        opt_override=opt_override,
        corner_override=corner_override,
        limite_poly=limite_poly,
        mc_iterations=mc_iterations,
        engine_id=engine_id or get_active_engine_id(),
        cancel_checker=cancel_checker,
    )
