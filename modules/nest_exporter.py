# modules/nest_exporter.py
import os
import math
import re
import time
import uuid
from functools import lru_cache

import ezdxf
from ezdxf.addons import Importer
from ezdxf.math import Matrix44
from ezdxf import colors  
import config

from modules.dxf_native_curves import export_ring_native, normalize_ring
from modules.nesting_engine.geometry_parser import ESCALA_DXF, _clasificar_capa
from modules.nesting_engine.cu_largos_nesting import TOL_GEOM_MM
from freecad_runner import ejecutar_macro_freecad

# Tolerancias de validación (fallar antes de escribir DXF incorrecto).
ALIGN_TOL_MM = 8.0
SHEET_MARGIN_MM = 2.0
PRODUCTION_LAYERS = frozenset({"CUT_OUTER", "CUT_INNER", "CUT_CU"})
NATIVE_PROD_TYPES = frozenset({"LINE", "ARC", "CIRCLE"})
COBRE_CUT_CU_TYPES = frozenset({"LWPOLYLINE"})
COBRE_CUT_OUTER_STEP_TYPES = frozenset({"LWPOLYLINE"})


class DxfExportValidationError(RuntimeError):
    """La exportación se aborta: el DXF no sería 1:1 válido para láser."""


def _piece_label(p: dict) -> str:
    return str(p.get("part_name", p.get("name", "PIEZA")))


def _entities_bbox(entities) -> tuple[float, float, float, float] | None:
    if not entities:
        return None
    try:
        from ezdxf import bbox as ezdxf_bbox

        ext = ezdxf_bbox.extents(entities)
        return (
            float(ext.extmin.x),
            float(ext.extmin.y),
            float(ext.extmax.x),
            float(ext.extmax.y),
        )
    except Exception:
        return None


def _inside_sheet_bounds(
    bbox: tuple[float, float, float, float],
    sheet_len: float,
    sheet_w: float,
    *,
    margin: float = SHEET_MARGIN_MM,
) -> bool:
    if not bbox or sheet_len <= 0 or sheet_w <= 0:
        return True
    return (
        float(bbox[0]) >= -margin
        and float(bbox[1]) >= -margin
        and float(bbox[2]) <= float(sheet_len) + margin
        and float(bbox[3]) <= float(sheet_w) + margin
    )


def _placement_origin_mm(p: dict) -> tuple[float, float]:
    """
    Origen local (mm) para colocar el DXF en la placa.
    Cobre largos: mismo origen que el parser/nest (poly.bounds), no solo CUT_OUTER del archivo.
    """
    if bool(p.get("cu_largos_piece")):
        return (
            float(p.get("orig_minx", 0.0) or 0.0),
            float(p.get("orig_miny", 0.0) or 0.0),
        )
    ruta = str(p.get("ruta") or "").strip()
    dxf_orig = _dxf_outer_origin_mm(ruta) if ruta else None
    if dxf_orig:
        return dxf_orig
    return (
        float(p.get("orig_minx", 0.0) or 0.0),
        float(p.get("orig_miny", 0.0) or 0.0),
    )


def _sheet_omits_cut_cu(sheet: dict | None, p: dict | None = None) -> bool:
    """DXF nest cobre: contorno en CUT_OUTER (nunca CUT_CU; STEP y CyPTube)."""
    if isinstance(p, dict) and p.get("omit_cut_cu") is not None:
        return bool(p.get("omit_cut_cu"))
    if isinstance(sheet, dict) and sheet.get("modo_largos_cu"):
        return True
    if not isinstance(sheet, dict):
        return False
    fmt = str(sheet.get("export_3d_format") or "").strip().lower()
    if fmt in ("dxf", "none", "2d"):
        return True
    return (
        str(sheet.get("cu_modo_separacion_barra") or "").strip().lower() == "sin_gap"
    )


def _sheet_cu_exporta_cortes_segmentados(sheet: dict | None) -> bool:
    """True solo sin_gap (CyPTube): clon 1:1 del DXF fuente + guillotina final de barra."""
    return _sheet_is_sin_gap(sheet)


def _sheet_cu_allows_closed_cut_outer_poly(sheet: dict | None) -> bool:
    """con_gap / STEP: 1 LWPOLYLINE cerrada CUT_OUTER por pieza (no segmentos láser)."""
    if not isinstance(sheet, dict) or not sheet.get("modo_largos_cu"):
        return False
    return not _sheet_cu_exporta_cortes_segmentados(sheet)


def _sheet_allows_amada_closed_poly(sheet: dict | None) -> bool:
    """AMADA/FIXTURA: islas cerradas (LWPOLYLINE/CIRCLE) con colchón +10\"."""
    return isinstance(sheet, dict) and bool(sheet.get("cu_export_amada"))


def _validate_production_entities(
    entities,
    p: dict,
    *,
    sheet_len: float,
    sheet_w: float,
    solo_cobre: bool,
    sheet: dict | None = None,
) -> None:
    """Solo tipos nativos de corte; sin comparar bbox vs nest (cobre largos exporta segmentos parciales)."""
    prod = [
        e
        for e in entities
        if str(getattr(e.dxf, "layer", "") or "") in PRODUCTION_LAYERS
    ]
    if not prod:
        return

    label = _piece_label(p)
    allow_outer_poly = solo_cobre and _sheet_cu_allows_closed_cut_outer_poly(sheet)
    allow_amada_poly = solo_cobre and _sheet_allows_amada_closed_poly(sheet)
    for ent in prod:
        typ = ent.dxftype()
        layer_u = str(getattr(ent.dxf, "layer", "") or "").upper()
        if solo_cobre and layer_u == "CUT_CU" and typ == "LWPOLYLINE":
            if not bool(getattr(ent, "closed", False) or ent.closed):
                raise DxfExportValidationError(
                    f"{label}: CUT_CU debe ser LWPOLYLINE cerrada (1 por pieza para STEP)."
                )
            continue
        if (
            allow_outer_poly
            and layer_u == "CUT_OUTER"
            and typ in COBRE_CUT_OUTER_STEP_TYPES
        ):
            if not bool(getattr(ent, "closed", False) or ent.closed):
                raise DxfExportValidationError(
                    f"{label}: CUT_OUTER debe ser LWPOLYLINE cerrada (1 por pieza STEP)."
                )
            continue
        if (
            allow_amada_poly
            and layer_u in ("CUT_OUTER", "CUT_INNER")
            and typ == "LWPOLYLINE"
        ):
            if not bool(getattr(ent, "closed", False) or ent.closed):
                raise DxfExportValidationError(
                    f"{label}: {layer_u} Amada debe ser LWPOLYLINE cerrada."
                )
            continue
        if typ not in NATIVE_PROD_TYPES:
            raise DxfExportValidationError(
                f"{label}: geometría de corte no nativa ({typ} en {ent.dxf.layer}). "
                f"Se requieren LINE/ARC/CIRCLE 1:1 del AutoDXF."
            )


def _validate_dxf_document(doc) -> None:
    from ezdxf.audit import Auditor

    auditor = Auditor(doc)
    auditor.run()
    if auditor.errors:
        first = str(auditor.errors[0])
        raise DxfExportValidationError(
            f"DXF inválido tras exportación (audit): {first}"
        )


def _validate_full_sheet(
    msp,
    *,
    sheet_len: float,
    sheet_w: float,
    solo_cobre: bool,
    skip_bounds: bool = False,
    sheet: dict | None = None,
) -> None:
    prod = [
        e
        for e in msp
        if str(getattr(e.dxf, "layer", "") or "") in PRODUCTION_LAYERS
    ]
    if not prod:
        raise DxfExportValidationError(
            "La hoja no tiene geometría de corte (CUT_OUTER/CUT_INNER)."
        )
    allow_outer_poly = solo_cobre and _sheet_cu_allows_closed_cut_outer_poly(sheet)
    allow_amada_poly = solo_cobre and _sheet_allows_amada_closed_poly(sheet)
    for ent in prod:
        typ = ent.dxftype()
        layer_u = str(getattr(ent.dxf, "layer", "") or "").upper()
        if solo_cobre and layer_u == "CUT_CU":
            if typ not in COBRE_CUT_CU_TYPES:
                raise DxfExportValidationError(
                    f"CUT_CU no aislado por pieza ({typ}). "
                    f"Se requiere INSERT o LWPOLYLINE cerrada por pieza."
                )
            continue
        if (
            allow_outer_poly
            and layer_u == "CUT_OUTER"
            and typ in COBRE_CUT_OUTER_STEP_TYPES
        ):
            if not bool(getattr(ent, "closed", False) or ent.closed):
                raise DxfExportValidationError(
                    "CUT_OUTER debe ser LWPOLYLINE cerrada por pieza (STEP cobre)."
                )
            continue
        if (
            allow_amada_poly
            and layer_u in ("CUT_OUTER", "CUT_INNER")
            and typ == "LWPOLYLINE"
        ):
            if not bool(getattr(ent, "closed", False) or ent.closed):
                raise DxfExportValidationError(
                    f"{layer_u} Amada debe ser LWPOLYLINE cerrada."
                )
            continue
        if typ not in NATIVE_PROD_TYPES:
            raise DxfExportValidationError(
                f"Geometría de corte no nativa en capa {ent.dxf.layer} ({typ})."
            )
    bbox = _entities_bbox(prod)
    if (
        not skip_bounds
        and bbox
        and not _inside_sheet_bounds(bbox, sheet_len, sheet_w)
    ):
        raise DxfExportValidationError(
            f"Geometría de corte fuera de la "
            f"{'barra' if solo_cobre else 'placa'} "
            f"({sheet_len:.1f}×{sheet_w:.1f} mm): "
            f"{tuple(round(v, 1) for v in bbox)}."
        )


