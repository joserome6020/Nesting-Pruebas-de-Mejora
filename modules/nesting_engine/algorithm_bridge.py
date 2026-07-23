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
    cancel_checker=None,
):
    """Motor ARGA FORCE (pizarrón) — C++ packer_base; semillas en paralelo si hay CPU."""
    import random
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _cancelled() -> bool:
        try:
            return bool(cancel_checker and cancel_checker())
        except Exception:
            return False

    if _cancelled():
        return (
            {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0},
            list(piezas or []),
        )

    try:
        from . import algorithm_cpp
    except ImportError as exc:
        raise NestingEngineUnavailableError(_CPP_REQUIRED_MSG) from exc

    if not hasattr(algorithm_cpp, "empaquetar_una_hoja_base"):
        raise NestingEngineUnavailableError(
            "algorithm_cpp.pyd desactualizado (falta empaquetar_una_hoja_base). "
            "Recompila con build_cpp_engine.ps1."
        )

    from .nest_hardware import apply_nest_thread_env, hardware_nest_budget
    from .nest_optimization import get_engine_profile

    budget = hardware_nest_budget()
    apply_nest_thread_env(budget)
    seeds = int(
        get_engine_profile("arga_force").get(
            "force_parallel_seeds", budget["force_parallel_seeds"]
        )
        or 1
    )
    seeds = max(1, min(int(seeds), 8))

    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    def _one_pack(ordered_piezas, seed_idx: int = 0):
        import time as _time

        if _cancelled():
            return {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}, list(
                piezas or []
            )

        t0 = _time.perf_counter()
        print(f"[FORCE] semilla {seed_idx + 1}/{seeds} iniciada…", flush=True)
        native_piezas = [_piece_to_native(p) for p in (ordered_piezas or [])]
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
        # Lookup contra lista original (nombres/rutas), no el orden barajado.
        hoja, restos = _assemble_pack_result(hoja_native, restos_native, piezas)
        n_ok = len(hoja.get("piezas") or [])
        n_rest = len(restos or [])
        print(
            f"[FORCE] semilla {seed_idx + 1}/{seeds} fin · "
            f"colocadas={n_ok} restos={n_rest} · {_time.perf_counter() - t0:.1f}s",
            flush=True,
        )
        return hoja, restos

    base = list(piezas or [])
    if seeds <= 1 or len(base) <= 1:
        return _one_pack(base, 0)

    orders = [base]
    for i in range(1, seeds):
        batch = base.copy()
        random.Random(1000 + i).shuffle(batch)
        batch.sort(key=lambda x: float(x.get("area", 0) or 0), reverse=True)
        orders.append(batch)

    print(
        f"[FORCE] semillas_paralelas={seeds} | threads_budget={budget['nest_threads']} "
        f"| cpus={budget['logical_cpus']} | early_exit_si_completo=1",
        flush=True,
    )

    mejor_hoja = None
    mejor_restos = list(base)
    mejor_score = None

    def _score(hoja, restos):
        return (
            len(hoja.get("piezas") or []),
            float(hoja.get("area_usada", 0) or 0),
            -len(restos or []),
            float(hoja.get("eficiencia", 0) or 0),
        )

    with ThreadPoolExecutor(max_workers=seeds) as pool:
        futs = {
            pool.submit(_one_pack, ord_, idx): idx for idx, ord_ in enumerate(orders)
        }
        for fut in as_completed(futs):
            if _cancelled():
                for other in futs:
                    other.cancel()
                break
            try:
                hoja, restos = fut.result()
            except Exception as exc:
                print(f"[FORCE] semilla error: {exc}", flush=True)
                continue
            sc = _score(hoja, restos)
            if mejor_score is None or sc > mejor_score:
                mejor_score = sc
                mejor_hoja, mejor_restos = hoja, restos
            # Completo: no esperar las otras semillas (reloj ≠ número de hilos).
            if not restos and (hoja.get("piezas") or []):
                print(
                    f"[FORCE] completo en semilla {futs[fut] + 1} · cancelando hermanas",
                    flush=True,
                )
                for other in futs:
                    other.cancel()
                break

    if _cancelled():
        return (
            {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0},
            list(piezas or []),
        )
    if mejor_hoja is None:
        return _one_pack(base, 0)
    return mejor_hoja, mejor_restos


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
    cancel_checker=None,
):
    """Motor Burke BLF + NFP — C++ packer_burke_blf."""
    from .nest_optimization import get_engine_profile

    try:
        if cancel_checker and cancel_checker():
            return (
                {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0},
                list(piezas or []),
            )
    except Exception:
        pass

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
    """
    SVGNest Ultra — prueba fast-first.

    1) Nest rápido: 1 gen, pop chica, 90°, un seed.
    2) Refine corto opcional (pocas gens) solo si quedan restos o profile lo pide.
    3) Continual NestFab solo en renest Accept (no en pack diario).

    Desactivar prueba: ARGA_ULTRA_FAST_FIRST=0
    """
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

    import os
    import time
    from concurrent.futures import ThreadPoolExecutor

    from .nest_engine_context import (
        is_ultra_renest_accept_mode,
        is_ultra_sim_bounded,
        notify_ultra_best_ready,
    )
    from .nest_hardware import apply_nest_thread_env, hardware_nest_budget

    profile = get_engine_profile("svgnest_ultra")
    budget = hardware_nest_budget()
    apply_nest_thread_env(budget)

    env_ff = str(os.environ.get("ARGA_ULTRA_FAST_FIRST", "1")).strip().lower()
    fast_first = bool(profile.get("fast_first", True)) and env_ff not in (
        "0",
        "false",
        "no",
        "off",
    )

    # Cap duro en fast-first: hardware no debe inflar pop a 18+ (mata el tiempo).
    pop_req = int(ga_population or profile.get("ga_population", 12))
    if fast_first:
        pop = max(4, min(pop_req, int(profile.get("fast_first_pop", 8) or 8), 12))
        rot_step = float(
            rotation_step_deg
            if rotation_step_deg is not None
            else profile.get("fast_first_rotation_deg", 90.0)
        )
        first_gens = max(1, int(profile.get("fast_first_gens", 1) if profile.get("fast_first_gens") is not None else 1))
        _rg = profile.get("fast_first_refine_gens", 2)
        refine_gens = max(0, int(0 if _rg is None else _rg))
    else:
        pop = max(4, min(pop_req, 60))
        rot_step = float(rotation_step_deg or profile.get("rotation_step_deg", 30.0))
        first_gens = 1
        refine_gens = max(1, min(int(ga_generations or profile.get("mc_iterations", 6)), 100))

    gens_profile = max(1, min(int(ga_generations or profile.get("mc_iterations", 6)), 100))
    pip = bool(profile.get("part_in_part", True) if part_in_part is None else part_in_part)

    # Sin tilt en prueba rápida.
    prev_tilt = os.environ.pop("ARGA_ULTRA_TILT_DEG", None)

    print(
        f"[ULTRA-HW] fast_first={int(fast_first)} cpus={budget['logical_cpus']} "
        f"threads={budget['nest_threads']} pop={pop} rot={rot_step} "
        f"first_gens={first_gens} refine_gens={refine_gens}",
        flush=True,
    )

    renest_accept = is_ultra_renest_accept_mode() and not is_ultra_sim_bounded()
    if renest_accept and cancel_checker is None:
        try:
            from .manager import _active_pack_cancel_checker

            cancel_checker = _active_pack_cancel_checker()
        except Exception:
            cancel_checker = None

    native_piezas = [_piece_to_native(p) for p in (piezas or [])]
    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    def _cancelled() -> bool:
        try:
            return bool(cancel_checker and cancel_checker())
        except Exception:
            return False

    def _run_cpp(
        generations: int,
        seed: int,
        *,
        pop_n: int | None = None,
        rot: float | None = None,
        use_pip: bool | None = None,
        seed_order=None,
    ):
        args = dict(
            piezas=native_piezas,
            w_placa=w_placa,
            h_placa=h_placa,
            kerf_override=kerf_override,
            margin_override=margin_override,
            opt_override=opt_override,
            corner_override=corner_override,
            limite_rings=limite_rings,
            ga_population=int(pop_n if pop_n is not None else pop),
            ga_generations=int(generations),
            rotation_step_deg=float(rot if rot is not None else rot_step),
            part_in_part=bool(pip if use_pip is None else use_pip),
            ga_seed=int(seed),
        )
        try:
            raw = algorithm_cpp.empaquetar_una_hoja_svgnest_ultra(
                **args,
                seed_order=list(seed_order) if seed_order else None,
            )
        except TypeError:
            raw = algorithm_cpp.empaquetar_una_hoja_svgnest_ultra(**args)
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            hoja_native, restos_native, orden_native = raw[0], raw[1], raw[2]
        else:
            hoja_native, restos_native = raw[0], raw[1]
            orden_native = []
        hoja, restos = _assemble_pack_result(hoja_native, restos_native, piezas)
        orden = [int(x) for x in (orden_native or [])]
        return hoja, restos, orden

    try:
        # --- Pack diario / SIM: fast-first (sin bucle infinito) ---
        if not renest_accept:
            t0 = time.perf_counter()
            hoja, restos, orden = _run_cpp(first_gens, 1)
            t_first = time.perf_counter() - t0
            mejor_h, mejor_r, mejor_o = hoja, restos, orden

            # Refine corto SOLO si hay restos y presupuesto > 0.
            do_refine = (
                refine_gens > 0
                and bool(mejor_o)
                and bool(mejor_h.get("piezas"))
                and (bool(mejor_r) or not fast_first)
            )
            if do_refine and not _cancelled():
                # En fast-first: refine acotado; sin fast-first usa gens_profile.
                rg = refine_gens if fast_first else max(refine_gens, gens_profile)
                h2, r2, o2 = _run_cpp(rg, 2, seed_order=mejor_o)
                if _svgnest_is_better(h2, r2, mejor_h, mejor_r):
                    mejor_h, mejor_r, mejor_o = h2, r2, o2

            print(
                f"[ULTRA-FAST] first={t_first:.2f}s total={time.perf_counter()-t0:.2f}s "
                f"placed={len(mejor_h.get('piezas') or [])} restos={len(mejor_r or [])} "
                f"pop={pop} rot={rot_step} refine={int(do_refine)}",
                flush=True,
            )
            return mejor_h, mejor_r

        # --- Renest Accept: continual ligero hasta Cancel ---
        mejor_hoja = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
        mejor_restos = list(piezas or [])
        mejor_orden: list[int] | None = None
        notified_best = False
        mejoras = 0
        n_in = len(piezas or [])

        def _is_complete(hoja, restos) -> bool:
            return len(restos or []) == 0 and len(hoja.get("piezas") or []) >= max(1, n_in)

        def _efi_pct(hoja) -> float:
            efi = float(hoja.get("eficiencia", 0.0) or 0.0)
            return efi if efi > 1.5 else efi * 100.0

        def _consider(hoja, restos, orden=None) -> bool:
            nonlocal mejor_hoja, mejor_restos, mejor_orden
            if not (hoja and (hoja.get("piezas") or [])):
                return False
            if _svgnest_is_better(hoja, restos, mejor_hoja, mejor_restos):
                mejor_hoja, mejor_restos = hoja, restos
                if orden and len(orden) == n_in:
                    mejor_orden = list(orden)
                return True
            return False

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            seed = 1
            while not _cancelled():
                refining = bool(mejor_orden and _is_complete(mejor_hoja, mejor_restos))
                batch = 2 if refining else first_gens
                seed_ord = mejor_orden if refining else None
                fut = pool.submit(_run_cpp, batch, seed, seed_order=seed_ord)
                while not fut.done():
                    if _cancelled():
                        break
                    time.sleep(0.1)
                if _cancelled() and not fut.done():
                    break
                if fut.done():
                    try:
                        hoja, restos, orden = fut.result()
                    except Exception as exc:
                        print(f"[ULTRA-RENEST] fail: {exc}", flush=True)
                        seed += 1
                        continue
                    improved = _consider(hoja, restos, orden)
                    if _is_complete(mejor_hoja, mejor_restos):
                        if improved and notified_best:
                            mejoras += 1
                        notify_ultra_best_ready(
                            f"{len(mejor_hoja.get('piezas') or [])} pzas · "
                            f"{_efi_pct(mejor_hoja):.1f}% · mejoras={mejoras}"
                        )
                        notified_best = True
                seed += 1
        finally:
            pool.shutdown(wait=False, cancel_futures=False)
        return mejor_hoja, mejor_restos
    finally:
        if prev_tilt is not None:
            os.environ["ARGA_ULTRA_TILT_DEG"] = prev_tilt


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

    profile = get_engine_profile("arga_lite")
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


