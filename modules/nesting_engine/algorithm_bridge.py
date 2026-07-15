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
    """Motor ARGA FORCE (pizarrón) — C++ packer_base; semillas en paralelo si hay CPU."""
    import random
    from concurrent.futures import ThreadPoolExecutor, as_completed

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
    pop = max(4, min(int(ga_population or profile.get("ga_population", 30)), 60))
    gens = max(1, min(int(ga_generations or profile.get("mc_iterations", 30)), 100))
    rot_step = float(rotation_step_deg or profile.get("rotation_step_deg", 15.0))
    pip = bool(profile.get("part_in_part", True) if part_in_part is None else part_in_part)
    print(
        f"[ULTRA-HW] cpus={budget['logical_cpus']} ram={budget['ram_gb']:.1f}GB "
        f"threads={budget['nest_threads']} pop={pop} gens={gens}",
        flush=True,
    )
    # Continual NestFab:
    # - perfil continual_until_user_stops (OFF por defecto), o
    # - renesteo Ultra con «Aceptar mejor actual» (solo Ultra; sin semilla FORCE).
    # Nunca en SIM-PLACA (sim_bounded): si no, cada candidata se queda en bucle.
    renest_accept = is_ultra_renest_accept_mode() and not is_ultra_sim_bounded()
    if renest_accept and cancel_checker is None:
        try:
            from .manager import _active_pack_cancel_checker

            cancel_checker = _active_pack_cancel_checker()
        except Exception:
            cancel_checker = None
    continual = (
        renest_accept
        or (
            bool(cancel_checker)
            and bool(profile.get("continual_until_user_stops", False))
            and not is_ultra_sim_bounded()
        )
    )
    stagnation_limit = int(profile.get("continual_stagnation_rounds", 0) or 0)
    if renest_accept:
        # Solo UX: no degradar calidad Ultra (pop/rot/PIP = perfil completo).
        stagnation_limit = 0

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
        raw = algorithm_cpp.empaquetar_una_hoja_svgnest_ultra(
            native_piezas,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            opt_override,
            corner_override,
            limite_rings,
            int(pop_n if pop_n is not None else pop),
            generations,
            float(rot if rot is not None else rot_step),
            bool(pip if use_pip is None else use_pip),
            seed,
            list(seed_order) if seed_order else None,
        )
        # Compat: builds viejos (hoja, restos) vs nuevos (hoja, restos, orden).
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            hoja_native, restos_native, orden_native = raw[0], raw[1], raw[2]
        else:
            hoja_native, restos_native = raw[0], raw[1]
            orden_native = []
        hoja, restos = _assemble_pack_result(hoja_native, restos_native, piezas)
        orden = [int(x) for x in (orden_native or [])]
        return hoja, restos, orden

    if not continual:
        hoja, restos, _ord = _run_cpp(gens, 0)
        return hoja, restos

    mejor_hoja = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    mejor_restos = list(piezas or [])
    mejor_orden: list[int] | None = None
    notified_best = False
    mejoras = 0  # veces que el mejor completo mejoró (NestFab: progreso visible)
    n_in = len(piezas or [])

    def _is_complete(hoja, restos) -> bool:
        n_ok = len(hoja.get("piezas") or [])
        n_rest = len(restos or [])
        return n_rest == 0 and n_ok >= max(1, n_in)

    def _efi_pct(hoja) -> float:
        efi = float(hoja.get("eficiencia", 0.0) or 0.0)
        # C++ Ultra ya devuelve %; otros caminos pueden devolver 0–1.
        return efi if efi > 1.5 else efi * 100.0

    def _consider(hoja, restos, orden=None) -> bool:
        """Actualiza mejor. True si mejoró."""
        nonlocal mejor_hoja, mejor_restos, mejor_orden
        if not (hoja and (hoja.get("piezas") or [])):
            return False
        if _svgnest_is_better(hoja, restos, mejor_hoja, mejor_restos):
            mejor_hoja, mejor_restos = hoja, restos
            if orden and len(orden) == n_in:
                mejor_orden = list(orden)
            return True
        return False

    def _publish_best(*, label: str = "") -> None:
        """Habilita Aceptar + contador de mejoras (solo nest COMPLETO)."""
        nonlocal notified_best
        if not _is_complete(mejor_hoja, mejor_restos):
            return
        n_pz = len(mejor_hoja.get("piezas") or [])
        pref = f"{label} · " if label else ""
        notify_ultra_best_ready(
            f"{pref}{n_pz} pzas · {_efi_pct(mejor_hoja):.1f}% · mejoras={mejoras}"
        )
        notified_best = True

    pool = ThreadPoolExecutor(max_workers=1)

    def _run_await(fn, *args, **kwargs):
        """Lanza un round C++. Si el usuario Acepta/Cancela y ya hay mejor
        completo, NO espera el round en curso (puede durar 10–20 min)."""
        fut = pool.submit(fn, *args, **kwargs)
        while not fut.done():
            if _cancelled():
                if _is_complete(mejor_hoja, mejor_restos):
                    print(
                        "[ULTRA-RENEST] Aceptar/Cancel: usando mejor ya listo · "
                        "no esperar generación C++ en curso",
                        flush=True,
                    )
                else:
                    print(
                        "[ULTRA-RENEST] Cancel sin completo · "
                        "abortando espera del round en curso",
                        flush=True,
                    )
                # El pack C++ puede seguir unos segundos en background;
                # la UI ya no queda bloqueada.
                return None
            time.sleep(0.12)
        return fut.result()

    try:
        print(
            f"[ULTRA-RENEST] accept={renest_accept} cancel={bool(cancel_checker)} "
            f"n_piezas={n_in} pop={pop} rot={rot_step} pip={pip} seed=ultra "
            f"refine_from_best=1",
            flush=True,
        )

        seed = 1
        no_improve = 0
        rounds = 0
        # Mínimo gens rondas de calidad full; si accept-mode sigue hasta Cancel/Aceptar.
        min_rounds = max(1, int(gens))

        while True:
            if _cancelled():
                break

            # 1er completo: exploración rápida (1 gen). Luego refina desde ese orden
            # con varias gens (estilo NestFab continual).
            refining = bool(
                renest_accept
                and mejor_orden
                and _is_complete(mejor_hoja, mejor_restos)
            )
            if renest_accept:
                batch = max(4, min(int(gens), 12)) if refining else 1
            else:
                batch = max(1, min(3, gens))
            seed_ord = mejor_orden if refining else None

            t_round = time.perf_counter()
            if renest_accept:
                pack = _run_await(
                    _run_cpp, batch, seed, seed_order=seed_ord
                )
                if pack is None:
                    break
                hoja, restos, orden = pack
            else:
                hoja, restos, orden = _run_cpp(batch, seed)

            rounds += 1
            n_ok = len((hoja or {}).get("piezas") or [])
            n_rest = len(restos or [])
            print(
                f"[ULTRA-RENEST] round={rounds} gen_batch={batch} "
                f"colocadas={n_ok} restos={n_rest} "
                f"complete={_is_complete(hoja, restos)} "
                f"refine={int(refining)} "
                f"{time.perf_counter() - t_round:.1f}s "
                f"pop={pop} rot={rot_step} pip={pip}",
                flush=True,
            )

            if n_ok > 0:
                improved = _consider(hoja, restos, orden)
                if _is_complete(mejor_hoja, mejor_restos):
                    if improved:
                        if notified_best:
                            mejoras += 1
                        elif _is_complete(hoja, restos):
                            # Primer completo: mejoras=0 (base), Aceptar ya disponible.
                            pass
                        _publish_best(label="mejor" if notified_best else "nuevo")
                    elif not notified_best and _is_complete(hoja, restos):
                        _publish_best(label="nuevo")
                    elif notified_best:
                        # Misma calidad: refrescar contador/efi actual.
                        _publish_best(label="mejor")

            if _cancelled():
                break

            if _is_complete(hoja, restos):
                no_improve = 0
            else:
                no_improve += 1
            seed += 1
            if renest_accept:
                # Continual hasta Aceptar/Cancel; tras base completa → refine.
                continue
            if stagnation_limit > 0 and no_improve >= stagnation_limit:
                break
            if rounds >= min_rounds:
                break
    except Exception as exc:
        print(f"[ULTRA-RENEST] mejora interrumpida: {exc}", flush=True)
        if not (mejor_hoja.get("piezas") or []):
            raise
    finally:
        pool.shutdown(wait=False, cancel_futures=False)

    if renest_accept and not _is_complete(mejor_hoja, mejor_restos):
        print(
            f"[ULTRA-RENEST] sin completo al salir · "
            f"colocadas={len(mejor_hoja.get('piezas') or [])} "
            f"restos={len(mejor_restos or [])}",
            flush=True,
        )

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
