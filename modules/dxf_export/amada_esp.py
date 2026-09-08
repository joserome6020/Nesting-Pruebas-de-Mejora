"""
Export DXF pieza ESP. Amada: contorno engañado (+10\") + barrenos, sin marcaje.

El soft Amada legacy espera un perfil alto (pieza 5\" + colchón 10\" = 15\").
El contorno exterior va como un único LWPOLYLINE cerrado (join) en CUT_OUTER.
Barrenos: CIRCLE o LWPOLYLINE con bulges (ranura); nunca polilíneas facetadas.
"""
from __future__ import annotations

import math
import os
from typing import Sequence

from ezdxf.math import Matrix44

from modules.dxf_native_curves import (
    circle_centroid_mean,
    fit_stadium_lwpoly_bulge,
    normalize_ring,
)
from modules.nest_exporter import DxfExportValidationError, _add_lwpolyline

_IN_TO_MM = 25.4
# Colchón inferior para el perfil Amada (pulgadas).
AMADA_ESP_SOFT_PADDING_IN = 10.0


def amada_esp_padding_mm(padding_in: float = AMADA_ESP_SOFT_PADDING_IN) -> float:
    return float(padding_in) * _IN_TO_MM


def _ring_bbox(ring: Sequence) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for pt in ring or []:
        try:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
        except (TypeError, ValueError, IndexError):
            continue
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _shift_ring_xy(ring: Sequence, dx: float, dy: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for pt in ring or []:
        try:
            out.append((float(pt[0]) + dx, float(pt[1]) + dy))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _rotate_ring_90_ccw(ring: Sequence) -> list[tuple[float, float]]:
    """(x, y) → (−y, x)."""
    out: list[tuple[float, float]] = []
    for pt in ring or []:
        try:
            out.append((-float(pt[1]), float(pt[0])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def orient_amada_strip_canal_as_y(
    outer: Sequence,
    holes: Sequence | None = None,
    *,
    canal_mm: float = 5.0 * _IN_TO_MM,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]], bool]:
    """
    Deja el lado más cercano al canal Amada (5\") como alto Y.

    PARTS a veces muestra 5×9.5 (Y largo); Amada/FIXTURA necesita largo en X y
    ~5\" en Y + colchón 10\". Si no se rota, barrenos del DXF fuente (ya en
    orientación tira) quedan fuera del rectángulo engañado.
    """
    bb = _ring_bbox(outer)
    if bb is None:
        return [], [], False
    minx, miny, maxx, maxy = bb
    w = float(maxx) - float(minx)
    h = float(maxy) - float(miny)
    need_rot = abs(h - float(canal_mm)) > abs(w - float(canal_mm))
    if not need_rot:
        outer_o = _shift_ring_xy(outer, -minx, -miny)
        holes_o = [_shift_ring_xy(hh, -minx, -miny) for hh in (holes or []) if hh]
        return outer_o, holes_o, False

    outer_r = _rotate_ring_90_ccw(outer)
    holes_r = [_rotate_ring_90_ccw(hh) for hh in (holes or []) if hh]
    bb2 = _ring_bbox(outer_r)
    if bb2 is None:
        return [], [], False
    dx, dy = -float(bb2[0]), -float(bb2[1])
    outer_o = _shift_ring_xy(outer_r, dx, dy)
    holes_o = [_shift_ring_xy(hh, dx, dy) for hh in holes_r]
    return outer_o, holes_o, True


def build_amada_esp_padded_geometry(
    outer: Sequence,
    holes: Sequence | None = None,
    *,
    padding_in: float = AMADA_ESP_SOFT_PADDING_IN,
    canal_mm: float = 5.0 * _IN_TO_MM,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]], float, float]:
    """
    Normaliza la pieza al origen, orienta canal 5\" en Y, coloca el colchón de
    10\" abajo y sube barrenos a la banda superior (5\" reales de cobre).

    Devuelve (outer_padded, holes_shifted, largo_mm, alto_total_mm).
    """
    outer_o, holes_o, _rot = orient_amada_strip_canal_as_y(
        outer, holes, canal_mm=canal_mm
    )
    bb2 = _ring_bbox(outer_o)
    if bb2 is None:
        return [], [], 0.0, 0.0
    _x0, _y0, maxx2, maxy2 = bb2
    largo_mm = max(0.0, float(maxx2) - float(_x0))
    alto_pieza_mm = max(0.0, float(maxy2) - float(_y0))
    pad_mm = amada_esp_padding_mm(padding_in)
    alto_total_mm = alto_pieza_mm + pad_mm

    # Rectángulo cerrado único: colchón abajo, pieza arriba (como AutoCAD de referencia).
    outer_padded = [
        (0.0, 0.0),
        (largo_mm, 0.0),
        (largo_mm, alto_total_mm),
        (0.0, alto_total_mm),
    ]
    holes_shifted = [_shift_ring_xy(h, 0.0, pad_mm) for h in holes_o if h]
    return outer_padded, holes_shifted, largo_mm, alto_total_mm