def _placement_needs_strict_source(p: dict) -> bool:
    if bool(p.get("compensated")):
        return False
    if not bool(p.get("prefer_source_dxf")):
        return False
    ruta = str(p.get("ruta") or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return False
    outer = p.get("outer") or p.get("outer_poly") or []
    return len(outer) >= 2


def _msp_snapshot(msp) -> list:
    return list(msp)


def _msp_count(msp) -> int:
    return len(_msp_snapshot(msp))


def _fail_export(part_name: str, reason: str) -> None:
    raise DxfExportValidationError(f"{part_name}: {reason}")


def _production_layer_entities(msp):
    """Entidades que definen la vista/producción (excluye ARGA_META / ruido)."""
    keep_prefixes = ("CUT_",)
    keep_exact = {"MARK", "FIXTURE", "PLATE", "BAR_START"}
    out = []
    for e in msp:
        layer = str(getattr(e.dxf, "layer", "") or "").upper()
        if layer.startswith(keep_prefixes) or layer in keep_exact:
            out.append(e)
    return out


def _sync_dxf_header_view(doc) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """
    AutoCAD/TrueView: ezdxf deja EXTMIN/EXTMAX en ±1e20 y LIMMAX en A4.
    Sin bbox real la vista inicial queda vacía (pantalla negra hasta ZOOM E).
    También ajusta VPORT *Active (center/height) — AutoCAD ignora EXT* al abrir
    si el viewport activo apunta a (0,0) con altura default.

    Cobre vs acero: el acero dibuja Plate (marco de placa) y el zoom “cae” bien;
    el cobre omite Plate y las barras son largas (hasta 144\") — si LIMMAX queda
    en A4 (420×297) AutoCAD abre mirando vacío. Por eso el sync/VPORT va en
    _save_dxf_atomic (ambos caminos), con bbox solo de capas de corte.
    """
    try:
        from ezdxf import bbox as ezdxf_bbox

        msp = doc.modelspace()
        prod = _production_layer_entities(msp)
        ext = ezdxf_bbox.extents(prod if prod else msp)
        if not ext.has_data:
            return None
        mn, mx = ext.extmin, ext.extmax
        # saveas() copia extmin/extmax del layout al header; sin esto no persiste.
        msp.dxf.extmin = mn
        msp.dxf.extmax = mx
        try:
            layout = doc.active_layout()
            layout.dxf.extmin = mn
            layout.dxf.extmax = mx
        except Exception:
            pass
        doc.header["$EXTMIN"] = (float(mn.x), float(mn.y), float(mn.z))
        doc.header["$EXTMAX"] = (float(mx.x), float(mx.y), float(mx.z))
        pad = 10.0
        limmin = (float(mn.x) - pad, float(mn.y) - pad)
        limmax = (float(mx.x) + pad, float(mx.y) + pad)
        doc.header["$LIMMIN"] = limmin
        doc.header["$LIMMAX"] = limmax
        doc.header["$LIMCHECK"] = 0
        if int(doc.header.get("$INSUNITS", 0) or 0) == 0:
            doc.header["$INSUNITS"] = 4
        _fit_active_vport(doc, mn, mx)
        return limmin, limmax
    except Exception:
        return None


def _fit_active_vport(doc, mn, mx) -> None:
    """Centra VPORT *Active en el bbox (equivalente a ZOOM Extents al abrir)."""
    cx = (float(mn.x) + float(mx.x)) / 2.0
    cy = (float(mn.y) + float(mx.y)) / 2.0
    span_x = max(float(mx.x) - float(mn.x), 1.0)
    span_y = max(float(mx.y) - float(mn.y), 1.0)
    # Altura con margen; aspect del viewport tipico ~1.3–1.8.
    height = max(span_y * 1.15, span_x * 1.15 / 1.34)
    try:
        for vp in doc.viewports:
            if str(getattr(vp.dxf, "name", "") or "") in ("*Active", "*ACTIVE", ""):
                vp.dxf.center = (cx, cy, 0.0)
                vp.dxf.height = float(height)
    except Exception:
        pass
    try:
        for entry in doc.tables.viewports:
            name = str(getattr(entry.dxf, "name", "") or "")
            if name in ("*Active", "*ACTIVE"):
                entry.dxf.center = (cx, cy)
                entry.dxf.height = float(height)
    except Exception:
        pass


def _patch_dxf_limits_on_disk(
    path: str,
    limmin: tuple[float, float],
    limmax: tuple[float, float],
) -> None:
    """ezdxf saveas() resetea $LIMMIN/$LIMMAX a A4 aunque el header en memoria sea correcto."""
    import re
    from pathlib import Path

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for tag, (x, y) in (("$LIMMIN", limmin), ("$LIMMAX", limmax)):
        pat = (
            rf"({re.escape(tag)}\s*\r?\n\s*10\s*\r?\n)([^\r\n]+)"
            rf"(\s*\r?\n\s*20\s*\r?\n)([^\r\n]+)"
        )
        text, n = re.subn(pat, rf"\g<1>{float(x)}\g<3>{float(y)}", text, count=1)
        if n == 0:
            return
    try:
        p.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _save_dxf_atomic(doc, out_path: str) -> None:
    """Escribe DXF vía temporal + reemplazo atómico (tolerante a locks breves)."""
    out_path = os.path.abspath(str(out_path))
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_path = os.path.join(
        out_dir,
        f".__arga_export_{os.getpid()}_{uuid.uuid4().hex[:8]}.dxf",
    )
    try:
        limits = _sync_dxf_header_view(doc)
        doc.saveas(tmp_path)
        if limits is not None:
            _patch_dxf_limits_on_disk(tmp_path, limits[0], limits[1])
        _strip_ezdxf_fingerprint_on_disk(tmp_path)
        last_err: OSError | None = None
        for attempt in range(10):
            try:
                os.replace(tmp_path, out_path)
                return
            except OSError as exc:
                last_err = exc
                if exc.errno in (13, 16) or getattr(exc, "winerror", None) in (5, 32):
                    time.sleep(0.4 * (attempt + 1))
                    continue
                break
        nombre = os.path.basename(out_path)
        raise DxfExportValidationError(
            f"No se pudo guardar {nombre}: {last_err}. "
            "El archivo está en uso — cierra Inventor, AutoCAD o FreeCAD si lo tienen abierto "
            "y espera a que OneDrive termine de sincronizar."
        ) from last_err
    finally:
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

# =========================================================
# NEST DXF EXPORTER
# =========================================================

def _ensure_closed(poly):
    if not poly or len(poly) < 2:
        return poly
    if poly[0] != poly[-1]:
        return poly + [poly[0]]
    return poly

def _rotate_point(x, y, deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (x * c - y * s, x * s + y * c)

def _transform_poly(poly, tx=0.0, ty=0.0, rot_deg=0.0):
    out = []
    # 🚀 BLINDAJE: Validación de estructura de lista
    if not poly or not isinstance(poly, (list, tuple)): 
        return out
        
    for pt in poly:
        # 🚀 BLINDAJE: Evita 'tuple index out of range' en retazos incompletos
        if not isinstance(pt, (list, tuple)) or len(pt) < 2: 
            continue
            
        x, y = pt[0], pt[1]
        xr, yr = _rotate_point(x, y, rot_deg) if rot_deg else (x, y)
        out.append((xr + tx, yr + ty))
    return out

def _poly_bounds(poly):
    if not poly:
        return None
    xs, ys = [], []
    for pt in poly:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _bounds_close(a, b, tol=1.2):
    if a is None or b is None:
        return False
    return all(abs(float(a[i]) - float(b[i])) <= tol for i in range(4))


@lru_cache(maxsize=128)
def _dxf_outer_origin_mm_cached(ruta: str, mtime_ns: int) -> tuple[float, float] | None:
    """Origen local (mm) del contorno exterior en el DXF — igual que el visor hifi."""
    try:
        part_doc = ezdxf.readfile(ruta)
    except Exception:
        return None

    minx = miny = float("inf")
    found = False
    for entity in part_doc.modelspace():
        if entity.dxftype() not in (
            "LINE",
            "LWPOLYLINE",
            "POLYLINE",
            "ARC",
            "CIRCLE",
            "ELLIPSE",
            "SPLINE",
        ):
            continue
        if _clasificar_capa(str(entity.dxf.layer)) != "outer":
            continue
        try:
            from ezdxf import bbox as ezdxf_bbox

            ext = ezdxf_bbox.extents([entity])
            minx = min(minx, float(ext.extmin.x) * ESCALA_DXF)
            miny = min(miny, float(ext.extmin.y) * ESCALA_DXF)
            found = True
        except Exception:
            continue
    if not found or not math.isfinite(minx):
        return None
    return (minx, miny)


def _dxf_outer_origin_mm(ruta: str) -> tuple[float, float] | None:
    ruta = str(ruta or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return None
    try:
        mtime_ns = os.stat(ruta).st_mtime_ns
    except OSError:
        return None
    return _dxf_outer_origin_mm_cached(ruta, mtime_ns)


def _transformed_outer_bounds(part_doc, m) -> tuple[float, float, float, float] | None:
    from ezdxf import bbox as ezdxf_bbox

    staged = []
    for entity in part_doc.modelspace():
        if _clasificar_capa(str(entity.dxf.layer)) != "outer":
            continue
        try:
            ent = entity.copy()
            if ent.transform(m):
                staged.append(ent)
        except Exception:
            continue
    if not staged:
        return None
    try:
        ext = ezdxf_bbox.extents(staged)
        return (
            float(ext.extmin.x),
            float(ext.extmin.y),
            float(ext.extmax.x),
            float(ext.extmax.y),
        )
    except Exception:
        return None


def _resolve_placement(p: dict) -> dict:
    """Completa shift desde el bbox del contorno colocado (p. ej. largos CU sin metadata 2D)."""
    out = dict(p)
    bounds = _poly_bounds(out.get("outer") or out.get("outer_poly"))
    if bounds:
        sx = float(out.get("shift_x", 0.0) or 0.0)
        sy = float(out.get("shift_y", 0.0) or 0.0)
        if abs(sx) < 1e-6 and abs(sy) < 1e-6:
            out["shift_x"] = bounds[0]
            out["shift_y"] = bounds[1]
    return out


def _refine_placement_matrix(
    part_doc, outer_bounds, m, *, tol: float = 3.0, sheet: dict | None = None
) -> Matrix44:
    """
    Ajusta solo traslación para que el bbox del DXF fuente coincida con el nest.
    El nest define posición; el DXF fuente conserva arcos/círculos nativos.
    """
    if not outer_bounds:
        return m
    align_bounds = _nest_bounds_for_matrix_check(outer_bounds, sheet)
    src_bounds = _transformed_outer_bounds(part_doc, m)
    if not src_bounds:
        return m
    if _position_close(align_bounds, src_bounds, tol=max(tol, ALIGN_TOL_MM)):
        return m
    dx = float(align_bounds[0]) - float(src_bounds[0])
    dy = float(align_bounds[1]) - float(src_bounds[1])
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return m
    m2 = m.copy()
    m2 @= Matrix44.translate(dx, dy, 0)
    return m2


def _size_close(outer_bounds, src_bounds, tol: float = 3.0) -> bool:
    if not outer_bounds or not src_bounds:
        return True
    ow = float(outer_bounds[2]) - float(outer_bounds[0])
    oh = float(outer_bounds[3]) - float(outer_bounds[1])
    sw = float(src_bounds[2]) - float(src_bounds[0])
    sh = float(src_bounds[3]) - float(src_bounds[1])
    return abs(ow - sw) <= tol and abs(oh - sh) <= tol


def _position_close(outer_bounds, src_bounds, tol: float = 5.0) -> bool:
    if not outer_bounds or not src_bounds:
        return False
    return (
        abs(float(outer_bounds[0]) - float(src_bounds[0])) <= tol
        and abs(float(outer_bounds[1]) - float(src_bounds[1])) <= tol
    )


def _size_ratio_sane(outer_bounds, src_bounds, *, max_ratio: float = 1.2) -> bool:
    if not outer_bounds or not src_bounds:
        return True
    ow = max(float(outer_bounds[2]) - float(outer_bounds[0]), 1e-6)
    oh = max(float(outer_bounds[3]) - float(outer_bounds[1]), 1e-6)
    sw = float(src_bounds[2]) - float(src_bounds[0])
    sh = float(src_bounds[3]) - float(src_bounds[1])
    if sw <= 0 or sh <= 0:
        return False
    return (ow / max_ratio <= sw <= ow * max_ratio) and (oh / max_ratio <= sh <= oh * max_ratio)


def _matrix_maps_nest_bounds(
    part_doc, m, outer_bounds, *, tol: float = 5.0, sheet: dict | None = None
) -> bool:
    src_bounds = _transformed_outer_bounds(part_doc, m)
    if not outer_bounds or not src_bounds:
        return False
    align_bounds = _nest_bounds_for_matrix_check(outer_bounds, sheet)
    if not _position_close(align_bounds, src_bounds, tol=tol):
        return False
    return _size_ratio_sane(align_bounds, src_bounds)


def _resolve_placement_matrix(
    part_doc, p: dict, sheet: dict | None = None
) -> Matrix44:
    """Elige la transformación que alinea el DXF fuente con el contorno colocado en el nest."""
    outer_bounds = _poly_bounds(p.get("outer") or p.get("outer_poly"))
    resolved = _resolve_placement(p)
    candidates: list[Matrix44] = []

    def _add_candidate(placement: dict) -> None:
        candidates.append(_build_placement_matrix(placement, sheet))

    ruta = str(resolved.get("ruta") or p.get("ruta") or "").strip()
    if outer_bounds and ruta:
        forced = dict(resolved)
        if not bool(p.get("cu_largos_piece")):
            dxf_orig = _dxf_outer_origin_mm(ruta)
            if dxf_orig:
                forced["orig_minx"], forced["orig_miny"] = dxf_orig
        forced["shift_x"] = float(outer_bounds[0])
        forced["shift_y"] = float(outer_bounds[1])
        _add_candidate(forced)

    _add_candidate(resolved)
    if outer_bounds:
        anchored = dict(resolved)
        anchored["shift_x"] = float(outer_bounds[0])
        anchored["shift_y"] = float(outer_bounds[1])
        _add_candidate(anchored)
        raw = dict(p)
        if float(raw.get("shift_x", 0.0) or 0.0) or float(raw.get("shift_y", 0.0) or 0.0):
            _add_candidate(_resolve_placement(raw))

    seen: set[tuple] = set()
    best = candidates[0] if candidates else _build_placement_matrix(resolved, sheet)
    align_bounds = _nest_bounds_for_matrix_check(outer_bounds, sheet) if outer_bounds else None
    for m in candidates:
        m2 = _refine_placement_matrix(part_doc, outer_bounds, m, sheet=sheet)
        src_bounds = _transformed_outer_bounds(part_doc, m2)
        sig = tuple(round(float(v), 2) for v in (src_bounds or (0.0, 0.0, 0.0, 0.0)))
        if sig in seen:
            continue
        seen.add(sig)
        if align_bounds and src_bounds:
            oh = float(align_bounds[3]) - float(align_bounds[1])
            sh = float(src_bounds[3]) - float(src_bounds[1])
            if oh > 1.0 and sh > oh * 2.5:
                continue
        if not outer_bounds or _matrix_maps_nest_bounds(
            part_doc, m2, outer_bounds, tol=ALIGN_TOL_MM, sheet=sheet
        ):
            return m2
        best = m2
    m_final = _refine_placement_matrix(part_doc, outer_bounds, best, sheet=sheet)
    return m_final


def _write_native_entity(msp, entity, layer: str) -> int:
    """Escribe LINE/ARC/CIRCLE nativos; expande LWPOLYLINE a arcos/líneas reales."""
    typ = entity.dxftype()
    if typ == "LINE":
        s, en = entity.dxf.start, entity.dxf.end
        if math.hypot(float(en.x) - float(s.x), float(en.y) - float(s.y)) < 1e-6:
            return 0
        msp.add_line(
            (float(s.x), float(s.y)),
            (float(en.x), float(en.y)),
            dxfattribs={"layer": layer},
        )
        return 1
    if typ == "ARC":
        c = entity.dxf.center
        msp.add_arc(
            center=(float(c.x), float(c.y)),
            radius=float(entity.dxf.radius),
            start_angle=float(entity.dxf.start_angle),
            end_angle=float(entity.dxf.end_angle),
            dxfattribs={"layer": layer},
        )
        return 1
    if typ == "CIRCLE":
        c = entity.dxf.center
        msp.add_circle(
            (float(c.x), float(c.y)),
            float(entity.dxf.radius),
            dxfattribs={"layer": layer},
        )
        return 1
    if typ == "TEXT":
        ins = entity.dxf.insert
        msp.add_text(
            str(entity.dxf.text or ""),
            dxfattribs={
                "layer": layer,
                "height": float(getattr(entity.dxf, "height", 1.0) or 1.0),
                "rotation": float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
            },
        ).set_placement((float(ins.x), float(ins.y)))
        return 1
    if typ == "MTEXT":
        ins = entity.dxf.insert
        attribs = {
            "layer": layer,
            "char_height": float(getattr(entity.dxf, "char_height", 1.0) or 1.0),
            "rotation": float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
        }
        mt = msp.add_mtext(str(entity.plain_text() or ""), dxfattribs=attribs)
        mt.set_location((float(ins.x), float(ins.y)))
        return 1
    if typ in ("LWPOLYLINE", "POLYLINE"):
        added = 0
        for sub in entity.virtual_entities():
            added += _write_native_entity(msp, sub, layer)
        return added
    try:
        from ezdxf import path as ezdxf_path

        path_obj = ezdxf_path.make_path(entity)
        verts = list(path_obj.flattening(distance=max(0.05, 1e-4)))
        added = 0
        for i in range(len(verts) - 1):
            a, b = verts[i], verts[i + 1]
            if math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1])) < 1e-6:
                continue
            msp.add_line(
                (float(a[0]), float(a[1])),
                (float(b[0]), float(b[1])),
                dxfattribs={"layer": layer},
            )
            added += 1
        return added
    except Exception:
        return 0