def _lite_pieza_xy(pieza_hoja: dict) -> tuple[float, float]:
    """Esquina inferior-izquierda aproximada de una pieza ya colocada."""
    try:
        rings = pieza_hoja.get("poligonos") or []
        if rings and rings[0]:
            xs = [float(p[0]) for p in rings[0]]
            ys = [float(p[1]) for p in rings[0]]
            return (min(xs), min(ys))
    except Exception:
        pass
    return (0.0, 0.0)


def _lite_orden_desde_mejor(hoja: dict, restos: list, pool_src: list) -> list:
    """
    Orden tipo Ultra seed: piezas colocadas (izq→der / abajo→arriba) y luego restos.
    Sirve de base del siguiente pase MC aunque C++ reordene por clase/área.
    """
    lookup = _build_piece_lookup_lists(pool_src)
    ordenadas: list = []

    colocadas = sorted(
        list(hoja.get("piezas") or []),
        key=lambda pz: _lite_pieza_xy(pz),
    )
    for pz in colocadas:
        restored = _piece_from_native_rest(
            {"nombre": str(pz.get("nombre") or "")},
            lookup,
        )
        if restored is not None:
            ordenadas.append(restored)

    for r in restos or []:
        nom = str(r.get("nombre") or "")
        bucket = lookup.get(nom)
        if bucket:
            ordenadas.append(copy.deepcopy(bucket.pop(0)))
        else:
            # Fallback: objeto resto ya es pieza pack-ready
            ordenadas.append(copy.deepcopy(r))

    # Por si faltó alguna del pool original
    for nom, bucket in lookup.items():
        while bucket:
            ordenadas.append(copy.deepcopy(bucket.pop(0)))
    return ordenadas