def export_amada_esp_joined_outer(msp, outer_ring: Sequence, *, layer: str = "CUT_OUTER") -> bool:
    """Contorno exterior como LWPOLYLINE cerrado (join explícito para STEP/Amada)."""
    pts = normalize_ring(outer_ring, closed=True)
    if len(pts) < 3:
        return False
    if len(pts) >= 2 and math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) < 1e-6:
        pts = pts[:-1]
    _add_lwpolyline(msp, pts, layer, closed=True)
    return True


def _add_lwpolyline_xyb(
    msp,
    points: Sequence[tuple[float, float, float]],
    layer: str,
    *,
    closed: bool = True,
) -> bool:
    if not points or len(points) < 2:
        return False
    msp.add_lwpolyline(
        list(points),
        format="xyb",
        dxfattribs={"layer": layer, "closed": bool(closed)},
    )
    return True


def _write_amada_inner_preserved(msp, entity, layer: str) -> int:
    """CIRCLE o LWPOLYLINE con bulges — sin expandir a segmentos facetados."""
    typ = entity.dxftype()
    if typ == "CIRCLE":
        c = entity.dxf.center
        msp.add_circle(
            (float(c.x), float(c.y)),
            float(entity.dxf.radius),
            dxfattribs={"layer": layer},
        )
        return 1
    if typ == "LWPOLYLINE":
        pts = list(entity.get_points("xyb"))
        if len(pts) < 2:
            return 0
        msp.add_lwpolyline(
            pts,
            format="xyb",
            dxfattribs={"layer": layer, "closed": bool(entity.closed)},
        )
        return 1
    return 0


def _source_outer_bbox_in(part_doc) -> tuple[float, float, float, float] | None:
    """BBox del CUT_OUTER en unidades del archivo (pulgadas en Processed Files)."""
    from ezdxf import bbox as ezdxf_bbox

    from modules.nest_exporter import _clasificar_capa

    staged = []
    for entity in part_doc.modelspace():
        if _clasificar_capa(str(getattr(entity.dxf, "layer", "") or "")) != "outer":
            continue
        if entity.dxftype() not in (
            "LWPOLYLINE",
            "POLYLINE",
            "LINE",
            "ARC",
            "CIRCLE",
            "ELLIPSE",
            "SPLINE",
        ):
            continue
        staged.append(entity)
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


def _amada_source_to_strip_matrix(
    *,
    ox_in: float,
    oy_in: float,
    src_w_in: float,
    src_h_in: float,
    pad_mm: float,
    canal_mm: float = 5.0 * _IN_TO_MM,
) -> Matrix44:
    """
    Origen → (opcional) 90° CCW si el DXF fuente trae el canal en X
    (p. ej. PARTS 5×9.5 vertical) → mm → sube colchón +10\".

    Misma lógica que orient_amada_strip_canal_as_y: Amada quiere ~5\" en Y.

    Nota: en ezdxf, ``A @ B`` aplica A primero (izquierda→derecha).
    """
    need_rot = abs(float(src_h_in) * _IN_TO_MM - float(canal_mm)) > abs(
        float(src_w_in) * _IN_TO_MM - float(canal_mm)
    )
    m = Matrix44.translate(-float(ox_in), -float(oy_in), 0.0)
    if need_rot:
        m = m @ Matrix44.z_rotate(math.radians(90.0))
        # Tras R90 CCW el bbox queda en (−h, 0)–(0, w); empujar a origen.
        m = m @ Matrix44.translate(float(src_h_in), 0.0, 0.0)
    m = m @ Matrix44.scale(_IN_TO_MM, _IN_TO_MM, _IN_TO_MM)
    m = m @ Matrix44.translate(0.0, float(pad_mm), 0.0)
    return m