def _build_placement_matrix(p, sheet: dict | None = None) -> Matrix44:
    """Misma cadena que el visor/nesting: pulgadas→mm, normalizar, rotar en centroide, colocar en placa."""
    p = _resolve_placement(p)
    ox, oy = _placement_origin_mm(p)
    rot = math.radians(float(p.get("rot_deg", 0.0) or 0.0))
    sx = float(p.get("shift_x", 0.0) or 0.0)
    sy = float(p.get("shift_y", 0.0) or 0.0)
    if abs(sx) < 1e-6 and abs(sy) < 1e-6:
        bounds = _poly_bounds(p.get("outer") or p.get("outer_poly"))
        if bounds:
            sx, sy = bounds[0], bounds[1]
    rcx = float(p.get("rot_origin_cx", 0.0) or 0.0)
    rcy = float(p.get("rot_origin_cy", 0.0) or 0.0)

    m = Matrix44.scale(ESCALA_DXF, ESCALA_DXF, ESCALA_DXF)
    m @= Matrix44.translate(-ox, -oy, 0)
    if abs(rot) > 1e-12:
        m @= Matrix44.translate(rcx, rcy, 0)
        m @= Matrix44.z_rotate(rot)
        m @= Matrix44.translate(-rcx, -rcy, 0)
    m @= Matrix44.translate(sx, sy, 0)
    return _compose_sin_gap_vertical(m, sheet)


def _circle_signature(ent, *, decimals: int = 2) -> tuple[float, float, float] | None:
    if ent.dxftype() != "CIRCLE":
        return None
    c = ent.dxf.center
    return (
        round(float(c.x), decimals),
        round(float(c.y), decimals),
        round(float(ent.dxf.radius), decimals),
    )


def _inner_polyline_redundant_with_circle(ent, circle_sigs: set[tuple]) -> bool:
    """DXF fuente a veces duplica barreno como CIRCLE + LWPOLYLINE facetada."""
    if ent.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
        return False
    if not circle_sigs:
        return False
    try:
        from ezdxf import path as ezdxf_path

        p = ezdxf_path.make_path(ent)
        if not p.is_closed:
            return False
        verts = list(p.flattening(distance=0.05))
        if len(verts) < 8:
            return False
        xs = [float(v[0]) for v in verts]
        ys = [float(v[1]) for v in verts]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        r = sum(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys)) / len(xs)
        if r < 0.2:
            return False
        sig = (round(cx, 2), round(cy, 2), round(r, 2))
        for cs in circle_sigs:
            if (
                abs(sig[0] - cs[0]) <= 0.35
                and abs(sig[1] - cs[1]) <= 0.35
                and abs(sig[2] - cs[2]) <= 0.35
            ):
                return True
    except Exception:
        pass
    return False


def _import_layers_from_source(source_doc, target_doc, layer_names: set[str]) -> None:
    """Registra en el DXF destino las capas del fuente (nombre, color, linetype)."""
    tgt = target_doc.layers
    for name in layer_names:
        if not name or name in tgt:
            continue
        try:
            src = source_doc.layers.get(name)
            attrs: dict = {}
            if src is not None:
                attrs["color"] = int(src.dxf.color)
                lt = str(src.dxf.linetype or "").strip()
                if lt and lt.upper() != "BYLAYER":
                    attrs["linetype"] = lt
            tgt.new(name, dxfattribs=attrs)
        except Exception:
            try:
                tgt.new(name)
            except Exception:
                pass


def _is_inner_cut_entity(ent) -> bool:
    return _clasificar_capa(str(ent.dxf.layer)) == "inner"


def _dedupe_staged_inner_circles(staged: list) -> list:
    circle_sigs = {
        sig
        for ent in staged
        if _is_inner_cut_entity(ent)
        for sig in [_circle_signature(ent)]
        if sig is not None
    }
    if not circle_sigs:
        return staged
    out = []
    for ent in staged:
        if _is_inner_cut_entity(ent) and _inner_polyline_redundant_with_circle(
            ent, circle_sigs
        ):
            continue
        out.append(ent)
    return out


def _edge_on_bar_exterior(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    bar_len: float,
    bar_w: float,
    tol: float = TOL_GEOM_MM,
    piece_bounds=None,
    idx: int = 0,
    n_total: int = 1,
    bar_vertical: bool = False,
) -> bool:
    """True si la arista es cara exterior del stock (no se corta con láser)."""
    from modules.nesting_engine.cu_largos_nesting import (
        _es_arista_horizontal,
        _es_arista_vertical,
    )

    if math.hypot(x2 - x1, y2 - y1) <= tol:
        return True

    if bar_vertical:
        # sin_gap: largo en Y, ancho en X; inicio de barra en y=0.
        if _es_arista_horizontal(x1, y1, x2, y2, tol) and y1 <= tol and y2 <= tol:
            return True
        if _es_arista_vertical(x1, y1, x2, y2, tol) and x1 <= tol and x2 <= tol:
            return True
        if (
            _es_arista_vertical(x1, y1, x2, y2, tol)
            and bar_w > tol
            and x1 >= bar_w - tol
            and x2 >= bar_w - tol
        ):
            return True
        if (
            _es_arista_horizontal(x1, y1, x2, y2, tol)
            and bar_len > tol
            and abs((y1 + y2) / 2.0 - bar_len) <= tol
        ):
            return True
        if piece_bounds and _es_arista_horizontal(x1, y1, x2, y2, tol):
            minx, miny, maxx, maxy = piece_bounds
            ancho = maxx - minx
            if ancho > tol and bar_w > tol:
                span = abs(float(x2) - float(x1))
                if span >= min(ancho, bar_w) - max(tol, min(ancho, bar_w) * 0.05):
                    y_mid = (y1 + y2) / 2.0
                    if idx > 0 and abs(y_mid - miny) <= tol:
                        return True
                    if abs(y_mid - maxy) <= tol:
                        return True
        return False

    # Cara izquierda del stock (inicio de barra → marcador CUT_CU, no láser)
    if _es_arista_vertical(x1, y1, x2, y2, tol) and x1 <= tol and x2 <= tol:
        return True

    # Guillotina vertical entre rebanadas (no el corte final de la última pieza).
    if piece_bounds and _es_arista_vertical(x1, y1, x2, y2, tol):
        minx, miny, maxx, maxy = piece_bounds
        alto = maxy - miny
        if alto > tol:
            span = abs(float(y2) - float(y1))
            if span >= alto - max(tol, alto * 0.05):
                x_mid = (x1 + x2) / 2.0
                if idx > 0 and abs(x_mid - minx) <= tol:
                    return True
                # Derecha: entre piezas o cierre final (este último va por CU_CORTE__V__N)
                if abs(x_mid - maxx) <= tol:
                    return True

    # Fondo y techo de la barra maestra: nunca láser (coinciden con esquinas del largo)
    if _es_arista_horizontal(x1, y1, x2, y2, tol):
        ymid = (y1 + y2) / 2.0
        if ymid <= tol:
            return True
        if bar_w > tol and abs(ymid - bar_w) <= tol:
            return True

    return False


def _should_export_cu_laser_edge(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    outer_work: list,
    bar_len: float,
    bar_w: float,
    tol: float = TOL_GEOM_MM,
    piece_bounds=None,
    idx: int = 0,
    n_total: int = 1,
    bar_vertical: bool = False,
) -> bool:
    """
    True si la arista del contorno fuente debe ir al DXF sin_gap vertical.

    Exporta el CUT_OUTER nativo 1:1 (integridad del DXF original) salvo caras del
    stock (fondo/techo/laterales de barra y guillotinas verticales completas).
    """
    from modules.nesting_engine.cu_largos_nesting import _solo_cortes_guillotina_vertical

    if not outer_work or _solo_cortes_guillotina_vertical(outer_work, tol=tol):
        return False
    return _edge_is_bar_interior_laser_cut(
        x1,
        y1,
        x2,
        y2,
        bar_len=bar_len,
        bar_w=bar_w,
        tol=tol,
        piece_bounds=piece_bounds,
        idx=idx,
        n_total=n_total,
        bar_vertical=bar_vertical,
    )


