"""Puente Python → motores de nesting (registro + C++ legacy)."""
from __future__ import annotations

import copy
import os
import random
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
    out = {
        "nombre": str(piece.get("nombre") or ""),
        "area": area,
        "calibre": str(piece.get("calibre") or ""),
        "material": str(piece.get("material") or ""),
        "rings": _rings_from_shapely_polygon(poly),
        "marks": _marks_from_shapely(marks),
    }
    if piece.get("grain_locked"):
        out["grain_locked"] = True
    if piece.get("allowed_rotations") is not None:
        out["allowed_rotations"] = piece.get("allowed_rotations")
    return out


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
    kerf_override=0.15,
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
    kerf_override=0.15,
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
    kerf_override=0.15,
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
    kerf_override=0.15,
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
        # ANS C++: ruta ArgaNestCore cuando ARGA_NEST_CORE=1
        try:
            from . import arga_nest_core_bridge as _anc

            if _anc.core_enabled() and limite_rings is None and not seed_order:
                req = _anc.prepare_pack_request(
                    plate_w=float(w_placa),
                    plate_h=float(h_placa),
                    pieces=native_piezas,
                    kerf=float(kerf_override),
                    margin=float(margin_override),
                    engine="svgnest_ultra",
                    ga_population=int(pop_n if pop_n is not None else pop),
                    ga_generations=int(generations),
                    rotation_step_deg=float(rot if rot is not None else rot_step),
                    part_in_part=bool(pip if use_pip is None else use_pip),
                    certify=True,
                    enable_tabu=True,
                    tabu_seed_trials=3,
                    extra={"opt": opt_override, "corner": corner_override},
                )
                raw = _anc.pack_sheet_json(req)
                if not (raw.get("certify") or {}).get("ok", raw.get("ok", False)):
                    issues = (raw.get("certify") or {}).get("issues") or []
                    raise RuntimeError(f"ArgaNestCore certify fail: {issues}")
                try:
                    from .ai_telemetry import log_nest_event

                    log_nest_event(
                        engine="svgnest_ultra",
                        profile=str(os.environ.get("ARGA_NEST_MODE") or "first"),
                        n_piezas=len(native_piezas or []),
                        n_sheets=1,
                        efi=float((raw.get("metrics") or {}).get("eficiencia") or 0.0),
                        seed_order=[str(p.get("nombre") or "") for p in (native_piezas or [])],
                        certify_ok=True,
                        kerf=float(kerf_override),
                        plate_w=float(w_placa),
                        plate_h=float(h_placa),
                        source="algorithm_bridge_core",
                    )
                except Exception:
                    pass
                hoja_native = {
                    "piezas": [
                        {
                            "nombre": p.get("nombre"),
                            "area": p.get("area"),
                            "calibre": p.get("calibre"),
                            "material": p.get("material"),
                            "poligonos": p.get("poligonos") or [],
                        }
                        for p in (raw.get("placed") or [])
                    ],
                    "area_usada": float((raw.get("metrics") or {}).get("area_usada") or 0.0),
                    "eficiencia": float((raw.get("metrics") or {}).get("eficiencia") or 0.0),
                }
                restos_native = [{"nombre": n} for n in (raw.get("leftovers") or [])]
                hoja, restos = _assemble_pack_result(hoja_native, restos_native, piezas)
                return hoja, restos, []
        except Exception as _anc_exc:
            # Degradación limpia al algorithm_cpp legacy
            if str(os.environ.get("ARGA_NEST_CORE_STRICT", "")).strip() in ("1", "true", "yes"):
                raise
            print(f"[ARGA_NEST_CORE] fallback algorithm_cpp: {_anc_exc}", flush=True)

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