def _entities_inside_amada_sheet(
    entities,
    sheet_len: float,
    sheet_w: float,
    *,
    margin_mm: float = 1.5,
) -> bool:
    if sheet_len <= 0.5 or sheet_w <= 0.5:
        return True
    from ezdxf import bbox as ezdxf_bbox

    try:
        ext = ezdxf_bbox.extents(list(entities))
    except Exception:
        return True
    return (
        float(ext.extmin.x) >= -margin_mm
        and float(ext.extmin.y) >= -margin_mm
        and float(ext.extmax.x) <= float(sheet_len) + margin_mm
        and float(ext.extmax.y) <= float(sheet_w) + margin_mm
    )


def export_amada_holes_from_source_dxf(
    msp,
    doc,
    ruta: str,
    *,
    pad_mm: float,
    placement: dict | None = None,
) -> bool:
    """
    Clona CUT_INNER del Processed Files (círculos + ranuras con bulge).

    Escala pulgadas→mm, alinea orientación al canal Amada (~5\" en Y) aunque
    PARTS haya rotado el nest y el DXF fuente siga en vertical, normaliza al
    origen del outer y sube barrenos al colchón +10\". Si tras el clone quedan
    fuera de la hoja engañada, falla para que el caller use barrenos del nest.
    """
    from modules.nest_exporter import (
        _clasificar_capa,
        _dxf_outer_origin_mm,
        _import_layers_from_source,
        _msp_count,
        _msp_snapshot,
    )

    try:
        import ezdxf  # noqa: F401

        part_doc = ezdxf.readfile(str(ruta))
    except Exception:
        return False

    inners = [
        e
        for e in part_doc.modelspace()
        if _clasificar_capa(str(getattr(e.dxf, "layer", "") or "")) == "inner"
        and e.dxftype() in ("CIRCLE", "LWPOLYLINE", "POLYLINE")
    ]
    if not inners:
        return False

    bb = _source_outer_bbox_in(part_doc)
    if bb is not None:
        ox_in, oy_in = float(bb[0]), float(bb[1])
        src_w_in = max(1e-9, float(bb[2]) - float(bb[0]))
        src_h_in = max(1e-9, float(bb[3]) - float(bb[1]))
        m = _amada_source_to_strip_matrix(
            ox_in=ox_in,
            oy_in=oy_in,
            src_w_in=src_w_in,
            src_h_in=src_h_in,
            pad_mm=pad_mm,
        )
    else:
        ox_mm, oy_mm = _dxf_outer_origin_mm(str(ruta)) or (0.0, 0.0)
        ox_in = float(ox_mm) / _IN_TO_MM
        oy_in = float(oy_mm) / _IN_TO_MM
        # Sin outer medible: solo origen + escala + pad (sin rotar).
        m = (
            Matrix44.translate(-ox_in, -oy_in, 0.0)
            @ Matrix44.scale(_IN_TO_MM, _IN_TO_MM, _IN_TO_MM)
            @ Matrix44.translate(0.0, float(pad_mm), 0.0)
        )

    count_before = _msp_count(msp)
    added = 0
    for ent in inners:
        try:
            copy = ent.copy()
            copy.transform(m)
            added += _write_amada_inner_preserved(msp, copy, "CUT_INNER")
        except Exception:
            continue
    if added <= 0:
        return False

    new_ents = _msp_snapshot(msp)[count_before:]
    place = placement if isinstance(placement, dict) else {}
    sheet_l = float(place.get("cu_bar_l_mm") or 0.0)
    sheet_w = float(place.get("cu_bar_w_mm") or 0.0)
    if new_ents and not _entities_inside_amada_sheet(new_ents, sheet_l, sheet_w):
        for e in new_ents:
            try:
                msp.delete_entity(e)
            except Exception:
                pass
        return False

    _import_layers_from_source(part_doc, doc, {"CUT_INNER"})
    return True