def _edge_is_bar_interior_laser_cut(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    bar_len: float,
    bar_w: float,
    tol: float = TOL_GEOM_MM,
    piece_bounds=None,
    idx: int = 0,
    n_total: int = 1,
    bar_vertical: bool = False,
) -> bool:
    """
    Corte láser si la arista queda dentro de la barra y no es cara exterior del stock.
    Sin horizontales en fondo/techo ni guillotinas verticales completas entre piezas.
    """
    if math.hypot(x2 - x1, y2 - y1) <= tol:
        return False
    if _edge_on_bar_exterior(
        x1,
        y1,
        x2,
        y2,
        bar_len=bar_len,
        bar_w=bar_w,
        tol=tol,
        piece_bounds=piece_bounds,
        idx=idx,
        n_total=n_total,
        bar_vertical=bar_vertical,
    ):
        return False
    mx = (x1 + x2) / 2.0
    my = (y1 + y2) / 2.0
    if mx < -tol or my < -tol:
        return False
    if bar_vertical:
        if bar_w > tol and mx > bar_w + tol:
            return False
        if bar_len > tol and my > bar_len + tol:
            return False
        return True
    if bar_len > tol and mx > bar_len + tol:
        return False
    if bar_w > tol and my > bar_w + tol:
        return False
    return True


def _arc_sample_points(arc, n: int = 5) -> list[tuple[float, float]]:
    c = arc.dxf.center
    r = float(arc.dxf.radius)
    sa = math.radians(float(arc.dxf.start_angle))
    ea = math.radians(float(arc.dxf.end_angle))
    if ea < sa:
        ea += 2.0 * math.pi
    pts = []
    for i in range(max(2, n)):
        t = sa + (ea - sa) * (i / max(n - 1, 1))
        pts.append((float(c.x) + r * math.cos(t), float(c.y) + r * math.sin(t)))
    return pts


def _iter_outer_edge_segments_mm(part_doc, m, *, flat_tol_mm: float = 0.02):
    """(p1, p2, entidad_nativa|None) del contorno exterior en mm."""
    from ezdxf import path as ezdxf_path

    for entity in part_doc.modelspace():
        if _clasificar_capa(str(entity.dxf.layer)) != "outer":
            continue
        try:
            e = entity.copy()
            if not e.transform(m):
                continue
            typ = e.dxftype()
            if typ == "LINE":
                s, en = e.dxf.start, e.dxf.end
                yield (
                    (float(s.x), float(s.y)),
                    (float(en.x), float(en.y)),
                    e,
                )
            elif typ == "ARC":
                yield (None, None, e)
            elif typ in ("LWPOLYLINE", "POLYLINE"):
                for sub in e.virtual_entities():
                    st = sub.dxftype()
                    if st == "LINE":
                        s, en = sub.dxf.start, sub.dxf.end
                        yield (
                            (float(s.x), float(s.y)),
                            (float(en.x), float(en.y)),
                            sub,
                        )
                    elif st == "ARC":
                        yield (None, None, sub)
            else:
                p = ezdxf_path.make_path(e)
                verts = list(p.flattening(distance=max(flat_tol_mm, 1e-4)))
                for i in range(len(verts) - 1):
                    a, b = verts[i], verts[i + 1]
                    yield (
                        (float(a[0]), float(a[1])),
                        (float(b[0]), float(b[1])),
                        None,
                    )
        except Exception:
            continue


def _emit_cut_outer_segment(
    msp,
    p1: tuple,
    p2: tuple,
    native,
    *,
    seen_lines: set,
    seen_arcs: set,
) -> bool:
    if native is not None and native.dxftype() == "ARC":
        c = native.dxf.center
        key = (
            round(float(c.x), 3),
            round(float(c.y), 3),
            round(float(native.dxf.radius), 4),
            round(float(native.dxf.start_angle), 3),
            round(float(native.dxf.end_angle), 3),
        )
        if key in seen_arcs:
            return False
        seen_arcs.add(key)
        msp.add_arc(
            center=(float(c.x), float(c.y)),
            radius=float(native.dxf.radius),
            start_angle=float(native.dxf.start_angle),
            end_angle=float(native.dxf.end_angle),
            dxfattribs={"layer": "CUT_OUTER"},
        )
        return True
    k1 = (round(float(p1[0]), 4), round(float(p1[1]), 4))
    k2 = (round(float(p2[0]), 4), round(float(p2[1]), 4))
    if (k1, k2) in seen_lines or (k2, k1) in seen_lines:
        return False
    seen_lines.add((k1, k2))
    if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1e-6:
        return False
    msp.add_line(p1, p2, dxfattribs={"layer": "CUT_OUTER"})
    return True


def _export_cu_contour_cuts_from_ring(
    msp,
    ring,
    p: dict,
    sheet: dict | None = None,
    *,
    coords_vertical: bool = False,
) -> int:
    """Respaldo: aristas del contorno colocado que son corte interior a la barra."""
    idx = int(p.get("cu_slice_idx", 0) or 0)
    n_total = max(1, int(p.get("cu_slice_count", 1) or 1))
    if not coords_vertical:
        ring = _ring_for_sin_gap_export(ring, sheet, p)
    pts = normalize_ring(ring, closed=True)
    if len(pts) < 2:
        return 0
    outer_work = list(pts)
    piece_bounds = _poly_bounds(outer_work)
    bar_w = float(p.get("cu_bar_w_mm") or 0.0)
    bar_l = float(p.get("cu_bar_l_mm") or 0.0)
    if bar_w <= 0 and piece_bounds:
        bar_w = piece_bounds[3] - piece_bounds[1]
    if bar_l <= 0 and piece_bounds:
        bar_l = piece_bounds[2] - piece_bounds[0]
    bar_vertical = _sheet_export_sin_gap_vertical(sheet) or coords_vertical
    seen_lines: set = set()
    seen_arcs: set = set()
    added = 0
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        p1, p2 = pts[i], pts[j]
        if not _should_export_cu_laser_edge(
            p1[0],
            p1[1],
            p2[0],
            p2[1],
            outer_work=outer_work,
            bar_len=bar_l,
            bar_w=bar_w,
            tol=TOL_GEOM_MM,
            piece_bounds=piece_bounds,
            idx=idx,
            n_total=n_total,
            bar_vertical=bar_vertical,
        ):
            continue
        if _emit_cut_outer_segment(msp, p1, p2, None, seen_lines=seen_lines, seen_arcs=seen_arcs):
            added += 1
    return added


def _export_cu_laser_outer_for_piece(
    msp,
    part_doc,
    m,
    p: dict,
    outer: list,
    sheet: dict | None = None,
) -> int:
    """
    CUT_OUTER cobre largos: DXF nativo 1:1; respaldo contorno nest.
    Retorna 0 si la pieza solo requiere guillotina vertical (CU_CORTE__).
    """
    from modules.nesting_engine.cu_largos_nesting import (
        _segmentos_corte_laser_pieza,
        _solo_cortes_guillotina_vertical,
    )

    laser_added = _export_cu_laser_outer_cuts_native(msp, part_doc, m, p, sheet=sheet)
    if laser_added <= 0:
        laser_added = _export_cu_contour_cuts_from_ring(msp, outer, p, sheet=sheet)

    if laser_added > 0 or _solo_cortes_guillotina_vertical(outer):
        return laser_added

    idx = int(p.get("cu_slice_idx", 0) or 0)
    n_total = max(1, int(p.get("cu_slice_count", 1) or 1))
    segs = _segmentos_corte_laser_pieza(outer, idx=idx, n_total=n_total)
    if not segs:
        return 0

    seen_lines: set = set()
    seen_arcs: set = set()
    for seg in segs:
        if len(seg) < 2:
            continue
        if _emit_cut_outer_segment(
            msp,
            seg[0],
            seg[1],
            None,
            seen_lines=seen_lines,
            seen_arcs=seen_arcs,
        ):
            laser_added += 1
    return laser_added


def _export_cu_laser_outer_cuts_native(
    msp, part_doc, m, p: dict, sheet: dict | None = None
) -> int:
    """
    Aristas del contorno DXF dentro de la barra → CUT_OUTER (LINE/ARC nativos, 1:1).
    """
    bar_w = float(p.get("cu_bar_w_mm") or 0.0)
    bar_l = float(p.get("cu_bar_l_mm") or 0.0)
    idx = int(p.get("cu_slice_idx", 0) or 0)
    n_total = max(1, int(p.get("cu_slice_count", 1) or 1))
    outer = p.get("outer") or p.get("outer_poly") or []
    outer_work = _ring_for_sin_gap_export(outer, sheet, p)
    piece_bounds = _poly_bounds(outer_work)
    if not piece_bounds and (bar_w <= 0 or bar_l <= 0):
        return 0
    if bar_w <= 0 and piece_bounds:
        bar_w = piece_bounds[3] - piece_bounds[1]
    if bar_l <= 0 and piece_bounds:
        bar_l = piece_bounds[2] - piece_bounds[0]

    bar_vertical = _sheet_export_sin_gap_vertical(sheet)
    seen_lines: set = set()
    seen_arcs: set = set()
    added = 0

    for p1, p2, native in _iter_outer_edge_segments_mm(part_doc, m):
        if native is not None and native.dxftype() == "ARC":
            pts = _arc_sample_points(native, n=7)
            if len(pts) < 2:
                continue
            keep_arc = False
            for i in range(len(pts) - 1):
                if _should_export_cu_laser_edge(
                    pts[i][0],
                    pts[i][1],
                    pts[i + 1][0],
                    pts[i + 1][1],
                    outer_work=outer_work,
                    bar_len=bar_l,
                    bar_w=bar_w,
                    tol=TOL_GEOM_MM,
                    piece_bounds=piece_bounds,
                    idx=idx,
                    n_total=n_total,
                    bar_vertical=bar_vertical,
                ):
                    keep_arc = True
                    break
            if keep_arc and _emit_cut_outer_segment(
                msp,
                pts[0],
                pts[-1],
                native,
                seen_lines=seen_lines,
                seen_arcs=seen_arcs,
            ):
                added += 1
            continue
        if p1 is None or p2 is None:
            continue
        if not _should_export_cu_laser_edge(
            p1[0],
            p1[1],
            p2[0],
            p2[1],
            outer_work=outer_work,
            bar_len=bar_l,
            bar_w=bar_w,
            tol=TOL_GEOM_MM,
            piece_bounds=piece_bounds,
            idx=idx,
            n_total=n_total,
            bar_vertical=bar_vertical,
        ):
            continue
        if _emit_cut_outer_segment(
            msp, p1, p2, native, seen_lines=seen_lines, seen_arcs=seen_arcs
        ):
            added += 1

    return added


def _export_cu_full_contour_cut_cu_from_ring(msp, doc, ring: list) -> int:
    """
    Legacy CUT_CU (ya no usado en export nest cobre; conservado por compatibilidad).
    """
    del doc
    pts = normalize_ring(ring, closed=True)
    if len(pts) < 3:
        return 0
    _add_lwpolyline(msp, pts, "CUT_CU", closed=True)
    return 1


def _export_cu_full_contour_cut_outer_from_ring(msp, ring: list) -> int:
    """con_gap / STEP: 1 LWPOLYLINE cerrada CUT_OUTER por pieza (FreeCAD / auditoría)."""
    pts = normalize_ring(ring, closed=True)
    if len(pts) < 3:
        return 0
    _add_lwpolyline(msp, pts, "CUT_OUTER", closed=True)
    return 1