def empaquetar_una_hoja_arga_lite(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.3,
    margin_override=0.15,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    mc_iterations=None,
):
    """
    ARGA LITE: MC clásico con 3 pases explore→refine (estilo Ultra light).

    Pase 1: exploración corta. Pases 2–3: parten del mejor orden previo y
    amplían intentos MC; solo se acepta si mejora (piezas / área / restos).
    Sin recompilar C++.
    """
    from .nest_optimization import get_engine_profile

    profile = get_engine_profile("arga_lite")
    pases = int(profile.get("lite_refine_passes", 3) or 3)
    pases = max(1, min(pases, 5))
    if mc_iterations is not None:
        # Compat: si piden iterations explícitas, un solo shot MC.
        iters = max(1, min(int(mc_iterations), 8))
        print(f"[LITE] empaque MC · iterations={iters} (shot único)", flush=True)
        return empaquetar_una_hoja_legacy_mc(
            piezas,
            w_placa,
            h_placa,
            kerf_override=kerf_override,
            margin_override=margin_override,
            opt_override=opt_override,
            corner_override=corner_override,
            limite_poly=limite_poly,
            mc_iterations=iters,
        )

    pool0 = list(piezas or [])
    if not pool0:
        return {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}, []

    mejor_hoja = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    mejor_restos = list(pool0)
    orden_actual = list(pool0)
    mejoras = 0

    print(
        f"[LITE] explore->refine | pases={pases} (base=mejor anterior)",
        flush=True,
    )

    for pase in range(1, pases + 1):
        # Más intentos MC en pases posteriores (1 → 2 → 3 estrategias internas).
        iters_pase = max(1, min(pase, 4))
        print(
            f"[LITE] pase {pase}/{pases} · mc_iters={iters_pase} · "
            f"pool={len(orden_actual)}",
            flush=True,
        )
        hoja, restos = empaquetar_una_hoja_legacy_mc(
            orden_actual,
            w_placa,
            h_placa,
            kerf_override=kerf_override,
            margin_override=margin_override,
            opt_override=opt_override,
            corner_override=corner_override,
            limite_poly=limite_poly,
            mc_iterations=iters_pase,
        )
        if _svgnest_is_better(hoja, restos, mejor_hoja, mejor_restos):
            mejoras += 1
            mejor_hoja, mejor_restos = hoja, restos
            print(
                f"[LITE] mejora pase {pase} · "
                f"colocadas={len(hoja.get('piezas') or [])} "
                f"restos={len(restos or [])} "
                f"efi={float(hoja.get('eficiencia') or 0):.1f}%",
                flush=True,
            )
            if not restos:
                print(f"[LITE] completo en pase {pase} · stop", flush=True)
                break
        else:
            print(f"[LITE] pase {pase} sin mejora · se conserva mejor", flush=True)

        # Siguiente pase nace del mejor actual (orden espacial + restos).
        orden_actual = _lite_orden_desde_mejor(mejor_hoja, mejor_restos, pool0)

    print(
        f"[LITE] fin · mejoras={mejoras}/{pases} · "
        f"colocadas={len(mejor_hoja.get('piezas') or [])} "
        f"restos={len(mejor_restos or [])}",
        flush=True,
    )
    return mejor_hoja, mejor_restos


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
