"""Geometría de visualización 1:1 desde DXF fuente (mantiene acomodo del nest)."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from shapely import affinity

from .geometry_parser import (
    poligonos_desde_shapely,
    reconstruir_poly_seguro,
    recuperar_geometria_robusta,
)

_DXF_LOCAL_CACHE: dict[tuple[str, float], tuple] = {}
_DXF_CACHE_LOCK = threading.Lock()
_DXF_CACHE_MAX = 1024

_TRANSFORM_ROT_CACHE: dict[tuple, dict] = {}
_TRANSFORM_CACHE_LOCK = threading.Lock()
_TRANSFORM_CACHE_MAX = 8192

DXF_EXPORT_CACHE_VERSION = 1
_TRANSFORM_EXPORT_KEYS = (
    "rot_deg",
    "shift_x",
    "shift_y",
    "orig_minx",
    "orig_miny",
    "rot_origin_cx",
    "rot_origin_cy",
)


def _workers_default() -> int:
    try:
        n = int(os.environ.get("ARGA_DXF_PREP_WORKERS", "0") or "0")
        if n > 0:
            return min(n, 16)
    except ValueError:
        pass
    cpu = os.cpu_count() or 4
    return min(12, max(4, cpu))


def _cargar_poly_local_dxf(ruta: str):
    try:
        mtime = os.path.getmtime(ruta)
    except OSError:
        return None

    key = (ruta, mtime)
    with _DXF_CACHE_LOCK:
        cached = _DXF_LOCAL_CACHE.get(key)
    if cached is not None:
        return cached

    poly, marks = recuperar_geometria_robusta(ruta)
    if poly is None or getattr(poly, "is_empty", True):
        return None

    minx, miny, _, _ = poly.bounds
    poly_local = affinity.translate(poly, -minx, -miny)
    marks_local = marks
    if marks is not None and not getattr(marks, "is_empty", True):
        marks_local = affinity.translate(marks, -minx, -miny)

    cached = (poly_local, marks_local, float(minx), float(miny))
    with _DXF_CACHE_LOCK:
        _DXF_LOCAL_CACHE[key] = cached
        if len(_DXF_LOCAL_CACHE) > _DXF_CACHE_MAX:
            _DXF_LOCAL_CACHE.pop(next(iter(_DXF_LOCAL_CACHE)))
    return cached


def precalentar_cache_dxf(rutas: set[str], *, workers: int | None = None) -> int:
    """Precarga DXF únicos en paralelo (solo lectura + cache)."""
    paths = [r for r in rutas if r and os.path.isfile(r)]
    if not paths:
        return 0
    w = workers or _workers_default()

    def _load(r: str) -> bool:
        return _cargar_poly_local_dxf(r) is not None

    with ThreadPoolExecutor(max_workers=w) as ex:
        list(ex.map(_load, paths))
    return len(paths)


def _inferir_transformacion(p_orig: dict, pieza: dict):
    from .manager import _inferir_transformacion_desde_resultado

    return _inferir_transformacion_desde_resultado(p_orig, pieza)


def _origen_rotacion(poly_local):
    from .manager import _origen_rotacion_pieza

    return _origen_rotacion_pieza(poly_local)


def _nested_shape_sig(pieza: dict) -> tuple | None:
    """Huella de la pieza colocada (forma normalizada al origen, sin posición absoluta)."""
    nested = pieza.get("poligonos") or []
    if not nested or not nested[0] or len(nested[0]) < 3:
        return None
    ring = nested[0]
    xs = [float(p[0]) for p in ring]
    ys = [float(p[1]) for p in ring]
    nminx, nminy = min(xs), min(ys)
    step = max(1, len(ring) // 40)
    pts = tuple(
        (round(xs[i] - nminx, 1), round(ys[i] - nminy, 1))
        for i in range(0, len(ring), step)
    )
    n_holes = sum(1 for h in nested[1:] if h and len(h) >= 3)
    return (
        pts,
        n_holes,
        round(max(xs) - nminx, 1),
        round(max(ys) - nminy, 1),
    )


def _shift_desde_rotacion(poly_local, rot_deg: float, rot_origin, final_poly) -> tuple[float, float]:
    test_poly = affinity.rotate(poly_local, rot_deg, origin=rot_origin)
    tminx, tminy, _, _ = test_poly.bounds
    nminx, nminy, _, _ = final_poly.bounds
    return float(nminx - tminx), float(nminy - tminy)


def _transform_desde_pieza(pieza: dict) -> dict | None:
    if pieza.get("_transform_export_ok") or pieza.get("_geom_dxf_ok"):
        return {
            "rot_deg": float(pieza.get("rot_deg", 0.0) or 0.0),
            "shift_x": float(pieza.get("shift_x", 0.0) or 0.0),
            "shift_y": float(pieza.get("shift_y", 0.0) or 0.0),
        }
    return None


def poligonos_display_desde_dxf(pieza: dict) -> list | None:
    """
    Reconstruye anillos de la pieza colocada usando el DXF fuente (tolerancia CAD)
    y el acomodo actual (polígonos del nest). No altera pieza.
    """
    ruta = str(pieza.get("ruta") or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return None

    nested = pieza.get("poligonos") or []
    if not nested or not nested[0] or len(nested[0]) < 3:
        return None

    loaded = _cargar_poly_local_dxf(ruta)
    if loaded is None:
        return None
    poly_local, marks_local, orig_minx, orig_miny = loaded

    transform = _transform_desde_pieza(pieza)
    if not transform:
        p_orig = {
            "poly": poly_local,
            "poly_exact": poly_local,
            "marks": marks_local,
            "marks_exact": marks_local,
            "orig_minx": orig_minx,
            "orig_miny": orig_miny,
        }
        transform = _inferir_transformacion(p_orig, pieza)
    if not transform:
        final_poly = reconstruir_poly_seguro(nested)
        if final_poly is None or final_poly.is_empty:
            return None
        fnminx, fnminy, _, _ = final_poly.bounds
        placed = affinity.translate(poly_local, fnminx, fnminy)
        return poligonos_desde_shapely(placed)

    rot_origin = _origen_rotacion(poly_local)
    rotated = affinity.rotate(
        poly_local,
        float(transform.get("rot_deg", 0) or 0),
        origin=rot_origin,
    )
    placed = affinity.translate(
        rotated,
        float(transform.get("shift_x", 0) or 0),
        float(transform.get("shift_y", 0) or 0),
    )
    return poligonos_desde_shapely(placed)


def _es_pieza_virtual_nombre(nom: str) -> bool:
    n = str(nom or "")
    return n.startswith(
        ("REF__", "TATUAJE__", "RETAZO_GUILLOTINA__", "CU_CORTE__", "REMANENTE__")
    )


def _pieza_tiene_campos_transform(pieza: dict) -> bool:
    return all(k in pieza for k in _TRANSFORM_EXPORT_KEYS)


def _ruta_dxf_vigente(pieza: dict) -> bool:
    ruta = str(pieza.get("ruta") or "").strip()
    return bool(ruta and os.path.isfile(ruta))


def normalizar_sello_transform_export(pieza: dict) -> bool:
    """
    Marca _transform_export_ok si la pieza ya trae transform export completa
    y el AABB nest coincide con el DXF @ rot (evita sellos viejos desalineeados).
    """
    if not isinstance(pieza, dict) or _es_pieza_virtual_nombre(pieza.get("nombre")):
        return True
    if pieza.get("_transform_export_ok") or pieza.get("_geom_dxf_ok"):
        return True
    if not _ruta_dxf_vigente(pieza):
        return False
    nested = pieza.get("poligonos") or []
    if not nested or not nested[0] or len(nested[0]) < 3:
        return False
    if not _pieza_tiene_campos_transform(pieza):
        return False

    # Validar que el sello no sea un rot/shift inventado sobre polígono nest distinto.
    try:
        loaded = _cargar_poly_local_dxf(str(pieza.get("ruta") or "").strip())
        final_poly = reconstruir_poly_seguro(nested)
        if loaded is None or final_poly is None or final_poly.is_empty:
            return False
        poly_local, _, _, _ = loaded
        rot_origin = (
            float(pieza.get("rot_origin_cx", 0.0) or 0.0),
            float(pieza.get("rot_origin_cy", 0.0) or 0.0),
        )
        if abs(rot_origin[0]) < 1e-9 and abs(rot_origin[1]) < 1e-9:
            rot_origin = _origen_rotacion(poly_local)
        placed_bounds = _dxf_placed_bounds(
            poly_local,
            float(pieza.get("rot_deg", 0.0) or 0.0),
            rot_origin,
            float(pieza.get("shift_x", 0.0) or 0.0),
            float(pieza.get("shift_y", 0.0) or 0.0),
        )
        if not _aabb_size_match(final_poly.bounds, placed_bounds):
            invalidar_sello_transform_export(pieza)
            return False
    except Exception:
        return False

    pieza["_transform_export_ok"] = True
    return True


def invalidar_sello_transform_export(pieza: dict) -> None:
    if not isinstance(pieza, dict):
        return
    pieza.pop("_transform_export_ok", None)
    pieza.pop("_geom_dxf_ok", None)


def pieza_necesita_transform_export(pieza: dict) -> bool:
    """True si falta inferir rotación/traslación para export 1:1."""
    if not isinstance(pieza, dict) or _es_pieza_virtual_nombre(pieza.get("nombre")):
        return False
    if pieza.get("_transform_export_ok") or pieza.get("_geom_dxf_ok"):
        return False
    if normalizar_sello_transform_export(pieza):
        return False
    ruta = str(pieza.get("ruta") or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return False
    nested = pieza.get("poligonos") or []
    return bool(nested and nested[0] and len(nested[0]) >= 3)


def pieza_necesita_geom_dxf(pieza: dict) -> bool:
    """True si la pieza debe refrescar polígonos de display (arcos 1:1)."""
    if not isinstance(pieza, dict) or _es_pieza_virtual_nombre(pieza.get("nombre")):
        return False
    if pieza.get("_geom_dxf_ok"):
        return False
    ruta = str(pieza.get("ruta") or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return False
    nested = pieza.get("poligonos") or []
    return bool(nested and nested[0] and len(nested[0]) >= 3)


def _aabb_size_match(bounds_a, bounds_b, *, tol_mm: float | None = None) -> bool:
    """True si dos AABB tienen el mismo ancho/alto (rotación rígida del mismo DXF)."""
    if not bounds_a or not bounds_b:
        return False
    aw = float(bounds_a[2]) - float(bounds_a[0])
    ah = float(bounds_a[3]) - float(bounds_a[1])
    bw = float(bounds_b[2]) - float(bounds_b[0])
    bh = float(bounds_b[3]) - float(bounds_b[1])
    tol = float(tol_mm) if tol_mm is not None else max(8.0, 0.02 * max(aw, ah, bw, bh, 1.0))
    return abs(aw - bw) <= tol and abs(ah - bh) <= tol


def _dxf_placed_bounds(poly_local, rot_deg: float, rot_origin, shift_x: float, shift_y: float):
    rotated = affinity.rotate(poly_local, float(rot_deg), origin=rot_origin)
    placed = affinity.translate(rotated, float(shift_x), float(shift_y))
    if placed is None or getattr(placed, "is_empty", True):
        return None
    return placed.bounds


def completar_transform_export_pieza(pieza: dict) -> bool:
    """
    Infiera rotación y traslación desde DXF + polígonos colocados (export 1:1).
    Necesario cuando el nest viene de .arganest sin metadata de transformación.
    """
    if not isinstance(pieza, dict) or _es_pieza_virtual_nombre(pieza.get("nombre")):
        return False

    if pieza.get("_transform_export_ok") or pieza.get("_geom_dxf_ok"):
        return True

    ruta = str(pieza.get("ruta") or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return False

    nested = pieza.get("poligonos") or []
    if not nested or not nested[0] or len(nested[0]) < 3:
        return False

    loaded = _cargar_poly_local_dxf(ruta)
    if loaded is None:
        return False
    poly_local, marks_local, orig_minx, orig_miny = loaded

    final_poly = reconstruir_poly_seguro(nested)
    if final_poly is None or final_poly.is_empty:
        return False

    rot_origin = _origen_rotacion(poly_local)
    shape_sig = _nested_shape_sig(pieza)
    cache_key = None
    rot_cached = None
    if shape_sig is not None:
        try:
            mtime = os.path.getmtime(ruta)
            cache_key = (ruta, mtime, shape_sig)
            with _TRANSFORM_CACHE_LOCK:
                rot_cached = _TRANSFORM_ROT_CACHE.get(cache_key)
        except OSError:
            cache_key = None

    if rot_cached is not None:
        rot_deg = float(rot_cached.get("rot_deg", 0.0) or 0.0)
        shift_x, shift_y = _shift_desde_rotacion(
            poly_local, rot_deg, rot_origin, final_poly
        )
    else:
        p_orig = {
            "poly": poly_local,
            "poly_exact": poly_local,
            "marks": marks_local,
            "marks_exact": marks_local,
            "orig_minx": orig_minx,
            "orig_miny": orig_miny,
        }
        transform = _inferir_transformacion(p_orig, pieza)
        if not transform:
            return False
        rot_deg = float(transform.get("rot_deg", 0.0) or 0.0)
        shift_x = float(transform.get("shift_x", 0.0) or 0.0)
        shift_y = float(transform.get("shift_y", 0.0) or 0.0)
        if cache_key is not None:
            with _TRANSFORM_CACHE_LOCK:
                _TRANSFORM_ROT_CACHE[cache_key] = {"rot_deg": rot_deg}
                if len(_TRANSFORM_ROT_CACHE) > _TRANSFORM_CACHE_MAX:
                    _TRANSFORM_ROT_CACHE.pop(next(iter(_TRANSFORM_ROT_CACHE)))

    placed_bounds = _dxf_placed_bounds(
        poly_local, rot_deg, rot_origin, shift_x, shift_y
    )
    if not _aabb_size_match(final_poly.bounds, placed_bounds):
        # Nest vs DXF desalineeado: no sellar transform (export debe fallar claro).
        invalidar_sello_transform_export(pieza)
        return False

    pieza["orig_minx"] = float(orig_minx)
    pieza["orig_miny"] = float(orig_miny)
    pieza["rot_deg"] = rot_deg
    pieza["shift_x"] = shift_x
    pieza["shift_y"] = shift_y
    pieza["rot_origin_cx"] = float(rot_origin[0])
    pieza["rot_origin_cy"] = float(rot_origin[1])
    pieza["_transform_export_ok"] = True
    return True


def completar_transform_export_hoja(hoja: dict) -> int:
    if not isinstance(hoja, dict):
        return 0
    n = 0
    for pz in hoja.get("piezas") or []:
        if completar_transform_export_pieza(pz):
            n += 1
    return n


def _iou_poligonos(a_rings, b_rings) -> float:
    """IoU entre dos anillos externos (0..1). Fallos → 0."""
    try:
        pa = reconstruir_poly_seguro(a_rings)
        pb = reconstruir_poly_seguro(b_rings)
        if pa is None or pb is None or pa.is_empty or pb.is_empty:
            return 0.0
        inter = float(pa.intersection(pb).area)
        union = float(pa.union(pb).area)
        if union <= 1e-12:
            return 0.0
        return inter / union
    except Exception:
        return 0.0


def refrescar_poligonos_display_pieza(pieza: dict, *, force: bool = False) -> bool:
    """Sustituye poligonos en memoria por versión fiel al DXF (misma posición)."""
    if not isinstance(pieza, dict):
        return False
    if _es_pieza_virtual_nombre(pieza.get("nombre")):
        return False
    if not force and not pieza_necesita_geom_dxf(pieza):
        return False

    if not pieza.get("_transform_export_ok") and not pieza.get("_geom_dxf_ok"):
        if not completar_transform_export_pieza(pieza):
            return False

    nested_before = list(pieza.get("poligonos") or [])
    pols = poligonos_display_desde_dxf(pieza)
    if not pols:
        return False

    # Guardrail anti-empalme: si el DXF reubicado no coincide con el nest, NO reescribir.
    # (p. ej. rotación 45° mal inferida → polígonos cruzados en pantalla).
    iou = _iou_poligonos(nested_before, pols)
    if nested_before and iou < 0.92:
        return False

    pieza["poligonos"] = pols
    pieza["_geom_dxf_ok"] = True
    pieza.pop("_poly_cache", None)
    pieza.pop("_bounds_cache", None)
    return True


def _iter_piezas_multilote(multilote) -> list[dict]:
    out: list[dict] = []
    for lote in multilote or []:
        if not isinstance(lote, dict):
            continue
        data = lote.get("data")
        if not isinstance(data, dict):
            continue
        for info in data.values():
            if not isinstance(info, dict):
                continue
            for hoja in info.get("hojas") or []:
                if not isinstance(hoja, dict):
                    continue
                for pz in hoja.get("piezas") or []:
                    if isinstance(pz, dict):
                        out.append(pz)
    return out


def auditar_cache_dxf_multilote(multilote) -> dict:
    """Resume cuántas piezas ya tienen cache DXF export vs pendientes."""
    piezas = _iter_piezas_multilote(multilote)
    total = len(piezas)
    virtual = 0
    sin_ruta = 0
    transform_ok = 0
    display_ok = 0
    pendientes_transform = 0
    pendientes_display = 0

    for pz in piezas:
        nom = pz.get("nombre")
        if _es_pieza_virtual_nombre(nom):
            virtual += 1
            continue
        normalizar_sello_transform_export(pz)
        if not _ruta_dxf_vigente(pz):
            sin_ruta += 1
            continue
        if pz.get("_transform_export_ok") or pz.get("_geom_dxf_ok"):
            transform_ok += 1
        elif pieza_necesita_transform_export(pz):
            pendientes_transform += 1
        if pz.get("_geom_dxf_ok"):
            display_ok += 1
        elif pieza_necesita_geom_dxf(pz):
            pendientes_display += 1

    con_ruta = total - virtual - sin_ruta
    return {
        "version": DXF_EXPORT_CACHE_VERSION,
        "piezas_total": total,
        "piezas_virtual": virtual,
        "piezas_sin_ruta": sin_ruta,
        "piezas_con_ruta": con_ruta,
        "transform_ok": transform_ok,
        "display_ok": display_ok,
        "pendientes_transform": pendientes_transform,
        "pendientes_display": pendientes_display,
        "transform_ready": pendientes_transform == 0 and con_ruta > 0,
    }


def normalizar_sellos_dxf_en_multilote(multilote) -> dict:
    """Reconoce transform guardada en piezas legacy (sin flag explícito)."""
    return auditar_cache_dxf_multilote(multilote)


def asegurar_dxf_export_cache_para_guardar(
    multilote,
    *,
    log: Callable | None = None,
) -> dict:
    """
    Antes de guardar .arganest: completa transform export faltante y devuelve
    metadatos de cache para persistir en el archivo.
    """
    audit = normalizar_sellos_dxf_en_multilote(multilote)
    if audit["pendientes_transform"] > 0:
        if log:
            log(
                f"Sellando transform export: {audit['pendientes_transform']} "
                f"pieza(s) pendiente(s)…",
                phase="SAVE",
            )
        preparar_transform_export_multilote_paralelo(multilote, log=log)
        audit = auditar_cache_dxf_multilote(multilote)

    for pz in _iter_piezas_multilote(multilote):
        normalizar_sello_transform_export(pz)

    audit["transform_ready"] = audit["pendientes_transform"] == 0
    return audit


def _preparar_paralelo(
    piezas: list[dict],
    *,
    pending_fn: Callable[[dict], bool],
    worker_fn: Callable[[dict], bool],
    workers: int | None = None,
    log: Callable | None = None,
    log_every: int = 100,
    phase: str = "DXF",
    precalentar: bool = True,
) -> dict:
    pending = [pz for pz in piezas if pending_fn(pz)]
    skipped = len(piezas) - len(pending)
    if not pending:
        return {
            "piezas": len(piezas),
            "pendientes": 0,
            "omitidas": skipped,
            "ok": 0,
            "geom_ok": 0,
        }

    rutas = {str(pz.get("ruta") or "").strip() for pz in pending}
    rutas.discard("")
    w = workers or _workers_default()
    if precalentar and log:
        log(
            f"Precalentando {len(rutas)} DXF único(s) con {w} workers…",
            phase=phase,
        )
    if precalentar:
        precalentar_cache_dxf(rutas, workers=w)

    ok = 0
    t0 = __import__("time").perf_counter()
    with ThreadPoolExecutor(max_workers=w) as ex:
        futures = [ex.submit(worker_fn, pz) for pz in pending]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                if fut.result():
                    ok += 1
            except Exception:
                pass
            if log and (i % log_every == 0 or i == len(pending)):
                elapsed = __import__("time").perf_counter() - t0
                rate = i / elapsed if elapsed > 0 else 0
                log(
                    f"  {phase} {i}/{len(pending)} ok={ok} "
                    f"({rate:.0f} piezas/s, omitidas_prev={skipped})",
                    phase=phase,
                )

    return {
        "piezas": len(piezas),
        "pendientes": len(pending),
        "omitidas": skipped,
        "ok": ok,
        "geom_ok": ok,
        "workers": w,
        "dxf_unicos": len(rutas),
    }


def preparar_transform_export_piezas_paralelo(
    piezas: list[dict],
    *,
    workers: int | None = None,
    log: Callable | None = None,
    log_every: int = 100,
) -> dict:
    """Solo rotación/traslación para export (sin reconstruir polígonos de display)."""
    return _preparar_paralelo(
        piezas,
        pending_fn=pieza_necesita_transform_export,
        worker_fn=completar_transform_export_pieza,
        workers=workers,
        log=log,
        log_every=log_every,
        phase="TRANSFORM",
        precalentar=True,
    )


def preparar_geom_piezas_paralelo(
    piezas: list[dict],
    *,
    workers: int | None = None,
    log: Callable | None = None,
    log_every: int = 100,
) -> dict:
    """Refina polígonos de display 1:1 en paralelo."""
    return _preparar_paralelo(
        piezas,
        pending_fn=pieza_necesita_geom_dxf,
        worker_fn=refrescar_poligonos_display_pieza,
        workers=workers,
        log=log,
        log_every=log_every,
        phase="DISPLAY",
        precalentar=False,
    )


def refrescar_poligonos_display_hoja(hoja: dict) -> int:
    if not isinstance(hoja, dict):
        return 0
    n = 0
    for pz in hoja.get("piezas") or []:
        if refrescar_poligonos_display_pieza(pz):
            n += 1
    return n


def refrescar_poligonos_display_resultados(resultados: dict) -> int:
    """Una sola pasada sobre todas las hojas (carga nest / fin de nesting)."""
    if not isinstance(resultados, dict):
        return 0
    piezas = []
    for info in resultados.values():
        if not isinstance(info, dict):
            continue
        for hoja in info.get("hojas") or []:
            if isinstance(hoja, dict):
                piezas.extend(hoja.get("piezas") or [])
    stats = preparar_geom_piezas_paralelo(piezas)
    return int(stats.get("geom_ok", 0) or 0)


def refrescar_poligonos_display_multilote(multilote) -> int:
    piezas = _iter_piezas_multilote(multilote)
    stats = preparar_geom_piezas_paralelo(piezas)
    return int(stats.get("geom_ok", 0) or 0)


def preparar_transform_export_multilote_paralelo(
    multilote,
    *,
    log: Callable | None = None,
) -> dict:
    """Transformaciones export 1:1 en paralelo (rápido, sin refresh de display)."""
    piezas = _iter_piezas_multilote(multilote)
    log_every = 100 if len(piezas) > 500 else 50
    return preparar_transform_export_piezas_paralelo(piezas, log=log, log_every=log_every)


def preparar_geom_multilote_paralelo(
    multilote,
    *,
    log: Callable | None = None,
) -> dict:
    """Refinado display 1:1 paralelo sobre todo el multilote."""
    piezas = _iter_piezas_multilote(multilote)
    log_every = 50 if len(piezas) > 500 else 25
    return preparar_geom_piezas_paralelo(piezas, log=log, log_every=log_every)