def _export_cu_outer_completo_desde_fuente(
    msp,
    doc,
    part_doc,
    m,
    p: dict,
    outer: list,
    sheet: dict | None = None,
) -> int:
    """con_gap / STEP: contorno cerrado en CUT_OUTER desde el nest (no LINE/ARC sueltos)."""
    del doc, part_doc, m  # inner/marks siguen en el canal fuente
    outer_work = _ring_for_sin_gap_export(outer, sheet, p)
    return _export_cu_full_contour_cut_outer_from_ring(msp, outer_work)


def _export_cu_full_contour_cut_cu(
    msp,
    doc,
    part_doc,
    m,
    p: dict,
    outer: list,
    sheet: dict | None = None,
) -> int:
    """Legacy CUT_CU — no usar en export nest cobre actual."""
    outer_work = _ring_for_sin_gap_export(outer, sheet, p)
    if not outer_work:
        label = _piece_label(p)
        raise DxfExportValidationError(
            f"{label}: sin contorno outer colocado para CUT_CU (STEP requiere 1 isla/pieza)."
        )
    return _export_cu_full_contour_cut_cu_from_ring(msp, doc, outer_work)


def _layer_for_exported_entity(clase: str, entity, p: dict) -> str:
    """Capa destino según modo de exportación."""
    raw = str(entity.dxf.layer or "").strip()
    if bool(p.get("cu_largos_piece")):
        if clase == "inner":
            return "CUT_INNER"
        if clase == "mark":
            return "MARK"
        return raw or "CUT_OUTER"
    return raw


def _export_cu_inner_and_marks_from_source(
    msp,
    doc,
    part_doc,
    m,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
) -> bool:
    """Inner/marks cobre largos clonando entidades nativas del DXF fuente (arcos/círculos 1:1)."""
    added = 0
    layers_used: set[str] = set()
    for entity in part_doc.modelspace():
        if entity.dxftype() not in (
            "LINE",
            "LWPOLYLINE",
            "POLYLINE",
            "ARC",
            "CIRCLE",
            "ELLIPSE",
            "SPLINE",
        ):
            continue
        clase = _clasificar_capa(str(entity.dxf.layer))
        if clase == "inner" and not draw_holes:
            continue
        if clase == "mark" and not draw_marks:
            continue
        if clase not in ("inner", "mark"):
            continue
        try:
            new_e = entity.copy()
            if not new_e.transform(m):
                continue
            layer = "CUT_INNER" if clase == "inner" else "MARK"
            added += _write_native_entity(msp, new_e, layer)
            layers_used.add(layer)
        except Exception:
            continue

    if added <= 0:
        return False
    _import_layers_from_source(part_doc, doc, layers_used)
    return True


def _export_cu_inner_and_marks(
    msp,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
    sheet: dict | None = None,
) -> bool:
    """Inner/marks cobre largos desde polígonos del nest (mm placa, 1:1 con visor)."""
    added = False
    bw = _bar_width_mm(sheet, float(p.get("cu_bar_w_mm") or 0.0))
    if draw_holes:
        for h in p.get("holes") or p.get("inner") or []:
            h_t = _transform_poly(h, tx=0.0, ty=0.0, rot_deg=0.0)
            if _sheet_export_sin_gap_vertical(sheet):
                h_t = _transform_poly_sin_gap_vertical(h_t, bw)
            if not h_t:
                continue
            export_ring_native(msp, h_t, "CUT_INNER", closed=True, prefer_circle=True)
            added = True
    if draw_marks:
        for mk in p.get("marks") or p.get("mark") or []:
            if not mk:
                continue
            mk_t = _transform_poly(mk, tx=0.0, ty=0.0, rot_deg=0.0)
            if _sheet_export_sin_gap_vertical(sheet):
                mk_t = _transform_poly_sin_gap_vertical(mk_t, bw)
            if mk_t and _add_mark_open_segments(msp, mk_t, layer="MARK"):
                added = True
    return added


def _export_cu_sin_gap_outer_from_source(
    msp,
    doc,
    part_doc,
    m,
    p: dict,
    outer: list,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
    sheet: dict | None = None,
) -> bool:
    """
    sin_gap CyPTube: contorno nativo del DXF fuente sin caras del stock;
    inner/marks 1:1. Las guillotinas CU_CORTE__V__ van por placements aparte.
    """
    from modules.nesting_engine.cu_largos_nesting import _solo_cortes_guillotina_vertical

    label = _piece_label(p)
    outer_added = _export_cu_laser_outer_cuts_native(
        msp, part_doc, m, p, sheet=sheet
    )
    if outer_added <= 0 and not _solo_cortes_guillotina_vertical(outer):
        return False

    inner_ok = _export_cu_inner_and_marks_from_source(
        msp, doc, part_doc, m, p, draw_holes=draw_holes, draw_marks=draw_marks
    )
    holes = p.get("holes") or p.get("inner") or []
    marks = p.get("marks") or p.get("mark") or []
    if not inner_ok and ((holes and draw_holes) or (marks and draw_marks)):
        inner_ok = _export_cu_inner_and_marks(
            msp,
            p,
            draw_holes=draw_holes,
            draw_marks=draw_marks,
            sheet=sheet,
        )
    if not inner_ok and holes and draw_holes:
        _fail_export(
            label,
            "barrenos/interiores no exportables 1:1 desde el DXF fuente",
        )
    return outer_added > 0 or _solo_cortes_guillotina_vertical(outer)


def _export_cu_largos_from_source(
    msp,
    doc,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
    strict: bool = True,
    sheet: dict | None = None,
) -> None:
    """Cobre largos: DXF fuente 1:1 (sin_gap) o CUT_OUTER cerrado (con_gap/STEP)."""
    label = _piece_label(p)
    ruta = str(p.get("ruta") or "").strip()
    outer = p.get("outer") or p.get("outer_poly") or []
    if not ruta or not os.path.isfile(ruta):
        _fail_export(label, f"sin DXF fuente válido ({ruta or 'sin ruta'})")
    if len(outer) < 2:
        _fail_export(label, "sin contorno colocado en el nest")

    try:
        part_doc = ezdxf.readfile(ruta)
    except Exception as e:
        _fail_export(label, f"DXF fuente ilegible: {e}")

    m = _resolve_placement_matrix(part_doc, _resolve_placement(p), sheet=sheet)

    if not _sheet_cu_exporta_cortes_segmentados(sheet):
        outer_added = _export_cu_outer_completo_desde_fuente(
            msp, doc, part_doc, m, p, outer, sheet=sheet
        )
        if outer_added <= 0:
            _fail_export(
                label,
                "sin contorno CUT_OUTER cerrado exportable para STEP (revise DXF fuente)",
            )
        inner_ok = _export_cu_inner_and_marks_from_source(
            msp, doc, part_doc, m, p, draw_holes=draw_holes, draw_marks=draw_marks
        )
        holes = p.get("holes") or p.get("inner") or []
        marks = p.get("marks") or p.get("mark") or []
        if not inner_ok and ((holes and draw_holes) or (marks and draw_marks)):
            inner_ok = _export_cu_inner_and_marks(
                msp,
                p,
                draw_holes=draw_holes,
                draw_marks=draw_marks,
                sheet=sheet,
            )
        if not inner_ok and holes and draw_holes:
            _fail_export(
                label,
                "barrenos/interiores no exportables 1:1 desde el DXF fuente",
            )
        return

    if not _export_cu_sin_gap_outer_from_source(
        msp,
        doc,
        part_doc,
        m,
        p,
        outer,
        draw_holes=draw_holes,
        draw_marks=draw_marks,
        sheet=sheet,
    ):
        _fail_export(
            label,
            "sin cortes CUT_OUTER exportables desde el DXF fuente (revise perfil y capas)",
        )
    return


def _export_source_dxf_at_placement(
    msp,
    doc,
    p: dict,
    *,
    draw_marks: bool = True,
    bounds_tol: float = 3.0,
    strict: bool = True,
) -> None:
    """Clona entidades nativas del DXF fuente en la posición validada del nest."""
    label = _piece_label(p)
    ruta = str(p.get("ruta") or "").strip()
    outer = p.get("outer") or p.get("outer_poly") or []
    if not ruta or not os.path.isfile(ruta):
        _fail_export(label, f"sin DXF fuente válido ({ruta or 'sin ruta'})")
    if len(outer) < 2:
        _fail_export(label, "sin contorno colocado en el nest")

    try:
        part_doc = ezdxf.readfile(ruta)
    except Exception as e:
        _fail_export(label, f"DXF fuente ilegible: {e}")

    m = _resolve_placement_matrix(part_doc, _resolve_placement(p))
    added = 0
    layers_used: set[str] = set()

    for entity in part_doc.modelspace():
        if entity.dxftype() not in (
            "LINE",
            "LWPOLYLINE",
            "POLYLINE",
            "ARC",
            "CIRCLE",
            "ELLIPSE",
            "SPLINE",
        ):
            continue
        clase = _clasificar_capa(str(entity.dxf.layer))
        if clase is None:
            continue
        if clase == "mark" and not draw_marks:
            continue
        if clase == "outer" and p.get("cu_largos_piece"):
            continue
        try:
            new_e = entity.copy()
            if not new_e.transform(m):
                continue
            layer = _layer_for_exported_entity(clase, new_e, p)
            added += _write_native_entity(msp, new_e, layer)
            layers_used.add(layer)
        except Exception:
            continue

    if added <= 0 and not p.get("cu_largos_piece"):
        _fail_export(label, "el DXF fuente no tiene geometría exportable en capas de corte")

    if layers_used:
        _import_layers_from_source(part_doc, doc, layers_used)

    if p.get("cu_largos_piece"):
        outer = p.get("outer") or p.get("outer_poly") or []
        laser_added = _export_cu_laser_outer_for_piece(msp, part_doc, m, p, outer)
        if laser_added <= 0 and outer:
            from modules.nesting_engine.cu_largos_nesting import (
                _segmentos_corte_laser_pieza,
                _solo_cortes_guillotina_vertical,
            )

            if not _solo_cortes_guillotina_vertical(outer):
                idx = int(p.get("cu_slice_idx", 0) or 0)
                n_total = max(1, int(p.get("cu_slice_count", 1) or 1))
                if _segmentos_corte_laser_pieza(outer, idx=idx, n_total=n_total):
                    _fail_export(label, "sin cortes láser CUT_OUTER desde DXF fuente")


def _export_ring_exact(msp, points, layer: str, *, closed: bool = True) -> bool:
    """
    Exporta anillo como LINE entre vértices del nest — sin inferir círculos/arcos.
    Posición 1:1 con el visor; respaldo cuando no hay DXF fuente válido.
    """
    pts = normalize_ring(points, closed=closed)
    if len(pts) < 2:
        return False
    if closed:
        for i in range(len(pts)):
            j = (i + 1) % len(pts)
            x1, y1 = pts[i]
            x2, y2 = pts[j]
            if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                continue
            msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})
    else:
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                continue
            msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})
    return True


def _add_lwpolyline(msp, points, layer, closed=True):
    """
    Versión Purificada: 
    Delega el cierre del polígono puramente a la etiqueta 'closed'.
    """
    # 🚀 BLINDAJE: AutoCAD requiere al menos 2 puntos para una polilínea
    if not points or len(points) < 2: 
        return
    msp.add_lwpolyline(points, dxfattribs={"layer": layer, "closed": bool(closed)})


def _add_mark_open_segments(msp, points, layer: str = "MARK") -> int:
    """Marcaje stick: segmentos LINE abiertos (FreeCAD Edges / Part.makeLine)."""
    if not points or len(points) < 2:
        return 0
    added = 0
    for i in range(len(points) - 1):
        try:
            x1, y1 = float(points[i][0]), float(points[i][1])
            x2, y2 = float(points[i + 1][0]), float(points[i + 1][1])
        except (TypeError, ValueError, IndexError):
            continue
        if abs(x2 - x1) < 1e-12 and abs(y2 - y1) < 1e-12:
            continue
        msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})
        added += 1
    return added