def empaquetar_una_hoja_arga_apex(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.15,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    cancel_checker=None,
):
    """
    ARGA APEX — pipeline completo (calidad + velocidad + aprendizaje):

      0) OCCT: sanear/extruir contornos
      1) Hive ML: sugerir política de sembrado + Eddie ordena
      2) CUDA opt-in (si runtime disponible)
      3) Explore NFP/GA (+ heartbeat)
      4) Refine solo si hay restos
      5) Venom + guardar episodio en hive_mind_nests + aprender
    """
    import threading
    import time

    from .nest_hardware import apply_nest_thread_env, hardware_nest_budget
    from .nest_optimization import get_engine_profile

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

    # --- Prep OCCT ---
    piezas_in = list(piezas or [])
    occt_stats: dict = {}
    try:
        from .apex_occt_prep import prepare_pieces_for_apex

        piezas_in, occt_stats = prepare_pieces_for_apex(piezas_in)
        print(
            f"[APEX-OCCT] enabled={int(bool(occt_stats.get('enabled')))} "
            f"ok={occt_stats.get('ok', 0)} fail={occt_stats.get('fail', 0)} "
            f"skip={occt_stats.get('skip', 0)} "
            f"holes={occt_stats.get('holes_in', 0)}->{occt_stats.get('holes_out', 0)} "
            f"{occt_stats.get('error') or ''}",
            flush=True,
        )
    except Exception as exc:
        print(f"[APEX-OCCT] skip: {exc}", flush=True)
        piezas_in = list(piezas or [])

    # --- Fase 3: ML sembrado (hive kNN → Eddie) ---
    seed_policy = ""
    ml_info: dict = {}
    try:
        from .ai_heuristic import get_last_seed_info, smart_seed_order
        from .hive_mind_nests import force_eddie_policy, suggest_seed_policy

        ml_info = suggest_seed_policy(
            piezas_in,
            w_placa=float(w_placa),
            h_placa=float(h_placa),
            kerf=float(kerf_override or 0.15),
        )
        pol = str(ml_info.get("policy") or "host_parasite")
        force_eddie_policy("arga_apex", pol)
        piezas_in = smart_seed_order(piezas_in, "arga_apex")
        seed_policy = str(get_last_seed_info("arga_apex").get("policy") or pol)
        print(
            f"[APEX-ML] suggest={pol} conf={ml_info.get('confidence')} "
            f"neighbors={ml_info.get('neighbors')} used={seed_policy} "
            f"reason={ml_info.get('reason')}",
            flush=True,
        )
    except Exception as exc:
        print(f"[APEX-ML] skip: {exc}", flush=True)

    try:
        from . import algorithm_cpp
    except ImportError as exc:
        raise NestingEngineUnavailableError(_CPP_REQUIRED_MSG) from exc

    if not hasattr(algorithm_cpp, "empaquetar_una_hoja_svgnest_ultra"):
        raise NestingEngineUnavailableError(
            "algorithm_cpp.pyd desactualizado (falta empaquetar_una_hoja_svgnest_ultra). "
            "Recompila con build_cpp_engine.ps1."
        )

    profile = get_engine_profile("arga_apex")
    budget = hardware_nest_budget()
    apply_nest_thread_env(budget)

    pop = max(8, min(16, int(profile.get("ga_population", 10) or 10)))
    explore_gens = max(1, int(profile.get("apex_explore_gens", 1) or 1))
    refine_gens = max(1, int(profile.get("ga_generations", 2) or 2))
    explore_rot = float(profile.get("apex_explore_rot_deg", 15.0) or 15.0)
    refine_rot = float(profile.get("rotation_step_deg", 5.0) or 5.0)
    pip = bool(profile.get("part_in_part", True))
    seeds = 1
    try:
        from .venom_ai import venom_enabled

        do_venom = bool(profile.get("apex_venom_polish", False)) and venom_enabled()
    except Exception:
        do_venom = False

    n_piezas = len(piezas_in)

    if n_piezas >= 25:
        refine_gens = min(refine_gens, 1)
        refine_rot = max(refine_rot, 15.0)

    if str(os.environ.get("ARGA_APEX_SMOKE", "")).strip() in ("1", "true", "yes"):
        seeds = 1
        pop = min(pop, 6)
        explore_gens = 1
        refine_gens = 1
        print("[APEX] smoke_mode=1", flush=True)

    # --- Fase 4: CUDA ---
    cuda_used = False
    cuda_ctx = None
    try:
        from .nest_cuda import (
            cuda_status_for_engine,
            engine_cuda_enabled,
            nest_cuda_env,
        )

        want_cuda = bool(profile.get("apex_cuda", True)) and engine_cuda_enabled(
            "arga_apex"
        )
        st = cuda_status_for_engine("arga_apex")
        print(
            f"[APEX-CUDA] want={int(want_cuda)} flag={int(st.get('flag_enabled'))} "
            f"runtime={int(st.get('runtime_available'))} detail={st.get('detail')}",
            flush=True,
        )
        if want_cuda:
            cuda_ctx = nest_cuda_env(True)
            cuda_ctx.__enter__()
            cuda_used = True
    except Exception as exc:
        print(f"[APEX-CUDA] skip: {exc}", flush=True)

    print(
        f"[APEX] start piezas={n_piezas} pop={pop} "
        f"explore={explore_gens}@{explore_rot}° refine={refine_gens}@{refine_rot}° "
        f"pip={int(pip)} seeds={seeds} venom={int(do_venom)} cuda={int(cuda_used)} "
        f"threads={budget.get('nest_threads')}",
        flush=True,
    )

    native_piezas = [_piece_to_native(p) for p in piezas_in]
    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    def _run_cpp(generations: int, seed: int, rot: float, seed_order=None):
        args = dict(
            piezas=native_piezas,
            w_placa=w_placa,
            h_placa=h_placa,
            kerf_override=kerf_override,
            margin_override=margin_override,
            opt_override=opt_override,
            corner_override=corner_override,
            limite_rings=limite_rings,
            ga_population=int(pop),
            ga_generations=int(generations),
            rotation_step_deg=float(rot),
            part_in_part=bool(pip),
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
        hoja, restos = _assemble_pack_result(hoja_native, restos_native, piezas_in)
        orden = [int(x) for x in (orden_native or [])]
        return hoja, restos, orden

    def _run_cpp_heartbeat(label: str, *args, **kwargs):
        """Fase 1: heartbeat cada 5s mientras el C++ trabaja."""
        box: dict = {}

        def _target():
            try:
                box["out"] = _run_cpp(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                box["err"] = exc

        th = threading.Thread(target=_target, name=f"apex-{label}", daemon=True)
        th.start()
        t_hb = time.perf_counter()
        while th.is_alive():
            th.join(5.0)
            if th.is_alive():
                print(
                    f"[APEX] ... sigue nestando ({time.perf_counter() - t_hb:.0f}s) "
                    f"fase={label}",
                    flush=True,
                )
            if _cancelled():
                print(f"[APEX] cancel pedido durante {label}", flush=True)
                break
        if "err" in box:
            raise box["err"]
        return box.get("out") or (
            {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0},
            list(piezas_in),
            [],
        )

    t0 = time.perf_counter()
    mejor_h = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    mejor_r = list(piezas_in)
    mejor_o: list[int] = []

    try:
        if not _cancelled():
            hoja, restos, orden = _run_cpp_heartbeat(
                "explore", explore_gens, 1001, explore_rot
            )
            n_ok = len((hoja or {}).get("piezas") or [])
            print(
                f"[APEX] explore_seed=1/1 placed={n_ok} restos={len(restos or [])} "
                f"· {time.perf_counter() - t0:.1f}s",
                flush=True,
            )
            mejor_h, mejor_r, mejor_o = hoja, restos, orden

        do_refine = (
            not _cancelled()
            and bool(mejor_o)
            and bool(mejor_h.get("piezas"))
            and bool(mejor_r)
            and refine_gens > 0
        )
        if do_refine:
            t_ref = time.perf_counter()
            h2, r2, o2 = _run_cpp_heartbeat(
                "refine", refine_gens, 2001, refine_rot, seed_order=mejor_o
            )
            print(
                f"[APEX] refine@{refine_rot}° "
                f"placed={len((h2 or {}).get('piezas') or [])} "
                f"restos={len(r2 or [])} · {time.perf_counter() - t_ref:.1f}s",
                flush=True,
            )
            if _svgnest_is_better(h2, r2, mejor_h, mejor_r):
                mejor_h, mejor_r, mejor_o = h2, r2, o2
                print("[APEX] refine wins", flush=True)
        elif not (mejor_r or []):
            print("[APEX] refine skipped (sin restos)", flush=True)
    finally:
        if cuda_ctx is not None:
            try:
                cuda_ctx.__exit__(None, None, None)
            except Exception:
                pass

    mejor_h["engine_id"] = "arga_apex"
    mejor_h["placa_w"] = float(w_placa)
    mejor_h["placa_h"] = float(h_placa)
    mejor_h["apex_occt"] = dict(occt_stats) if occt_stats else {}
    mejor_h["apex_ml"] = dict(ml_info) if ml_info else {}
    mejor_h["apex_cuda"] = bool(cuda_used)
    try:
        mejor_h["kerf_usado"] = float(kerf_override)
    except Exception:
        mejor_h["kerf_usado"] = 0.15

    if do_venom and (mejor_h.get("piezas") or []) and not _cancelled():
        try:
            from . import venom_ai

            venom_ai.apply_smart_polisher(
                mejor_h, "arga_apex", kerf_in=float(kerf_override)
            )
            print("[APEX] venom_polish done", flush=True)
        except Exception as exc:
            print(f"[APEX] venom_polish skip: {exc}", flush=True)

    elapsed = time.perf_counter() - t0
    mejor_h["apex_elapsed_s"] = elapsed
    print(
        f"[APEX] done placed={len(mejor_h.get('piezas') or [])} "
        f"restos={len(mejor_r or [])} · {elapsed:.1f}s",
        flush=True,
    )

    # Hive learn lo publica Venom (todos los motores). Aquí solo dejamos meta.
    mejor_h["apex_seed_policy"] = seed_policy
    return mejor_h, mejor_r


def empaquetar_una_hoja_legacy_mc(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.15,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    mc_iterations=None,
):
    """Empaque C++ Monte Carlo Lite (rápido). No sustituye por FORCE/base."""
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


def _lite_batch_desde_hoja(hoja: dict, pool_src: list) -> list:
    """Piezas pack-ready correspondientes a las ya colocadas en la hoja."""
    lookup = _build_piece_lookup_lists(pool_src)
    batch: list = []
    for pz in list(hoja.get("piezas") or []):
        restored = _piece_from_native_rest(
            {"nombre": str(pz.get("nombre") or "")},
            lookup,
        )
        if restored is not None:
            batch.append(restored)
    return batch


def _lite_plate_renest_enabled(profile: dict) -> bool:
    env = str(os.environ.get("ARGA_LITE_PLATE_RENEST", "") or "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return bool(profile.get("lite_plate_renest", True))


def _lite_resolve_passes(profile: dict) -> int:
    env = str(os.environ.get("ARGA_LITE_PASSES", "") or "").strip()
    if env:
        try:
            return max(1, min(int(env), 5))
        except ValueError:
            pass
    pases = int(profile.get("lite_refine_passes", 1) or 1)
    return max(1, min(pases, 5))


def _lite_resolve_renest_tries(profile: dict) -> int:
    env = str(os.environ.get("ARGA_LITE_PLATE_RENEST_TRIES", "") or "").strip()
    if env:
        try:
            return max(1, min(int(env), 4))
        except ValueError:
            pass
    tries = int(profile.get("lite_plate_renest_tries", 1) or 1)
    return max(1, min(tries, 4))


def _lite_resolve_renest_mc(profile: dict) -> int:
    env = str(os.environ.get("ARGA_LITE_PLATE_RENEST_MC", "") or "").strip()
    if env:
        try:
            return max(1, min(int(env), 4))
        except ValueError:
            pass
    return max(1, min(int(profile.get("lite_plate_renest_mc", 1) or 1), 4))


def empaquetar_una_hoja_arga_lite(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.15,
    margin_override=0.15,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    mc_iterations=None,
):
    """
    ARGA LITE (rápido): 1 pase MC + opcional 1 renest de placa (1 MC, keep-if-better).

    Opt-in más calidad (más lento):
      ARGA_LITE_PASSES=2  ARGA_LITE_PLATE_RENEST_TRIES=2  ARGA_LITE_PLATE_RENEST_MC=2
    Opt-out renest: ARGA_LITE_PLATE_RENEST=0
    """
    from .nest_optimization import get_engine_profile

    profile = get_engine_profile("arga_lite")
    pases = _lite_resolve_passes(profile)
    do_plate_renest = _lite_plate_renest_enabled(profile)
    renest_tries = _lite_resolve_renest_tries(profile)
    renest_mc = _lite_resolve_renest_mc(profile)

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
        f"[LITE] explore->refine | pases={pases} "
        f"plate_renest={int(do_plate_renest)} "
        f"renest_tries={renest_tries} renest_mc={renest_mc}",
        flush=True,
    )

    for pase in range(1, pases + 1):
        # Cap bajo: Lite no debe escalar MC con el número de pase.
        iters_pase = 1 if pases <= 1 else max(1, min(pase, 2))
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

        orden_actual = _lite_orden_desde_mejor(mejor_hoja, mejor_restos, pool0)

    # Renest placa sola — barato: 1 MC por try (no explore→refine anidado).
    if do_plate_renest and (mejor_hoja.get("piezas") or []):
        batch = _lite_batch_desde_hoja(mejor_hoja, pool0)
        if len(batch) >= 2:
            print(
                f"[LITE] plate_renest · tries={renest_tries} · "
                f"mc={renest_mc} · piezas={len(batch)}",
                flush=True,
            )
            for intento in range(renest_tries):
                if intento == 0:
                    orden0 = sorted(
                        batch,
                        key=lambda x: float(x.get("area", 0) or 0),
                        reverse=True,
                    )
                else:
                    orden0 = list(batch)
                    random.shuffle(orden0)
                    orden0.sort(
                        key=lambda x: float(x.get("area", 0) or 0),
                        reverse=True,
                    )

                hoja_r, restos_r = empaquetar_una_hoja_legacy_mc(
                    orden0,
                    w_placa,
                    h_placa,
                    kerf_override=kerf_override,
                    margin_override=margin_override,
                    opt_override=opt_override,
                    corner_override=corner_override,
                    limite_poly=limite_poly,
                    mc_iterations=renest_mc,
                )
                n_ok = len(hoja_r.get("piezas") or [])
                if n_ok < len(batch) or restos_r:
                    print(
                        f"[LITE] plate_renest try={intento + 1} "
                        f"rechazado (no caben todas: {n_ok}/{len(batch)})",
                        flush=True,
                    )
                    continue
                if _svgnest_is_better(
                    hoja_r, mejor_restos, mejor_hoja, mejor_restos
                ):
                    mejoras += 1
                    mejor_hoja = hoja_r
                    print(
                        f"[LITE] plate_renest mejora try={intento + 1} · "
                        f"efi={float(hoja_r.get('eficiencia') or 0):.1f}%",
                        flush=True,
                    )
                    break  # con 1 mejora basta; no gastar más tries
                print(
                    f"[LITE] plate_renest try={intento + 1} sin mejora",
                    flush=True,
                )

    print(
        f"[LITE] fin · mejoras={mejoras} · "
        f"colocadas={len(mejor_hoja.get('piezas') or [])} "
        f"restos={len(mejor_restos or [])}",
        flush=True,
    )
    try:
        from . import compact_lite

        if compact_lite.compact_enabled() and (mejor_hoja.get("piezas") or []):
            mejor_hoja.setdefault("placa_w", float(w_placa or 0))
            mejor_hoja.setdefault("placa_h", float(h_placa or 0))
            mejor_hoja.setdefault("kerf_usado", float(kerf_override or 0))
            compact_lite.apply_band_compact(mejor_hoja, engine_id="arga_lite")
    except Exception as compact_ex:
        print(f"[LITE] compact skip: {compact_ex}", flush=True)
    return mejor_hoja, mejor_restos


def empaquetar_una_hoja_mc(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.15,
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