def export_amada_esp_inner_closed(
    msp,
    ring: Sequence,
    *,
    layer: str = "CUT_INNER",
) -> bool:
    """Barreno Amada: CIRCLE o LWPOLYLINE con bulges (ranura); sin facetas."""
    pts = normalize_ring(ring, closed=True)
    if len(pts) < 3:
        return False

    if len(pts) >= 6:
        circ = circle_centroid_mean(pts)
        if circ is not None:
            _cx, _cy, r, err = circ
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            short = min(w, h)
            if (
                r > 1e-6
                and short > 1e-6
                and err <= max(0.1, r * 0.06)
                and max(w, h) / short <= 1.2
            ):
                msp.add_circle(
                    (float(_cx), float(_cy)),
                    float(r),
                    dxfattribs={"layer": layer},
                )
                return True

    slot = fit_stadium_lwpoly_bulge(pts)
    if slot:
        return _add_lwpolyline_xyb(msp, slot, layer, closed=True)

    # Rectángulo simple (4 esquinas): LWPOLY cerrada exacta, no facetas.
    if len(pts) == 4:
        _add_lwpolyline(msp, pts, layer, closed=True)
        return True

    return False


def validate_amada_esp_entities_closed(entities) -> list[str]:
    """Lista problemas: el soft Amada exige islas cerradas, no segmentos sueltos."""
    issues: list[str] = []
    for ent in entities or []:
        layer = str(getattr(ent.dxf, "layer", "") or "").upper()
        if layer not in ("CUT_OUTER", "CUT_INNER"):
            continue
        typ = ent.dxftype()
        if typ == "LINE":
            issues.append(f"{layer}: segmento LINE suelto (requiere contorno cerrado)")
        elif typ == "ARC":
            issues.append(f"{layer}: ARC suelto (requiere contorno cerrado)")
        elif typ == "LWPOLYLINE":
            if not bool(getattr(ent, "closed", False) or ent.closed):
                issues.append(f"{layer}: LWPOLYLINE sin flag closed")
            else:
                pts = list(ent.get_points("xyb"))
                bulges = [abs(float(p[2] or 0.0)) for p in pts]
                if len(pts) > 6 and max(bulges) < 1e-9:
                    issues.append(
                        f"{layer}: LWPOLYLINE facetada ({len(pts)} vértices); "
                        f"requiere CIRCLE o ranura con bulge"
                    )
        elif typ == "CIRCLE":
            pass
        else:
            issues.append(f"{layer}: tipo {typ} no permitido en corte Amada")
    return issues


def export_amada_esp_piece(
    msp,
    p: dict,
    *,
    doc=None,
    draw_holes: bool = True,
    padding_in: float = AMADA_ESP_SOFT_PADDING_IN,
    strict: bool = True,
) -> bool:
    """
    AMADA/FIXTURA: CUT_OUTER = rectángulo 15\" join; CUT_INNER = barrenos; sin MARK.
    """
    from modules.nest_exporter import _msp_count, _msp_snapshot

    count_before = _msp_count(msp)
    outer = p.get("outer") or p.get("outer_poly")
    holes = p.get("holes") or p.get("inner") or []
    if not outer:
        return False

    pad_mm = amada_esp_padding_mm(padding_in)

    if p.get("cu_amada_outer_padded"):
        outer_p = list(outer)
        holes_p = [list(h) for h in holes if h]
    else:
        outer_p, holes_p, _, _ = build_amada_esp_padded_geometry(
            outer, holes, padding_in=padding_in
        )

    added = export_amada_esp_joined_outer(msp, outer_p, layer="CUT_OUTER")
    holes_ok = False

    if draw_holes:
        ruta_src = str(p.get("ruta_origen") or p.get("ruta") or "").strip()
        if ruta_src and os.path.isfile(ruta_src) and doc is not None:
            holes_ok = export_amada_holes_from_source_dxf(
                msp, doc, ruta_src, pad_mm=pad_mm, placement=p
            )

        if not holes_ok:
            for h in holes_p:
                if not h:
                    continue
                if export_amada_esp_inner_closed(msp, h, layer="CUT_INNER"):
                    added = True
                    holes_ok = True
                elif strict:
                    label = str(p.get("part_name") or p.get("name") or "PIEZA")
                    raise DxfExportValidationError(
                        f"{label}: barreno no exportable con integridad nativa "
                        f"(requiere DXF fuente o ranura/círculo reconocible)"
                    )
        elif holes_ok:
            added = True

    issues = validate_amada_esp_entities_closed(_msp_snapshot(msp)[count_before:])
    if issues and strict:
        label = str(p.get("part_name") or p.get("name") or "PIEZA")
        raise DxfExportValidationError(
            f"{label}: Amada requiere contornos cerrados — " + "; ".join(issues[:3])
        )
    return added