def _export_placed_geometry(
    msp, p, *, doc=None, draw_holes=True, draw_marks=True, sheet=None
) -> bool:
    """
    Exporta contorno/marcas en coordenadas de placa (mm), 1:1 con el visor de nesting.
    Fuente de verdad: poligonos/marcas ya colocados por el motor.
    """
    outer = p.get("outer") or p.get("outer_poly")
    has_outer = bool(outer and len(outer) >= 2)
    marks = p.get("marks") or p.get("mark") or []
    if not has_outer and not marks:
        return False

    if has_outer:
        outer_t = _transform_poly(outer, tx=0.0, ty=0.0, rot_deg=0.0)
        if _sheet_export_sin_gap_vertical(sheet):
            outer_t = _transform_poly_sin_gap_vertical(
                outer_t, _bar_width_mm(sheet)
            )
        if outer_t and not p.get("cu_largos_holes_only"):
            if p.get("cu_largos_piece"):
                if _sheet_cu_exporta_cortes_segmentados(sheet):
                    _export_cu_contour_cuts_from_ring(
                        msp,
                        outer_t,
                        p,
                        sheet=sheet,
                        coords_vertical=_sheet_export_sin_gap_vertical(sheet),
                    )
                else:
                    _export_cu_full_contour_cut_outer_from_ring(msp, outer_t)
            else:
                layer_destino = str(p.get("layer_override") or "CUT_OUTER")
                closed_destino = bool(p.get("closed", True))
                if p.get("use_native_curves"):
                    if not export_ring_native(
                        msp, outer_t, layer_destino, closed=closed_destino
                    ):
                        _export_ring_exact(
                            msp, outer_t, layer_destino, closed=closed_destino
                        )
                else:
                    _export_ring_exact(msp, outer_t, layer_destino, closed=closed_destino)

    if p.get("cu_largos_piece"):
        _export_cu_inner_and_marks(
            msp, p, draw_holes=draw_holes, draw_marks=draw_marks, sheet=sheet
        )
    else:
        if draw_holes and has_outer:
            holes = p.get("holes") or p.get("inner") or []
            bw = _bar_width_mm(sheet, float(p.get("cu_bar_w_mm") or 0.0))
            for h in holes:
                h_t = _transform_poly(h, tx=0.0, ty=0.0, rot_deg=0.0)
                if _sheet_export_sin_gap_vertical(sheet):
                    h_t = _transform_poly_sin_gap_vertical(h_t, bw)
                if not h_t:
                    continue
                hole_layer = str(p.get("inner_layer_override") or "CUT_INNER")
                if p.get("use_native_curves"):
                    if not export_ring_native(
                        msp, h_t, hole_layer, closed=True, prefer_circle=True
                    ):
                        _export_ring_exact(msp, h_t, hole_layer, closed=True)
                else:
                    _export_ring_exact(msp, h_t, hole_layer, closed=True)

        if draw_marks:
            part_name = str(p.get("part_name") or p.get("name") or "")
            marks_layer = str(
                p.get("marks_layer")
                or p.get("marks_layer_override")
                or ("RTZ_LABEL" if part_name.startswith("TATUAJE") else "MARK")
            )
            bw = _bar_width_mm(sheet, float(p.get("cu_bar_w_mm") or 0.0))
            for mk in marks:
                if not mk:
                    continue
                mk_t = _transform_poly(mk, tx=0.0, ty=0.0, rot_deg=0.0)
                if _sheet_export_sin_gap_vertical(sheet):
                    mk_t = _transform_poly_sin_gap_vertical(mk_t, bw)
                if mk_t:
                    _add_mark_open_segments(msp, mk_t, layer=marks_layer)

    return has_outer or bool(marks)


def _export_block_at_placement(msp, doc, cache_blocks: dict, p: dict) -> bool:
    """Clona el modelspace del DXF fuente con transformación (geometría nativa exacta)."""
    ruta_original = str(p.get("ruta") or "").strip()
    if not ruta_original or not os.path.isfile(ruta_original):
        return False

    if ruta_original not in cache_blocks:
        safe_block_name = f"BLK_{uuid.uuid4().hex[:8]}"
        try:
            part_doc = ezdxf.readfile(ruta_original)
            _import_layers_from_source(
                part_doc, doc, {str(n) for n in part_doc.layers if str(n)}
            )
            blk = doc.blocks.new(name=safe_block_name)
            importer = Importer(part_doc, doc)
            importer.import_modelspace(blk)
            importer.finalize()
            cache_blocks[ruta_original] = safe_block_name
        except Exception as e:
            print(f"[ERROR] No se pudo leer el DXF base {ruta_original}: {e}")
            return False

    safe_block_name = cache_blocks.get(ruta_original)
    if not safe_block_name or safe_block_name not in doc.blocks:
        return False

    try:
        m = _build_placement_matrix(_resolve_placement(p))
        blockref = msp.add_blockref(safe_block_name, insert=(0, 0))
        blockref.transform(m)
        blockref.explode()
        return True
    except Exception as e:
        part_name = str(p.get("part_name", p.get("name", "?")))
        print(f"[ERROR] Transformación block falló para {part_name}: {e}")
        return False

def _posicion_x_cu_corte_v(p: dict) -> float | None:
    """Coordenada X (nest mm) de una guillotina CU_CORTE__V__."""
    outer = p.get("outer") or p.get("outer_poly") or []
    if len(outer) < 2:
        return None
    try:
        xs = [float(pt[0]) for pt in outer]
    except Exception:
        return None
    if not xs:
        return None
    if max(xs) - min(xs) > TOL_GEOM_MM:
        return None
    return sum(xs) / len(xs)


def _omitir_cu_corte_v_fuera_madre_sin_gap(
    p: dict,
    sheet: dict | None,
    placements: list | None = None,
) -> bool:
    """
    Madre sin_gap: omitir guillotinas después del fin de la última pieza real
    (inicio RTZCU / gap / cola RTZ). Solo debe quedar el corte en cu_fin_piezas_mm.
    """
    nom = str(p.get("part_name") or p.get("name") or "")
    if not nom.startswith("CU_CORTE__V__"):
        return False
    if not isinstance(sheet, dict) or not _sheet_is_sin_gap(sheet):
        return False
    if sheet.get("cu_rtz_virtual") or sheet.get("cu_export_amada"):
        return False
    fin_madre = _fin_x_ultima_pieza_cu(sheet, placements)
    if fin_madre <= TOL_GEOM_MM:
        return False
    x_corte = _posicion_x_cu_corte_v(p)
    if x_corte is None:
        return False
    return float(x_corte) > float(fin_madre) + max(0.5, TOL_GEOM_MM)


def _max_cu_corte_v_index(placements: list) -> int:
    best = 0
    for p in placements or []:
        nom = str(p.get("part_name", p.get("name", "")))
        if not nom.startswith("CU_CORTE__V__"):
            continue
        try:
            best = max(best, int(nom.rsplit("V__", 1)[-1]))
        except ValueError:
            continue
    return best


def _is_cu_corte_fin_bar(p: dict, placements: list) -> bool:
    """True solo para la guillotina vertical al final del nest (última placa)."""
    nom = str(p.get("part_name", p.get("name", "")))
    if not nom.startswith("CU_CORTE__V__"):
        return False
    try:
        idx = int(nom.rsplit("V__", 1)[-1])
    except ValueError:
        return False
    fin = _max_cu_corte_v_index(placements)
    return fin > 0 and idx == fin


def _export_cu_bar_inicio_marker(msp, bar_w: float) -> None:
    """Marcador visual de inicio de barra horizontal (no participa en sólidos STEP)."""
    if bar_w <= TOL_GEOM_MM:
        return
    msp.add_line((0.0, 0.0), (0.0, float(bar_w)), dxfattribs={"layer": "BAR_START"})


def _export_cu_bar_inicio_marker_vertical(msp, bar_width_mm: float) -> None:
    """Inicio de barra tras rotar sin_gap: ancho real de la barra en X, en y=0."""
    if bar_width_mm <= TOL_GEOM_MM:
        return
    msp.add_line(
        (0.0, 0.0),
        (float(bar_width_mm), 0.0),
        dxfattribs={"layer": "BAR_START"},
    )


def _fin_x_ultima_pieza_cu(
    sheet: dict | None, placements: list | None = None
) -> float:
    """Coordenada X (nest mm) del extremo + de la última pieza real en la madre."""
    fin = 0.0
    if isinstance(sheet, dict):
        try:
            fin = float(sheet.get("cu_fin_piezas_mm") or 0.0)
        except (TypeError, ValueError):
            fin = 0.0
    if fin > TOL_GEOM_MM:
        return fin
    for p in placements or []:
        if not isinstance(p, dict) or not p.get("cu_largos_piece"):
            continue
        nom = str(p.get("part_name") or p.get("name") or "")
        if nom.startswith("CU_CORTE__") or nom.startswith("TATUAJE"):
            continue
        outer = p.get("outer") or p.get("outer_poly") or []
        if len(outer) < 2:
            continue
        try:
            fin = max(fin, max(float(pt[0]) for pt in outer))
        except Exception:
            continue
    return fin


def _guillotina_fin_pieza_ya_exportada(
    msp,
    fin_x: float,
    bar_w: float,
    *,
    sheet: dict | None = None,
    tol: float | None = None,
) -> bool:
    """True si ya hay CUT_OUTER a ancho completo en el fin de la última pieza."""
    if fin_x <= TOL_GEOM_MM or bar_w <= TOL_GEOM_MM:
        return False
    tol = max(1.0, float(tol or TOL_GEOM_MM * 4))
    vertical = _sheet_export_sin_gap_vertical(sheet)
    for e in msp:
        if str(getattr(e.dxf, "layer", "") or "") != "CUT_OUTER":
            continue
        if e.dxftype() != "LINE":
            continue
        try:
            a, b = e.dxf.start, e.dxf.end
            ax, ay, bx, by = float(a.x), float(a.y), float(b.x), float(b.y)
        except Exception:
            continue
        if vertical:
            if abs(ay - fin_x) > tol or abs(by - fin_x) > tol:
                continue
            span = abs(bx - ax)
        else:
            if abs(ax - fin_x) > tol or abs(bx - fin_x) > tol:
                continue
            span = abs(by - ay)
        if span >= bar_w - tol:
            return True
    return False


def _emit_guillotina_fin_pieza_sin_gap(
    msp,
    fin_x: float,
    bar_w: float,
    *,
    sheet: dict | None = None,
) -> bool:
    """CUT_OUTER a ancho completo de tira al final de la última pieza (CyPTube)."""
    if fin_x <= TOL_GEOM_MM or bar_w <= TOL_GEOM_MM:
        return False
    if _guillotina_fin_pieza_ya_exportada(msp, fin_x, bar_w, sheet=sheet):
        return False
    p1 = (float(fin_x), 0.0)
    p2 = (float(fin_x), float(bar_w))
    if _sheet_export_sin_gap_vertical(sheet):
        pts = _transform_poly_sin_gap_vertical([p1, p2], bar_w)
        if len(pts) < 2:
            return False
        p1, p2 = pts[0], pts[1]
    msp.add_line(p1, p2, dxfattribs={"layer": "CUT_OUTER"})
    return True


def _ensure_guillotina_fin_ultima_pieza_sin_gap(
    msp,
    sheet: dict | None,
    placements: list | None = None,
) -> bool:
    """
    CyPTube: garantiza guillotina a ancho completo tras la última pieza real.
    Siempre (con o sin RTZCU detrás); el RTZCU se procesa aparte.
    """
    if not isinstance(sheet, dict) or not _sheet_is_sin_gap(sheet):
        return False
    if sheet.get("cu_rtz_virtual") or sheet.get("cu_export_amada"):
        return False
    bar_w = _bar_width_mm(sheet)
    fin_x = _fin_x_ultima_pieza_cu(sheet, placements)
    return _emit_guillotina_fin_pieza_sin_gap(msp, fin_x, bar_w, sheet=sheet)


