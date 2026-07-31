"""
Módulo de IA de Pulido — "Venom" (Compactación Inteligente con Aprendizaje)

Pipeline:
  1) Vector de gravedad (epsilon-greedy).
  2) Coarse nudge AABB vía venom_core (C++) si está disponible.
  3) Polish exacto Shapely (NFP-ish por vértices), con filtro CUDA opt-in.
  4) Reward = Δcompactación (bbox del nido / free-rect), NO eficiencia de área.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

VENOM_HIVE_MIND_PATHS = [
    Path(r"\\SERVER-ARGA\ArgaNesting\hive_mind_venom"),
    Path(__file__).parent.parent.parent / "cache" / "hive_mind_venom",
]

# Colmena compartida: lo que aprende Ultra/LITE/Force/APEX beneficia a todos.
VENOM_SHARED_ID = "_shared_all_engines"


def _get_venom_memory_path(engine_id: str) -> Path:
    for path in VENOM_HIVE_MIND_PATHS:
        try:
            if path.exists() or path.parent.exists():
                path.mkdir(parents=True, exist_ok=True)
                return path / f"venom_weights_{engine_id}.json"
        except OSError:
            continue
        except Exception:
            continue
    local = Path(__file__).parent / "venom_cache"
    local.mkdir(exist_ok=True)
    return local / f"venom_weights_{engine_id}.json"


def _default_venom_weights() -> dict:
    return {
        "schema_version": 2,
        "estrategias": {
            "(-1.0, -1.0)": {"recompensa_acumulada": 10.0, "usos": 1},
            "(-2.0, -0.5)": {"recompensa_acumulada": 5.0, "usos": 1},
            "(-0.5, -2.0)": {"recompensa_acumulada": 5.0, "usos": 1},
        },
        # Techo amplio: no bajar exploración demasiado pronto.
        "exploracion": 0.28,
    }


def _merge_estrategias(base: dict, extra: dict) -> dict:
    out = {k: dict(v) for k, v in (base or {}).items() if isinstance(v, dict)}
    for k, v in (extra or {}).items():
        if not isinstance(v, dict):
            continue
        if k not in out:
            out[k] = {
                "recompensa_acumulada": float(v.get("recompensa_acumulada", 0) or 0),
                "usos": int(v.get("usos", 0) or 0),
            }
            continue
        out[k] = {
            "recompensa_acumulada": float(out[k].get("recompensa_acumulada", 0) or 0)
            + float(v.get("recompensa_acumulada", 0) or 0),
            "usos": int(out[k].get("usos", 0) or 0) + int(v.get("usos", 0) or 0),
        }
    return out


def _read_venom_file(engine_id: str) -> dict:
    mem_path = _get_venom_memory_path(engine_id)
    if mem_path.exists():
        try:
            with open(mem_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "estrategias" in data:
                data.setdefault("schema_version", 2)
                return data
        except Exception:
            pass
    return _default_venom_weights()


def _load_venom_weights(engine_id: str) -> dict:
    """Pesos del motor + colmena compartida (aprende de cualquier nesteo)."""
    local = _read_venom_file(engine_id)
    shared = _read_venom_file(VENOM_SHARED_ID)
    merged = dict(local)
    merged["estrategias"] = _merge_estrategias(
        shared.get("estrategias") or {},
        local.get("estrategias") or {},
    )
    # Mantener exploración en rango útil (techo de mejora amplio).
    exp_l = float(local.get("exploracion", 0.28) or 0.28)
    exp_s = float(shared.get("exploracion", 0.28) or 0.28)
    merged["exploracion"] = max(0.12, min(0.40, max(exp_l, exp_s)))
    return merged


def _save_venom_weights(engine_id: str, data: dict):
    mem_path = _get_venom_memory_path(engine_id)
    try:
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["schema_version"] = 2
        # Piso de exploración: no matar el aprendizaje temprano.
        payload["exploracion"] = max(
            0.12, min(0.40, float(payload.get("exploracion", 0.28) or 0.28))
        )
        with open(mem_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)
    except Exception:
        pass
    # Refuerza la colmena global (LITE ayuda a Ultra y viceversa).
    try:
        shared = _read_venom_file(VENOM_SHARED_ID)
        shared["estrategias"] = _merge_estrategias(
            shared.get("estrategias") or {},
            (data or {}).get("estrategias") or {},
        )
        shared["exploracion"] = max(
            0.12,
            min(
                0.40,
                0.5 * float(shared.get("exploracion", 0.28) or 0.28)
                + 0.5 * float((data or {}).get("exploracion", 0.28) or 0.28),
            ),
        )
        shared_path = _get_venom_memory_path(VENOM_SHARED_ID)
        shared_path.parent.mkdir(parents=True, exist_ok=True)
        shared["schema_version"] = 2
        with open(shared_path, "w", encoding="utf-8") as f:
            json.dump(shared, f, indent=4)
    except Exception:
        pass


def _choose_gravity_vector(weights: dict, *, force_explore: bool = False) -> tuple:
    exploracion = float(weights.get("exploracion", 0.3) or 0.3)
    if force_explore:
        exploracion = max(exploracion, 0.55)
    if random.random() < exploracion:
        vx = -random.uniform(0.5, 3.0)
        vy = -random.uniform(0.5, 3.0)
        return (vx, vy)

    best_vector = "(-1.0, -1.0)"
    best_score = -1e18
    for vec_str, stats in weights.get("estrategias", {}).items():
        score = stats.get("recompensa_acumulada", 0) / max(1, stats.get("usos", 1))
        if score > best_score:
            best_score = score
            best_vector = vec_str

    try:
        clean = best_vector.strip("()")
        parts = clean.split(",")
        return (float(parts[0].strip()), float(parts[1].strip()))
    except Exception:
        return (-1.0, -1.0)


def _calcular_eficiencia_hoja(hoja: dict) -> float:
    """Área piezas / área placa (referencia; no es la señal de aprendizaje)."""
    w = float(hoja.get("placa_w", 0) or 0)
    h = float(hoja.get("placa_h", 0) or 0)
    area_placa = w * h
    if area_placa <= 0:
        return 0.0

    area_piezas = 0.0
    for p in hoja.get("piezas", []):
        nombre = str(p.get("nombre", "") or "")
        if nombre.startswith("REMANENTE__"):
            continue
        area_piezas += float(p.get("area", 0) or 0)

    return (area_piezas / area_placa) * 100.0 if area_placa > 0 else 0.0


def compute_compactness(hoja: dict, items: list | None = None) -> dict:
    """
    Compactación del nido: menor bbox usado + mayor free-rect L-shape ≈ mejor.

    compactness_score ∈ [0, 100]: 100 = nido ocupa poco del plato (mejor packing espacial).
    """
    placa_w = float(hoja.get("placa_w", 0) or 0)
    placa_h = float(hoja.get("placa_h", 0) or 0)
    plate_area = max(placa_w * placa_h, 1.0)

    bounds_list = []
    if items:
        for it in items:
            poly = it.get("poly") if isinstance(it, dict) else None
            if poly is not None and hasattr(poly, "bounds") and not poly.is_empty:
                bounds_list.append(poly.bounds)
    if not bounds_list:
        for p in hoja.get("piezas") or []:
            nombre = str(p.get("nombre", "") or "")
            if nombre.startswith("REMANENTE__") or nombre.startswith("REF__"):
                continue
            poly = p.get("poly_exact") or p.get("poly")
            if poly is not None and hasattr(poly, "bounds") and not getattr(poly, "is_empty", True):
                bounds_list.append(poly.bounds)

    if not bounds_list:
        return {
            "used_w": 0.0,
            "used_h": 0.0,
            "bbox_area": plate_area,
            "free_l_area": 0.0,
            "compactness_score": 0.0,
        }

    min_x = min(b[0] for b in bounds_list)
    min_y = min(b[1] for b in bounds_list)
    max_x = max(b[2] for b in bounds_list)
    max_y = max(b[3] for b in bounds_list)
    used_w = max(0.0, max_x - min_x)
    used_h = max(0.0, max_y - min_y)
    bbox_area = max(used_w * used_h, 1.0)

    # Remanente L aproximado anclado a esquina opuesta al nido compactado.
    free_w = max(0.0, placa_w - used_w)
    free_h = max(0.0, placa_h - used_h)
    free_l_area = free_w * placa_h + used_w * free_h

    # Score: premia bbox chico y remanente L grande.
    bbox_term = 1.0 - min(1.0, bbox_area / plate_area)
    free_term = min(1.0, free_l_area / plate_area)
    compactness_score = 100.0 * (0.65 * bbox_term + 0.35 * free_term)

    return {
        "used_w": used_w,
        "used_h": used_h,
        "bbox_area": bbox_area,
        "free_l_area": free_l_area,
        "compactness_score": compactness_score,
    }


def _translate_piece_data(p, sx, sy):
    from shapely import affinity

    p["shift_x"] = float(p.get("shift_x", 0.0) or 0.0) + sx
    p["shift_y"] = float(p.get("shift_y", 0.0) or 0.0) + sy

    if "poly_exact" in p and p["poly_exact"] is not None:
        p["poly_exact"] = affinity.translate(p["poly_exact"], sx, sy)
    if "poly" in p and p["poly"] is not None and hasattr(p["poly"], "bounds"):
        try:
            p["poly"] = affinity.translate(p["poly"], sx, sy)
        except Exception:
            pass

    for key in ["poligonos", "marcas"]:
        if key in p and p[key]:
            try:
                new_data = []
                for arr_data in p[key]:
                    new_arr = []
                    for pt in arr_data:
                        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            new_arr.append([pt[0] + sx, pt[1] + sy])
                        elif isinstance(pt, dict):
                            new_dict = dict(pt)
                            if "x" in new_dict:
                                new_dict["x"] += sx
                            elif "X" in new_dict:
                                new_dict["X"] += sx
                            if "y" in new_dict:
                                new_dict["y"] += sy
                            elif "Y" in new_dict:
                                new_dict["Y"] += sy
                            new_arr.append(new_dict)
                        else:
                            new_arr.append(pt)
                    new_data.append(new_arr)
                p[key] = new_data
            except Exception:
                pass


def _poly_to_rings(poly) -> list:
    try:
        rings = [list(poly.exterior.coords)]
        for hole in getattr(poly, "interiors", []) or []:
            rings.append(list(hole.coords))
        return rings
    except Exception:
        return []


def _cuda_screen_candidates(
    *,
    fixed_polys: list,
    piece_centered,
    plate_w: float,
    plate_h: float,
    candidates: list[tuple[float, float]],
) -> list[int]:
    """1 = rechazo raster seguro; 0 = validar con Shapely. Sin flag/CUDA → todo 0."""
    n = len(candidates)
    if n < 48 or plate_w <= 0 or plate_h <= 0:
        return [0] * n
    try:
        from .nest_cuda import engine_cuda_enabled

        if not engine_cuda_enabled("venom"):
            return [0] * n
        from . import algorithm_cpp as cpp

        filter_fn = getattr(cpp, "nest_filter_translations", None)
        if filter_fn is None or not bool(getattr(cpp, "nest_cuda_available", lambda: False)()):
            return [0] * n
        piece_rings = _poly_to_rings(piece_centered)
        if not piece_rings:
            return [0] * n
        fixed = []
        for pp in fixed_polys:
            rings = _poly_to_rings(pp)
            if rings:
                fixed.append(rings)
        if not fixed:
            return [0] * n
        flags = filter_fn(
            fixed,
            piece_rings,
            float(plate_w),
            float(plate_h),
            [(float(c[0]), float(c[1])) for c in candidates],
            8.0,
        )
        out = [int(f) for f in flags]
        if len(out) != n:
            return [0] * n
        return out
    except Exception:
        return [0] * n


def _try_import_venom_core():
    """Carga venom_core (.pyd) aunque el tag cpXX no coincida vía package import."""
    try:
        from . import venom_core as vc
        return vc
    except Exception:
        pass
    try:
        import importlib.util
        import sys

        here = Path(__file__).parent
        candidates = sorted(here.glob("venom_core*.pyd")) + sorted(here.glob("venom_core*.so"))
        for cand in candidates:
            spec = importlib.util.spec_from_file_location("venom_core", cand)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules["venom_core"] = mod
            spec.loader.exec_module(mod)
            return mod
    except Exception:
        pass
    return None


def _try_venom_core_coarse(items: list, vx: float, vy: float, kerf_mm: float, placa_w: float, placa_h: float) -> int:
    """
    Prefase AABB con venom_core. Devuelve #piezas movidas.
    Fallo silencioso si el .pyd no carga (el polish Shapely sigue).
    Compatible con .pyd viejo (sin plate_w/h) y nuevo.
    """
    venom_core = _try_import_venom_core()
    if venom_core is None or not hasattr(venom_core, "compact_plate") or not items:
        return 0

    # Pasos pequeños: el C++ suma vx/vy por iteración; normalizar a ~1mm.
    mag = max(abs(vx), abs(vy), 1e-6)
    step_vx = vx / mag
    step_vy = vy / mag

    pieces_data = []
    for item in items:
        b = item["poly"].bounds
        pieces_data.append((int(item["idx"]), float(b[0]), float(b[1]), float(b[2]), float(b[3])))

    try:
        import inspect

        fn = venom_core.compact_plate
        params = inspect.signature(fn).parameters
        if "plate_w" in params:
            results = fn(
                pieces_data,
                float(step_vx),
                float(step_vy),
                float(kerf_mm),
                float(placa_w),
                float(placa_h),
            )
        else:
            results = fn(pieces_data, float(step_vx), float(step_vy), float(kerf_mm))
    except Exception:
        return 0

    from shapely import affinity

    moved = 0
    by_id = {int(item["idx"]): item for item in items}
    for tup in results or []:
        try:
            pid, sx, sy = int(tup[0]), float(tup[1]), float(tup[2])
        except Exception:
            continue
        if abs(sx) < 1e-9 and abs(sy) < 1e-9:
            continue
        item = by_id.get(pid)
        if not item:
            continue
        # Clamp suave a placa (el C++ antiguo no conoce max bounds).
        b = item["poly"].bounds
        new_minx = b[0] + sx
        new_miny = b[1] + sy
        new_maxx = b[2] + sx
        new_maxy = b[3] + sy
        if placa_w > 0 and new_maxx > placa_w:
            sx -= new_maxx - placa_w
        if placa_h > 0 and new_maxy > placa_h:
            sy -= new_maxy - placa_h
        if new_minx < 0:
            sx -= new_minx
        if new_miny < 0:
            sy -= new_miny
        if abs(sx) < 1e-9 and abs(sy) < 1e-9:
            continue
        item["poly"] = affinity.translate(item["poly"], sx, sy)
        item["minx"] = item["poly"].bounds[0]
        item["miny"] = item["poly"].bounds[1]
        _translate_piece_data(item["p"], sx, sy)
        moved += 1
    return moved


def apply_smart_polisher(
    hoja: dict,
    engine_id: str,
    kerf_in: float | None = None,
    *,
    force_explore: bool = False,
):
    """
    Acabado Venom:
      1) Hole-fill same-sheet (cavidades VFM/C)
      2) Compactación por gravedad
    Si el resultado deja solapes, se revierte toda la hoja al snapshot previo.

    Aprende en colmena compartida: un renesteo LITE mejora también Ultra/APEX.
    """
    import copy

    print(f"[VENOM-AI] Iniciando apply_smart_polisher para motor: {engine_id}")

    # Permitir republicar en 2º pase de renesteo.
    try:
        hoja.pop("_hive_nest_published", None)
    except Exception:
        pass

    piezas = hoja.get("piezas", [])
    if not piezas:
        return

    if kerf_in is not None:
        try:
            hoja["kerf_usado"] = float(kerf_in)
        except Exception:
            pass

    snapshot_piezas = copy.deepcopy(piezas)

    # --- FASE 1: hole-fill (antes del gravity para no pegar chicos al borde) ---
    fill_stats = {"filled": 0, "area_filled": 0.0, "hosts": 0, "cavities": 0}
    try:
        from .venom_hole_fill import fill_host_cavities

        fill_stats = fill_host_cavities(hoja, engine_id) or fill_stats
    except Exception as e:
        try:
            from datetime import datetime
            from pathlib import Path

            log_path = Path(__file__).parent.parent.parent / "_logs" / "venom_debug.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now()}] FILL ERROR: {e}\n")
        except Exception:
            pass

    weights = _load_venom_weights(engine_id)
    vx, vy = _choose_gravity_vector(weights, force_explore=force_explore)

    kerf_eff = float(hoja.get("kerf_usado", 0.0) or 0.0)
    if kerf_in is not None:
        try:
            kerf_eff = float(kerf_in)
            hoja["kerf_usado"] = kerf_eff
        except Exception:
            pass
    kerf_mm = kerf_eff * 25.4
    kerf_half = max(kerf_mm / 2.0, 0.5)

    from shapely import affinity
    from .geometry_parser import reconstruir_poly_seguro

    # Refrescar lista por si fill mutó piezas
    piezas = hoja.get("piezas", [])

    items = []
    for idx, p in enumerate(piezas):
        poly = p.get("poly_exact") or p.get("poly")
        if poly is None and p.get("poligonos"):
            poly = reconstruir_poly_seguro(p.get("poligonos") or [])
        if poly and hasattr(poly, "bounds") and not poly.is_empty:
            if not poly.is_valid:
                poly = poly.buffer(0)
            if hasattr(poly, "geoms"):
                poly = max(poly.geoms, key=lambda g: g.area)
            if poly.is_valid:
                items.append(
                    {"idx": idx, "poly": poly, "p": p, "minx": poly.bounds[0], "miny": poly.bounds[1]}
                )

    if not items:
        return

    items.sort(key=lambda x: x["minx"] + x["miny"])
    placa_w = float(hoja.get("placa_w", 0) or 0)
    placa_h = float(hoja.get("placa_h", 0) or 0)
    compact_pre = compute_compactness(hoja, items)

    # --- GLOBAL SHIFT ---
    min_x_todas = min(item["poly"].bounds[0] for item in items)
    min_y_todas = min(item["poly"].bounds[1] for item in items)
    max_x_todas = max(item["poly"].bounds[2] for item in items)
    max_y_todas = max(item["poly"].bounds[3] for item in items)

    global_dx = 0.0
    global_dy = 0.0
    if vx < 0 and min_x_todas > kerf_half:
        global_dx = -(min_x_todas - kerf_half)
    elif vx > 0 and max_x_todas < placa_w - kerf_half:
        global_dx = (placa_w - kerf_half) - max_x_todas
    if vy < 0 and min_y_todas > kerf_half:
        global_dy = -(min_y_todas - kerf_half)
    elif vy > 0 and max_y_todas < placa_h - kerf_half:
        global_dy = (placa_h - kerf_half) - max_y_todas

    if global_dx != 0.0 or global_dy != 0.0:
        for item in items:
            item["poly"] = affinity.translate(item["poly"], global_dx, global_dy)
            item["minx"] += global_dx
            item["miny"] += global_dy
            _translate_piece_data(item["p"], global_dx, global_dy)

    # --- Coarse AABB (venom_core) ---
    coarse_moved = _try_venom_core_coarse(items, vx, vy, kerf_mm, placa_w, placa_h)

    nudges_totales = 0
    mm_ahorrados_x = 0.0
    mm_ahorrados_y = 0.0

    all_polys_data = []
    for item in items:
        poly = item["poly"]
        p_sim = poly.simplify(2.0)
        try:
            v_verts = list(p_sim.exterior.coords)
        except Exception:
            v_verts = [(poly.bounds[0], poly.bounds[1])]
        p_buf = poly.buffer(kerf_half, resolution=2)
        all_polys_data.append([item, poly, poly.bounds, v_verts, p_buf, p_buf.bounds])

    def dist_to_grav(data):
        bounds = data[2]
        dx = bounds[0] if vx < 0 else (-bounds[2] if vx > 0 else 0)
        dy = bounds[1] if vy < 0 else (-bounds[3] if vy > 0 else 0)
        return dx + dy

    all_polys_data.sort(key=dist_to_grav)

    MAX_JUMP_MM = 1600.0

    for i in range(len(all_polys_data)):
        item, poly, bounds, u_verts, p_buf, p_buf_bounds = all_polys_data[i]
        minx, miny, maxx, maxy = bounds
        poly_w = maxx - minx
        poly_h = maxy - miny

        poly_centered = affinity.translate(poly, -minx, -miny)
        u_verts_centered = [(x - minx, y - miny) for x, y in u_verts]

        candidates = []
        candidates.append((kerf_half, kerf_half))
        if placa_w > 0:
            candidates.append((placa_w - poly_w - kerf_half, kerf_half))
        if placa_h > 0:
            candidates.append((kerf_half, placa_h - poly_h - kerf_half))
        if placa_w > 0 and placa_h > 0:
            candidates.append((placa_w - poly_w - kerf_half, placa_h - poly_h - kerf_half))

        for j in range(len(all_polys_data)):
            if i == j:
                continue
            v_verts = all_polys_data[j][3]
            for vx_coord, vy_coord in v_verts:
                for ux_coord, uy_coord in u_verts_centered:
                    candidates.append((vx_coord - ux_coord, vy_coord - uy_coord))

        if placa_w > 0 and placa_h > 0:
            for gx in range(int(kerf_half), int(placa_w), 150):
                for gy in range(int(kerf_half), int(placa_h), 150):
                    candidates.append((gx, gy))

        valid_candidates = []
        for cx, cy in candidates:
            if cx < kerf_half or cy < kerf_half:
                continue
            if placa_w > 0 and cx + poly_w > placa_w - kerf_half:
                continue
            if placa_h > 0 and cy + poly_h > placa_h - kerf_half:
                continue
            if vx < 0 and cx > minx + 1.0:
                continue
            if vx > 0 and cx < minx - 1.0:
                continue
            if vy < 0 and cy > miny + 1.0:
                continue
            if vy > 0 and cy < miny - 1.0:
                continue
            if math.hypot(cx - minx, cy - miny) > MAX_JUMP_MM:
                continue
            valid_candidates.append((cx, cy))

        def score(c):
            sx, sy = c
            score_x = sx if vx < 0 else (-sx if vx > 0 else 0)
            score_y = sy if vy < 0 else (-sy if vy > 0 else 0)
            return score_x + score_y

        valid_candidates.sort(key=score)
        valid_candidates.append((minx, miny))

        seen = set()
        unique_candidates = []
        for c in valid_candidates:
            rounded = (round(c[0], 1), round(c[1], 1))
            if rounded not in seen:
                seen.add(rounded)
                unique_candidates.append(c)

        # CUDA: descartar rechazos seguros antes de Shapely.
        fixed_polys = [all_polys_data[j][4] for j in range(len(all_polys_data)) if j != i]
        cuda_flags = _cuda_screen_candidates(
            fixed_polys=fixed_polys,
            piece_centered=poly_centered,
            plate_w=placa_w,
            plate_h=placa_h,
            candidates=unique_candidates,
        )

        best_poly = None
        best_cx = minx
        best_cy = miny

        for ci, (cx, cy) in enumerate(unique_candidates):
            if ci < len(cuda_flags) and cuda_flags[ci] == 1:
                continue
            test_poly = affinity.translate(poly_centered, cx, cy)
            test_bounds = test_poly.bounds

            collision = False
            try:
                test_clear = test_poly.buffer(kerf_half, resolution=2, join_style=2)
            except Exception:
                test_clear = test_poly
            tc_bounds = test_clear.bounds
            for j in range(len(all_polys_data)):
                if i == j:
                    continue
                op_poly = all_polys_data[j][1]
                op_bounds = all_polys_data[j][2]
                if (
                    tc_bounds[2] <= op_bounds[0]
                    or tc_bounds[0] >= op_bounds[2]
                    or tc_bounds[3] <= op_bounds[1]
                    or tc_bounds[1] >= op_bounds[3]
                ):
                    continue
                # Kerf completo: ambos lados (test_clear vs other raw ≡ kerf/2+kerf/2 si other también buffer, aquí other.buffer)
                try:
                    op_clear = op_poly.buffer(kerf_half, resolution=2, join_style=2)
                except Exception:
                    op_clear = op_poly
                if test_clear.intersects(op_clear) and not test_clear.touches(op_clear):
                    collision = True
                    break

            if not collision:
                best_poly = test_poly
                best_cx = cx
                best_cy = cy
                break

        if best_poly and (best_cx != minx or best_cy != miny):
            shift_x = best_cx - minx
            shift_y = best_cy - miny

            item["poly"] = best_poly
            item["minx"] = best_cx
            item["miny"] = best_cy

            bp_sim = best_poly.simplify(2.0)
            try:
                new_u_verts = list(bp_sim.exterior.coords)
            except Exception:
                new_u_verts = [(best_cx, best_cy)]
            bp_buf = best_poly.buffer(kerf_half, resolution=2)

            all_polys_data[i][1] = best_poly
            all_polys_data[i][2] = best_poly.bounds
            all_polys_data[i][3] = new_u_verts
            all_polys_data[i][4] = bp_buf
            all_polys_data[i][5] = bp_buf.bounds

            _translate_piece_data(item["p"], shift_x, shift_y)
            mm_ahorrados_x += abs(shift_x)
            mm_ahorrados_y += abs(shift_y)
            nudges_totales += 1

    # --- Gravity slide exacto (ejes independientes; cierra gaps reales) ---
    step_mm = 2.0
    axis_steps = []
    if vx < 0:
        axis_steps.append((-step_mm, 0.0))
    elif vx > 0:
        axis_steps.append((step_mm, 0.0))
    if vy < 0:
        axis_steps.append((0.0, -step_mm))
    elif vy > 0:
        axis_steps.append((0.0, step_mm))

    if axis_steps:
        order = list(range(len(all_polys_data)))

        def _grav_key(ii: int) -> float:
            b = all_polys_data[ii][2]
            dx = b[0] if vx < 0 else (-b[2] if vx > 0 else 0.0)
            dy = b[1] if vy < 0 else (-b[3] if vy > 0 else 0.0)
            return dx + dy

        order.sort(key=_grav_key)
        for i in order:
            item = all_polys_data[i][0]
            moved_slide = False
            for sx, sy in axis_steps:
                guard = 0
                while guard < 5000:
                    guard += 1
                    poly = all_polys_data[i][1]
                    test_poly = affinity.translate(poly, sx, sy)
                    tb = test_poly.bounds
                    if tb[0] < kerf_half - 1e-6 or tb[1] < kerf_half - 1e-6:
                        break
                    if placa_w > 0 and tb[2] > placa_w - kerf_half + 1e-6:
                        break
                    if placa_h > 0 and tb[3] > placa_h - kerf_half + 1e-6:
                        break
                    collision = False
                    try:
                        test_clear = test_poly.buffer(kerf_half, resolution=2, join_style=2)
                    except Exception:
                        test_clear = test_poly
                    tc_b = test_clear.bounds
                    for j in range(len(all_polys_data)):
                        if i == j:
                            continue
                        op_poly = all_polys_data[j][1]
                        op_bounds = all_polys_data[j][2]
                        if (
                            tc_b[2] <= op_bounds[0]
                            or tc_b[0] >= op_bounds[2]
                            or tc_b[3] <= op_bounds[1]
                            or tc_b[1] >= op_bounds[3]
                        ):
                            continue
                        try:
                            op_clear = op_poly.buffer(kerf_half, resolution=2, join_style=2)
                        except Exception:
                            op_clear = op_poly
                        if test_clear.intersects(op_clear) and not test_clear.touches(op_clear):
                            collision = True
                            break
                    if collision:
                        break
                    item["poly"] = test_poly
                    item["minx"] = tb[0]
                    item["miny"] = tb[1]
                    bp_sim = test_poly.simplify(2.0)
                    try:
                        new_u_verts = list(bp_sim.exterior.coords)
                    except Exception:
                        new_u_verts = [(tb[0], tb[1])]
                    bp_buf = test_poly.buffer(kerf_half, resolution=2)
                    all_polys_data[i][1] = test_poly
                    all_polys_data[i][2] = tb
                    all_polys_data[i][3] = new_u_verts
                    all_polys_data[i][4] = bp_buf
                    all_polys_data[i][5] = bp_buf.bounds
                    _translate_piece_data(item["p"], sx, sy)
                    mm_ahorrados_x += abs(sx)
                    mm_ahorrados_y += abs(sy)
                    moved_slide = True
            if moved_slide:
                nudges_totales += 1

    # Fase 3: huecos libres de placa (después de gravedad, para no deshacerlos).
    sheet_pockets = 0
    try:
        from .venom_hole_fill import fill_sheet_free_pockets

        sheet_pockets = int(fill_sheet_free_pockets(hoja, engine_id) or 0)
        if sheet_pockets > 0:
            # Refrescar items/polys tras reubicación
            piezas = hoja.get("piezas", [])
            items = []
            for idx, p in enumerate(piezas):
                poly = p.get("poly_exact") or p.get("poly")
                if poly is None and p.get("poligonos"):
                    poly = reconstruir_poly_seguro(p.get("poligonos") or [])
                if poly and hasattr(poly, "bounds") and not poly.is_empty:
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if hasattr(poly, "geoms"):
                        poly = max(poly.geoms, key=lambda g: g.area)
                    if poly.is_valid:
                        items.append(
                            {
                                "idx": idx,
                                "poly": poly,
                                "p": p,
                                "minx": poly.bounds[0],
                                "miny": poly.bounds[1],
                            }
                        )
    except Exception:
        sheet_pockets = 0

    compact_post = compute_compactness(hoja, items)
    reward = float(compact_post["compactness_score"] - compact_pre["compactness_score"])
    if reward > 0.01:
        reward += 0.001 * (mm_ahorrados_x + mm_ahorrados_y)
    fill_n = int(fill_stats.get("filled") or 0)
    if fill_n > 0:
        reward += 10.0 * fill_n + 0.0001 * float(fill_stats.get("area_filled") or 0.0)
    strip_n = int(fill_stats.get("strip_packed") or 0)
    if strip_n > 0:
        reward += 1.5 * strip_n
    if sheet_pockets > 0:
        reward += 2.0 * sheet_pockets

    # Señal de aprendizaje útil: ¿el nido quedó más compacto de verdad?
    # (antes el bonus de fill tapaba peores layouts)
    bbox_delta = float(compact_pre["bbox_area"] - compact_post["bbox_area"])
    if bbox_delta > 0:
        reward += min(40.0, bbox_delta / max(placa_w * placa_h, 1.0) * 200.0)
    elif bbox_delta < -1e3:
        reward -= min(30.0, (-bbox_delta) / max(placa_w * placa_h, 1.0) * 150.0)

    # Pokayoke final: si hay solape metal, revertir TODO Venom (fill+gravity).
    try:
        from .sheet_integrity import hoja_tiene_solapes_metal

        has_ov, detail = hoja_tiene_solapes_metal(hoja, min_area_mm2=0.05)
        if has_ov:
            hoja["piezas"] = snapshot_piezas
            hoja["venom_reverted"] = True
            hoja["venom_revert_reason"] = detail
            fill_stats = {"filled": 0, "area_filled": 0.0}
            reward = -50.0
            log_msg = (
                f"[VENOM-AI] Motor: {engine_id} | REVERTIDO por solape post-polish | {detail}"
            )
            try:
                print(log_msg)
            except Exception:
                pass
            try:
                from datetime import datetime
                from pathlib import Path

                log_path = Path(__file__).parent.parent.parent / "_logs" / "AI_ACTIVITY.txt"
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {log_msg}\n")
            except Exception:
                pass
            try:
                from .hive_mind_nests import publish_nest_learning

                hoja["venom_reward"] = reward
                publish_nest_learning(engine_id=str(engine_id or "default"), hoja=hoja)
            except Exception:
                pass
            return
    except Exception:
        pass

    hoja["venom_compactness_pre"] = compact_pre["compactness_score"]
    hoja["venom_compactness_post"] = compact_post["compactness_score"]
    hoja["venom_reward"] = reward
    hoja["venom_improvement"] = reward
    hoja["venom_bbox_pre"] = compact_pre["bbox_area"]
    hoja["venom_bbox_post"] = compact_post["bbox_area"]
    hoja["venom_reverted"] = False

    vec_str = f"({vx:.1f}, {vy:.1f})"
    estrategias = weights.get("estrategias", {})
    if vec_str not in estrategias:
        estrategias[vec_str] = {"recompensa_acumulada": 0.0, "usos": 0}
    estrategias[vec_str]["recompensa_acumulada"] += reward
    estrategias[vec_str]["usos"] += 1
    weights["estrategias"] = estrategias
    # Decaimiento lento de exploración (techo amplio de mejora).
    weights["exploracion"] = max(
        0.12, float(weights.get("exploracion", 0.3) or 0.3) * 0.997
    )
    _save_venom_weights(engine_id, weights)

    # Colmena de nests: aprende de CUALQUIER motor que pase por Venom.
    try:
        from .hive_mind_nests import publish_nest_learning

        publish_nest_learning(engine_id=str(engine_id or "default"), hoja=hoja)
    except Exception:
        pass

    log_msg = (
        f"[VENOM-AI] Motor: {engine_id} | Gravedad ({vx:.1f}, {vy:.1f}) | "
        f"Coarse: {coarse_moved} | Nudges: {nudges_totales} | Fill: {fill_n} | "
        f"SheetPockets: {sheet_pockets} | "
        f"BBox: {compact_pre['bbox_area']:.0f}->{compact_post['bbox_area']:.0f} | "
        f"Compact: {compact_pre['compactness_score']:.1f}->{compact_post['compactness_score']:.1f} "
        f"(d{reward:+.2f})"
    )
    try:
        print(log_msg)
    except Exception:
        print(log_msg.encode("ascii", "replace").decode("ascii"))
    try:
        from datetime import datetime
        from pathlib import Path

        log_path = Path(__file__).parent.parent.parent / "_logs" / "AI_ACTIVITY.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {log_msg}\n")
    except Exception:
        pass