def _ensure_cierre_corte_madre_cu(msp, sheet: dict | None) -> bool:
    """Compat: cierre madre = guillotina fin última pieza (antes de RTZCU)."""
    return _ensure_guillotina_fin_ultima_pieza_sin_gap(msp, sheet)


def _export_cu_guillotina_fin_pieza(msp, p: dict, sheet: dict | None = None) -> bool:
    """
    Inserta CUT_OUTER en el extremo +X de la pieza (guillotina de cierre).
    Respaldo cuando el DXF fuente no aporta segmentos láser (1 pieza Amada).
    """
    outer = p.get("outer") or p.get("outer_poly") or []
    if len(outer) < 2:
        return False
    try:
        xs = [float(pt[0]) for pt in outer]
    except Exception:
        return False
    if not xs:
        return False
    maxx = max(xs)
    bar_w = _bar_width_mm(sheet, float(p.get("cu_bar_w_mm") or 0.0))
    if bar_w <= TOL_GEOM_MM:
        return False
    return _emit_guillotina_fin_pieza_sin_gap(msp, maxx, bar_w, sheet=sheet)


def _canal_es_cama_laser(canal: str | None) -> bool:
    """DXF destinado a carpeta CAMA LASER (no Robot Láser / plasma)."""
    u = str(canal or "").strip().upper()
    return bool(u) and "CAMA LASER" in u and "ROBOT" not in u


def _sheet_is_sin_gap(sheet: dict | None) -> bool:
    if not isinstance(sheet, dict):
        return False
    # RTZCU siempre con_gap / STEP (nunca CyPTube vertical).
    if sheet.get("cu_rtz_virtual"):
        return False
    return (
        str(sheet.get("cu_modo_separacion_barra") or "").strip().lower() == "sin_gap"
    )


def _sheet_export_sin_gap_vertical(sheet: dict | None) -> bool:
    """Rotación vertical CyPTube en DXF (toda madre sin_gap; RTZCU nunca)."""
    if not _sheet_is_sin_gap(sheet):
        return False
    if isinstance(sheet, dict) and sheet.get("cu_rtz_virtual"):
        return False
    return True


def _bar_width_mm(sheet: dict | None, fallback: float = 0.0) -> float:
    if not isinstance(sheet, dict):
        return float(fallback or 0.0)
    return float(sheet.get("width") or sheet.get("Width") or fallback or 0.0)


def _sin_gap_vertical_matrix(bar_w_mm: float) -> Matrix44:
    return Matrix44.translate(float(bar_w_mm), 0.0, 0.0) @ Matrix44.z_rotate(
        math.pi / 2.0
    )


def _compose_sin_gap_vertical(m: Matrix44, sheet: dict | None) -> Matrix44:
    if not _sheet_export_sin_gap_vertical(sheet):
        return m
    bw = _bar_width_mm(sheet)
    if bw <= TOL_GEOM_MM:
        return m
    return _sin_gap_vertical_matrix(bw) @ m


def _transform_xy_sin_gap_vertical(
    x: float, y: float, bar_w_mm: float
) -> tuple[float, float]:
    return -float(y) + float(bar_w_mm), float(x)


def _transform_poly_sin_gap_vertical(poly, bar_w_mm: float):
    if not poly:
        return poly
    out = []
    for pt in poly:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            out.append(_transform_xy_sin_gap_vertical(pt[0], pt[1], bar_w_mm))
    return out


def _nest_bounds_for_matrix_check(
    outer_bounds: tuple[float, float, float, float] | None,
    sheet: dict | None,
) -> tuple[float, float, float, float] | None:
    """BBox del nest en el mismo espacio que la matriz de export (vertical si sin_gap)."""
    if not outer_bounds:
        return outer_bounds
    if not _sheet_export_sin_gap_vertical(sheet):
        return outer_bounds
    minx, miny, maxx, maxy = outer_bounds
    bw = _bar_width_mm(sheet)
    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    tc = [_transform_xy_sin_gap_vertical(x, y, bw) for x, y in corners]
    xs = [c[0] for c in tc]
    ys = [c[1] for c in tc]
    return (min(xs), min(ys), max(xs), max(ys))


def _ring_for_sin_gap_export(ring, sheet: dict | None, p: dict | None = None):
    if not _sheet_export_sin_gap_vertical(sheet):
        return ring
    bw = _bar_width_mm(
        sheet, float((p or {}).get("cu_bar_w_mm") or 0.0)
    )
    return _transform_poly_sin_gap_vertical(ring, bw)

def _purge_entities_on_layers(msp, layer_names: set[str]) -> None:
    targets = {str(n).strip().upper() for n in layer_names if str(n or "").strip()}
    if not targets:
        return
    for ent in list(msp):
        layer_u = str(getattr(ent.dxf, "layer", "") or "").strip().upper()
        if layer_u in targets:
            try:
                msp.delete_entity(ent)
            except Exception:
                pass


def _purge_unused_layers(doc, keep: set[str] | None = None) -> None:
    msp = doc.modelspace()
    used = {str(e.dxf.layer) for e in msp if getattr(e.dxf, "layer", None)}
    if keep:
        used |= set(keep)
    for layer in list(doc.layers):
        name = str(layer.dxf.name)
        if name in used or name == "0":
            continue
        try:
            doc.layers.remove(name)
        except Exception:
            pass


def _setup_layers(
    doc,
    *,
    solo_cobre: bool = False,
    omit_plate: bool = False,
    omit_plate_text: bool = False,
):
    layers = doc.layers
    def ensure(name, color_index):
        if name not in layers:
            layers.new(name, dxfattribs={"color": int(color_index)})
        else:
            layers.get(name).dxf.color = int(color_index)

    ensure("CUT_OUTER", 1)
    ensure("CUT_INNER", 2)
    ensure("MARK", 4)
    if solo_cobre:
        ensure("CUT_CU", 1)
        ensure("BAR_START", 8)
        ensure("ARGA_META", 8)
        ensure("FIXTURE", 8)
    if not omit_plate:
        ensure("Plate", 3)
    if not solo_cobre and not omit_plate_text:
        ensure("Plate_Text", 7)
        ensure("RTZ_LABEL", 4)
    elif not solo_cobre:
        ensure("RTZ_LABEL", 4)


def _purge_capas_no_produccion_cobre(doc) -> None:
    """DXF cobre láser: solo capas con geometría de corte/marcaje."""
    msp = doc.modelspace()
    used = {str(e.dxf.layer) for e in msp if getattr(e.dxf, "layer", None)}
    for layer in list(doc.layers):
        name = str(layer.dxf.name)
        if name in used:
            continue
        try:
            doc.layers.remove(name)
        except Exception:
            pass


def _strip_arga_meta_entities(msp) -> None:
    """Quita ARGA_META del DXF de máquina (auditoría interna; el acero no lo lleva)."""
    doomed = [
        e
        for e in msp
        if str(getattr(e.dxf, "layer", "") or "").upper() == "ARGA_META"
    ]
    for e in doomed:
        try:
            msp.delete_entity(e)
        except Exception:
            try:
                e.destroy()
            except Exception:
                pass


def _clone_prod_entity(msp, ent, layer: str) -> bool:
    """Clona entidad de corte/marcaje sin arrastrar handles/reactores de ezdxf."""
    typ = ent.dxftype()
    attrs = {"layer": layer}
    try:
        if typ == "LINE":
            msp.add_line(ent.dxf.start, ent.dxf.end, dxfattribs=attrs)
            return True
        if typ == "CIRCLE":
            msp.add_circle(ent.dxf.center, float(ent.dxf.radius), dxfattribs=attrs)
            return True
        if typ == "ARC":
            msp.add_arc(
                ent.dxf.center,
                float(ent.dxf.radius),
                float(ent.dxf.start_angle),
                float(ent.dxf.end_angle),
                dxfattribs=attrs,
            )
            return True
        if typ == "LWPOLYLINE":
            pts = list(ent.get_points("xyb"))
            if len(pts) < 2:
                return False
            msp.add_lwpolyline(
                pts,
                format="xyb",
                dxfattribs={**attrs, "closed": bool(getattr(ent, "closed", False) or ent.closed)},
            )
            return True
        if typ == "POLYLINE":
            # Degenera a LWPOLY si es 2D.
            try:
                pts = [(float(v.dxf.location.x), float(v.dxf.location.y), 0.0) for v in ent.vertices]
            except Exception:
                return False
            if len(pts) < 2:
                return False
            msp.add_lwpolyline(
                pts,
                format="xyb",
                dxfattribs={**attrs, "closed": bool(ent.is_closed)},
            )
            return True
        if typ == "ELLIPSE":
            # Aprox. no: dejar caer — AutoCAD a veces rechaza ELLIPSE rara de terceros.
            return False
        if typ == "SPLINE":
            return False
        if typ == "POINT":
            msp.add_point(ent.dxf.location, dxfattribs=attrs)
            return True
    except Exception:
        return False
    return False


def _rewrite_cobre_doc_autocad_safe(doc):
    """
    Reescribe el DXF cobre a R2000 solo con geometría de máquina.

    AutoCAD 2026: DXF de terceros (ezdxf AC1024 + fingerprint en OBJECTS /
    DictionaryVariables) → 'Invalid or incomplete DXF input -- drawing discarded'
    y pantalla negra + 'Press ENTER to continue'. El acero no falla igual porque
    suele abrirse desde flujos con Plate/vista más 'nativa'; aquí forzamos un
    DXF mínimo que AutoCAD acepta.
    """
    keep_exact = {"MARK", "BAR_START", "FIXTURE", "PLATE", "CUT_CU"}
    src = [
        e
        for e in doc.modelspace()
        if (
            str(getattr(e.dxf, "layer", "") or "").upper().startswith("CUT_")
            or str(getattr(e.dxf, "layer", "") or "").upper() in keep_exact
        )
    ]
    out = ezdxf.new("R2000")
    out.header["$INSUNITS"] = int(doc.header.get("$INSUNITS", 4) or 4)
    out.header["$MEASUREMENT"] = 1
    for name, color in (
        ("CUT_OUTER", 1),
        ("CUT_INNER", 3),
        ("MARK", 4),
        ("BAR_START", 8),
        ("FIXTURE", 5),
        ("CUT_CU", 1),
        ("Plate", 3),
    ):
        if name not in out.layers:
            out.layers.new(name, dxfattribs={"color": color})
    msp = out.modelspace()
    for ent in src:
        layer = str(getattr(ent.dxf, "layer", "") or "0")
        _clone_prod_entity(msp, ent, layer)
    for layer in out.layers:
        try:
            layer.on()
            layer.thaw()
        except Exception:
            pass
    return out


def _finalize_cobre_dxf_for_autocad(doc, sheet: dict | None = None):
    """Deja el DXF cobre listo para AutoCAD (sin repair posterior)."""
    _ = sheet
    _strip_arga_meta_entities(doc.modelspace())
    clean = _rewrite_cobre_doc_autocad_safe(doc)
    _sync_dxf_header_view(clean)
    return clean


def _assert_dxf_autocad_safe_on_disk(path: str) -> None:
    """Falla el export si el archivo en disco aún no es aceptable para AutoCAD."""
    from pathlib import Path

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DxfExportValidationError(f"No se pudo verificar DXF cobre: {exc}") from exc
    if "EOF" not in text[-40:]:
        raise DxfExportValidationError("DXF cobre incompleto (sin EOF).")
    # Fingerprint ezdxf en OBJECTS → AutoCAD 2026: drawing discarded + Press ENTER.
    if re.search(r"1\.\d+\.\d+\s+@\s+20\d\d-", text):
        raise DxfExportValidationError(
            "DXF cobre con fingerprint ezdxf; AutoCAD lo descartaría."
        )
    try:
        doc = ezdxf.readfile(str(p))
    except Exception as exc:
        raise DxfExportValidationError(f"DXF cobre ilegible tras guardar: {exc}") from exc
    ver = str(doc.header.get("$ACADVER") or doc.dxfversion or "")
    if ver not in ("AC1015", "AC1014", "AC1012", "AC1009"):
        raise DxfExportValidationError(
            f"DXF cobre debe ser R2000/AC1015 para AutoCAD, quedó {ver}."
        )
    lim = doc.header.get("$LIMMAX")
    ext = doc.header.get("$EXTMAX")
    if lim is None or ext is None:
        raise DxfExportValidationError("DXF cobre sin $LIMMAX/$EXTMAX tras guardar.")
    try:
        if abs(float(lim[0]) - 420.0) < 0.1 and float(ext[0]) > 500.0:
            raise DxfExportValidationError(
                f"DXF cobre con $LIMMAX A4 ({lim}) y geometría {ext}; AutoCAD abre negro."
            )
    except (TypeError, ValueError, IndexError) as exc:
        raise DxfExportValidationError(f"DXF cobre header LIM/EXT inválido: {exc}") from exc


def _save_cobre_dxf_atomic(doc, out_path: str, sheet: dict | None = None) -> None:
    """Único punto de escritura DXF cobre: R2000 limpio + assert AutoCAD."""
    clean = _finalize_cobre_dxf_for_autocad(doc, sheet)
    _save_dxf_atomic(clean, out_path)
    _assert_dxf_autocad_safe_on_disk(out_path)


def _strip_ezdxf_fingerprint_on_disk(path: str) -> None:
    """Quita el stamp '1.4.x @ ISO-date' que ezdxf mete en OBJECTS (AutoCAD lo rechaza)."""
    import re
    from pathlib import Path

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    # Group code 1 with ezdxf version fingerprint inside DictionaryVariables.
    new, n = re.subn(
        r"(?m)^(\s*1\s*\r?\n)1\.\d+\.\d+\s+@\s+[^\r\n]+",
        r"\g<1>0",
        text,
        count=2,
    )
    if n:
        try:
            p.write_text(new, encoding="utf-8", newline="\n")
        except OSError:
            pass


# =========================================================================
# EXPORTACIÓN PRINCIPAL
# =========================================================================
def export_nest_to_dxf(
    out_path: str,
    sheet: dict,
    placements: list,
    *,
    title: str = "NEST_EXPORT",
    canal: str | None = None,
    draw_holes: bool = True,
    draw_labels: bool = False,  
    draw_marks: bool = True,
    label_height: float = 25.0,
    margin_text: float = 15.0,
    modo_largos_cu: bool = False,
    strict: bool = True,
):
    from modules.nesting_engine.dxf_export_log import (
        format_placement_spec,
        log,
        log_entities_added,
        log_export_done,
        log_export_error,
        log_export_start,
        resolve_export_mode,
    )

    canal_tag = str(canal or title or "DXF").strip()
    log_export_start(
        out_path, sheet, placements, canal=canal_tag, title=title, strict=strict
    )
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    solo_cobre = bool(
        modo_largos_cu
        or (isinstance(sheet, dict) and sheet.get("modo_largos_cu"))
        or any(bool(p.get("cu_largos_piece")) for p in (placements or []))
    )

    sheet_len = float(sheet.get("length", sheet.get("Length", 0)) or 0)
    sheet_w = float(sheet.get("width", sheet.get("Width", 0)) or 0)

    # Cobre: R2000 desde el inicio. R2010+ezdxf OBJECTS → AutoCAD 2026 descarta el DWG.
    doc = ezdxf.new(dxfversion="R2000" if solo_cobre else "R2010")
    doc.header["$INSUNITS"] = 4
    # Cama láser: sin contorno Plate ni título (máquina cobre + láser compartida).
    omit_plate_frame = _canal_es_cama_laser(canal_tag)
    _setup_layers(
        doc,
        solo_cobre=solo_cobre,
        omit_plate=omit_plate_frame,
        omit_plate_text=omit_plate_frame,
    )

    msp = doc.modelspace()

    _sheet_bar_l = sheet_len
    _sheet_bar_w = sheet_w

    if sheet_len > TOL_GEOM_MM and _sheet_bar_w > TOL_GEOM_MM and not omit_plate_frame:
        L, W = sheet_len, _sheet_bar_w
        sheet_poly = [(0, 0), (L, 0), (L, W), (0, W)]
        _add_lwpolyline(msp, sheet_poly, layer="Plate", closed=True)

    if not solo_cobre and not omit_plate_frame:
        material = sheet.get("material", sheet.get("Material", ""))
        thickness = sheet.get("thickness", sheet.get("Thickness", ""))
        arga_code = sheet.get("arga_code", sheet.get("Arga Code", ""))
        L, W = sheet_len, _sheet_bar_w

        header = f"{title} | {arga_code} | {material} | THK:{thickness} | {L:.1f}x{W:.1f} mm"
        msp.add_text(
            header,
            dxfattribs={"layer": "Plate_Text", "height": label_height}
        ).set_placement((0, W + margin_text))

    if solo_cobre and _sheet_bar_w > TOL_GEOM_MM and not _sheet_is_sin_gap(sheet):
        _export_cu_bar_inicio_marker(msp, _sheet_bar_w)

    cache_blocks = {} 
    exported_pieces = 0
    export_nest_to_dxf._plasma_bounds_cache = []

    try:
        for i, p in enumerate(placements, start=1):
            if solo_cobre and bool(p.get("cu_largos_piece")):
                if not float(p.get("cu_bar_w_mm") or 0):
                    p["cu_bar_w_mm"] = _sheet_bar_w
                if not float(p.get("cu_bar_l_mm") or 0):
                    p["cu_bar_l_mm"] = _sheet_bar_l

            ruta_original = p.get("ruta")
            part_name = str(p.get("part_name", p.get("name", f"PART_{i}")))
            # CU_CORTE__ (guillotinas / relieves): todas van al DXF como CUT_OUTER abierto.
            prefer_source = bool(p.get("prefer_source_dxf"))
            compensated = bool(p.get("compensated"))
            plasma_export = bool(p.get("plasma_export"))
            cu_largos_piece = bool(p.get("cu_largos_piece"))
            needs_strict = strict and _placement_needs_strict_source(p)
            count_before = _msp_count(msp)
            export_mode = resolve_export_mode(p)
            log(f"PIEZA {i}/{len(placements)}: {format_placement_spec(p, index=i)}")
            log(f"  modo={export_mode}")

            from modules.dxf_export.dispatcher import export_piece as dxf_export_piece
            from modules.dxf_export.validate import piece_clearance_record

            piece_bounds_cache: list = getattr(
                export_nest_to_dxf, "_plasma_bounds_cache", []
            )
            if not hasattr(export_nest_to_dxf, "_plasma_bounds_cache"):
                export_nest_to_dxf._plasma_bounds_cache = piece_bounds_cache

            mode, ok_channel = dxf_export_piece(
                msp,
                doc,
                p,
                draw_holes=draw_holes,
                draw_marks=draw_marks,
                strict=strict,
                solo_cobre=solo_cobre,
                cache_blocks=cache_blocks,
                sheet=sheet,
                all_piece_bounds=piece_bounds_cache if plasma_export else None,
            )

            if plasma_export:
                if not ok_channel:
                    if strict:
                        _fail_export(
                            part_name,
                            str(
                                p.pop("_plasma_validation_error", "")
                                or "plasma: sin contorno exportable desde el nest"
                            ),
                        )
                    else:
                        continue
                new_entities = _msp_snapshot(msp)[count_before:]
                log_entities_added(part_name, new_entities, mode=export_mode, ok=bool(new_entities))
                if new_entities:
                    exported_pieces += 1
                    rec = piece_clearance_record(new_entities)
                    if rec:
                        piece_bounds_cache.append(rec)
                continue

            if bool(p.get("compensated_plasma_source")) and str(p.get("ruta") or "").strip():
                if not ok_channel:
                    if strict:
                        _fail_export(
                            part_name,
                            "compensación plasma sin contorno exterior exportable",
                        )
                    else:
                        continue

            new_entities = _msp_snapshot(msp)[count_before:]

            if strict and new_entities:
                _validate_production_entities(
                    new_entities,
                    p,
                    sheet_len=sheet_len,
                    sheet_w=sheet_w,
                    solo_cobre=solo_cobre,
                    sheet=sheet,
                )
                exported_pieces += 1
            elif new_entities:
                exported_pieces += 1

            log_entities_added(
                part_name,
                new_entities,
                mode=export_mode,
                ok=bool(new_entities),
            )

        if strict and exported_pieces == 0:
            raise DxfExportValidationError(
                "La hoja no exportó ninguna pieza con geometría de corte válida."
            )

        if omit_plate_frame:
            keep_plate = bool(
                isinstance(sheet, dict)
                and (
                    sheet.get("cu_rtz_virtual")
                    or any(
                        str(p.get("part_name") or "").startswith("BORDE_RETAZO_")
                        for p in (placements or [])
                    )
                )
            )
            if not keep_plate:
                _purge_entities_on_layers(msp, {"Plate", "Plate_Text"})

        if _sheet_is_sin_gap(sheet):
            if solo_cobre and not (
                isinstance(sheet, dict) and sheet.get("cu_rtz_virtual")
            ):
                _export_cu_bar_inicio_marker_vertical(
                    msp, _bar_width_mm(sheet, _sheet_bar_w)
                )
            if not (isinstance(sheet, dict) and sheet.get("cu_rtz_virtual")):
                sheet_len, sheet_w = float(sheet_w), float(sheet_len)
                _sheet_bar_l = sheet_len
                _sheet_bar_w = sheet_w

        if solo_cobre:
            _purge_capas_no_produccion_cobre(doc)
        else:
            _purge_unused_layers(doc)

        if solo_cobre:
            if not _sheet_cu_exporta_cortes_segmentados(sheet):
                from modules.cobre_step_audit import validate_cut_outer_piece_count

                validate_cut_outer_piece_count(
                    msp,
                    placements,
                    sheet_label=str(title or out_path),
                    strict=strict,
                )

        if strict:
            _validate_full_sheet(
                msp,
                sheet_len=sheet_len,
                sheet_w=sheet_w,
                solo_cobre=solo_cobre,
                skip_bounds=_sheet_is_sin_gap(sheet) and solo_cobre,
                sheet=sheet,
            )
            _validate_dxf_document(doc)

        if solo_cobre:
            _save_cobre_dxf_atomic(
                doc, out_path, sheet if isinstance(sheet, dict) else None
            )
        else:
            _save_dxf_atomic(doc, out_path)
        log_export_done(out_path, canal=canal_tag, exported_pieces=exported_pieces)
    except DxfExportValidationError as exc:
        log_export_error(out_path, exc, canal=canal_tag)
        if os.path.isfile(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        raise

    return out_path

def export_all_sheets(out_dir, nests, *, base_name="NEST", draw_holes=True, draw_labels=False, draw_marks=True):
    os.makedirs(out_dir, exist_ok=True)
    exported = []
    for idx, n in enumerate(nests, start=1):
        sheet = n.get("sheet", {})
        placements = n.get("placements", [])
        out_path = os.path.join(out_dir, f"{base_name}_SHEET_{idx:02d}.dxf")
        export_nest_to_dxf(out_path, sheet, placements, title=f"{base_name} - SHEET {idx}", 
                           draw_holes=draw_holes, draw_labels=draw_labels, draw_marks=draw_marks)
        exported.append(out_path)
    return exported

class NestExporter:
    def export_all_sheets(self, job_folder_or_out_dir, *args, **kwargs):
        # Esta clase es un puente para mantener compatibilidad con la UI
        return []