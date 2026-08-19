import os

from scene_resolver import PIPELINE_LASER_LINES, get_active_laser_line

JSON_COORD_DECIMALS = 3

ORIGIN_TOLERANCE_MM = 1.0
BBOX_DIM_TOLERANCE_MM = 3.0

# Margen laser desde Esq1 PLATE hacia +X/+Y de UF-2 (dentro de la placa).
# Valores negativos empujan paths fuera de la cama (contra las flechas UF-2).
OFFSET_CAMA_B = (3.0, 3.0)

OFFSET_CAMA_A = OFFSET_CAMA_B  # compatibilidad interna del lector; no usar para generación LS

UF_BY_LINE = {
    "L4": {
        "UF2": {"x": 4342.192, "y": -968.717, "z": -754.772},
        "UF2": {"x": 4374.150, "y": 965.191, "z": -757.019},
    },
    "L3": {
        "UF2": {"x": 4159.328, "y": -970.258, "z": -741.078},
        "UF2": {"x": 4219.111, "y": 962.471, "z": -701.319},
    },
}


def normalize_laser_line_for_uf(laser_line=None):
    value = (laser_line or get_active_laser_line() or "L3").strip().upper()
    if value not in UF_BY_LINE:
        value = "L3"
    return value


def get_uf(laser_line, uf_name):
    line = normalize_laser_line_for_uf(laser_line)
    uf = UF_BY_LINE[line].get(uf_name)
    if not uf:
        raise ValueError("UF desconocido: {0} ({1})".format(uf_name, line))
    return dict(uf)


def anchor_destinations(laser_line, width_mm):
    uf2 = get_uf(laser_line, "UF2")
    uf2 = get_uf(laser_line, "UF2")
    ox_a, oy_a = OFFSET_CAMA_A
    ox_b, oy_b = OFFSET_CAMA_B
    w = float(width_mm or 0.0)
    return {
        "cama_b_ref_esq1": [
            round(uf2["x"] + ox_a, JSON_COORD_DECIMALS),
            round(uf2["y"] + oy_a, JSON_COORD_DECIMALS),
        ],
        "cama_b_esq2": [
            round(uf2["x"] + ox_b, JSON_COORD_DECIMALS),
            round(uf2["y"] + oy_b, JSON_COORD_DECIMALS),
        ],
        "cama_b_delta": [
            round((uf2["x"] + ox_b) - w, JSON_COORD_DECIMALS),
            round(uf2["y"] + oy_b, JSON_COORD_DECIMALS),
        ],
    }


def dxf_to_uf2(x, y, sheet_info=None, laser_line=None):
    """DXF (mm) -> UF-2 local cama B para metadata/preflight.

    X_UF2 = Y_DXF + 3
    Y_UF2 = sheet_width - X_DXF - 3
    """
    w = sheet_width_mm(sheet_info)
    margin = OFFSET_CAMA_B[0]
    return (
        round(float(y) + margin, JSON_COORD_DECIMALS),
        round(float(w) - float(x) - margin, JSON_COORD_DECIMALS),
    )


def sheet_width_mm(sheet_info, default=6096.0):
    if sheet_info and sheet_info.get("width_mm") is not None:
        return float(sheet_info["width_mm"])
    return float(default)


def sheet_height_mm(sheet_info, default=1219.2):
    if sheet_info and sheet_info.get("height_mm") is not None:
        return float(sheet_info["height_mm"])
    return float(default)


# Margen extra sobre el rectangulo de placa en UF-2 local (mm).
BED_BOUNDS_MARGIN_MM = 3.0


def uf2_local_plate_bounds(sheet_info, margin_mm=BED_BOUNDS_MARGIN_MM):
    """
    Rectangulo valido en UF-2 local (dxf X -> uf X, dxf Y -> uf Y).
    """
    w = sheet_width_mm(sheet_info)
    h = sheet_height_mm(sheet_info)
    ox, oy = OFFSET_CAMA_A
    m = float(margin_mm or 0.0)
    return {
        "uf_x_min": round(ox - m, JSON_COORD_DECIMALS),
        "uf_x_max": round(ox + w + m, JSON_COORD_DECIMALS),
        "uf_y_min": round(oy - m, JSON_COORD_DECIMALS),
        "uf_y_max": round(oy + h + m, JSON_COORD_DECIMALS),
        "width_mm": w,
        "height_mm": h,
        "margin_mm": m,
    }


def uf2_local_coord_within_plate(
    uf_x, uf_y, sheet_info, margin_mm=BED_BOUNDS_MARGIN_MM
):
    if not sheet_info:
        return False
    bounds = uf2_local_plate_bounds(sheet_info, margin_mm)
    x = float(uf_x)
    y = float(uf_y)
    return (
        bounds["uf_x_min"] <= x <= bounds["uf_x_max"]
        and bounds["uf_y_min"] <= y <= bounds["uf_y_max"]
    )


def looks_like_uf2_world_coords(uf_x, uf_y, laser_line=None):
    """
    Mundo/cell UF-2: X cerca del marco calibrado (~4k) y Y negativo.
    UF-2 local valido en placa usa Y positivo (offset + altura de placa).
    """
    x = float(uf_x)
    y = float(uf_y)
    line = normalize_laser_line_for_uf(laser_line or get_active_laser_line())
    uf2 = get_uf(line, "UF2")
    wx = float(uf2["x"])
    wy = float(uf2["y"])

    near_uf_origin = abs(x - wx) <= 600 and abs(y - wy) <= 600
    classic_world_band = x >= 3000 and y <= -200
    return near_uf_origin or classic_world_band


def is_uf2_world_coord_leak(
    uf_x,
    uf_y,
    sheet_info=None,
    laser_line=None,
    margin_mm=BED_BOUNDS_MARGIN_MM,
):
    """
    True si parecen coords de mundo/cell en UF-2, no locales de placa.
    Respeta el tamano de placa (240X48, 240X60, 240X72, 240X96, ...).
    """
    if uf2_local_coord_within_plate(uf_x, uf_y, sheet_info, margin_mm):
        return False
    return looks_like_uf2_world_coords(uf_x, uf_y, laser_line)


def assert_uf2_xy_on_bed(
    uf_x,
    uf_y,
    sheet_info,
    margin_mm=BED_BOUNDS_MARGIN_MM,
    dxf_x=None,
    dxf_y=None,
    laser_line=None,
):
    """
    Aborta si el punto UF-2 esta fuera de la placa (local) o parece coords de mundo.
    """
    if not sheet_info:
        raise ValueError("BED_ABORT: meta.sheet ausente; no se puede validar la cama.")

    x = float(uf_x)
    y = float(uf_y)
    line = normalize_laser_line_for_uf(laser_line or get_active_laser_line())

    if is_uf2_world_coord_leak(
        x, y, sheet_info=sheet_info, laser_line=line, margin_mm=margin_mm
    ):
        world_esq1 = anchor_destinations(line, 0.0)["cama_b_ref_esq1"]
        raise ValueError(
            "BED_ABORT: UF-2 con coords de mundo/cell ({0:.3f}, {1:.3f}); "
            "use transform local dxf_to_uf2. Destino mundo esq1=({2}, {3}).".format(
                x, y, world_esq1[0], world_esq1[1]
            )
        )

    bounds = uf2_local_plate_bounds(sheet_info, margin_mm)
    if (
        x < bounds["uf_x_min"]
        or x > bounds["uf_x_max"]
        or y < bounds["uf_y_min"]
        or y > bounds["uf_y_max"]
    ):
        raise ValueError(
            "BED_ABORT: punto UF-2 ({0:.3f}, {1:.3f}) fuera de placa local. "
            "Limites x=[{2}, {3}] y=[{4}, {5}] (placa {6}x{7} mm, margen {8} mm). "
            "Revisa calibracion escena.robx / origen UF-2.".format(
                x,
                y,
                bounds["uf_x_min"],
                bounds["uf_x_max"],
                bounds["uf_y_min"],
                bounds["uf_y_max"],
                bounds["width_mm"],
                bounds["height_mm"],
                bounds["margin_mm"],
            )
        )

    if dxf_x is not None and dxf_y is not None:
        validate_uf2_placement_applied(
            x,
            y,
            laser_line=line,
            dxf_x=dxf_x,
            dxf_y=dxf_y,
            sheet_info=sheet_info,
        )

    return True


def validate_cama_b_ref_bed_preflight(sheet_info, logs, margin_mm=BED_BOUNDS_MARGIN_MM):
    """
    Preflight en PQArt: esquinas DXF de la placa deben caer dentro del marco UF-2 local.
    """
    bounds = uf2_local_plate_bounds(sheet_info, margin_mm)
    logs.append(
        "BED_UF2_BOUNDS x=[{0},{1}] y=[{2},{3}] placa={4}x{5}mm margen={6}".format(
            bounds["uf_x_min"],
            bounds["uf_x_max"],
            bounds["uf_y_min"],
            bounds["uf_y_max"],
            bounds["width_mm"],
            bounds["height_mm"],
            bounds["margin_mm"],
        )
    )

    w = bounds["width_mm"]
    h = bounds["height_mm"]
    corners = [
        (0.0, 0.0, "esq1"),
        (w, 0.0, "esq2"),
        (0.0, h, "esq3"),
        (w, h, "esq4"),
    ]
    for dx, dy, label in corners:
        ux, uy = dxf_to_uf2(dx, dy, sheet_info)
        assert_uf2_xy_on_bed(ux, uy, sheet_info, margin_mm=margin_mm, dxf_x=dx, dxf_y=dy)
        logs.append(
            "BED_CORNER_OK {0} dxf({1},{2}) -> uf2({3},{4})".format(
                label, dx, dy, ux, uy
            )
        )

    logs.append("BED_PREFLIGHT_OK=Esquinas de placa dentro de UF-2 local")
    return bounds


def e1_reference_x_from_dxf(dxf_x, sheet_info=None, default_width=6096.0):
    width = sheet_width_mm(sheet_info, default_width)
    x = float(dxf_x)
    if x < 0.0:
        x = 0.0
    if x > width:
        x = width
    return x


PLACEMENT_MATCH_TOL_MM = 1.5


def validate_uf2_placement_applied(
    uf_x, uf_y, laser_line=None, dxf_x=None, dxf_y=None, sheet_info=None
):
    """
    Aborta si el path usa coords de mundo en UF-2 o DXF crudo sin transform local.
    """
    x = float(uf_x)
    y = float(uf_y)
    line = normalize_laser_line_for_uf(laser_line or get_active_laser_line())
    ox, oy = OFFSET_CAMA_A
    world_esq1 = anchor_destinations(line, 0.0)["cama_b_ref_esq1"]

    if is_uf2_world_coord_leak(
        x, y, sheet_info=sheet_info, laser_line=line
    ):
        raise ValueError(
            "PLACEMENT_ABORT: UF-2 con coordenadas de mundo/cell ({0:.3f}, {1:.3f}); "
            "se esperaban locales (p. ej. esquina dxf(0,0) -> ({2}, {3})). "
            "Destino mundo L{4} esq1=({5}, {6}).".format(
                x, y, ox, oy, line, world_esq1[0], world_esq1[1]
            )
        )

    if dxf_x is not None and dxf_y is not None:
        dx = float(dxf_x)
        dy = float(dxf_y)
        ex, ey = dxf_to_uf2(dx, dy, sheet_info, line)
        if abs(x - ex) > PLACEMENT_MATCH_TOL_MM or abs(y - ey) > PLACEMENT_MATCH_TOL_MM:
            raise ValueError(
                "PLACEMENT_ABORT: UF-2 ({0:.3f}, {1:.3f}) no coincide con transform local "
                "esperado ({2:.3f}, {3:.3f}) desde DXF ({4:.3f}, {5:.3f}).".format(
                    x, y, ex, ey, dx, dy
                )
            )
        if (
            abs(x - dx) <= PLACEMENT_MATCH_TOL_MM
            and abs(y - dy) <= PLACEMENT_MATCH_TOL_MM
        ):
            raise ValueError(
                "PLACEMENT_ABORT: UF-2 igual al DXF crudo ({0:.3f}, {1:.3f}); "
                "falta offset Cama B ({2}, {3}).".format(dx, dy, ox, oy)
            )
        return True

    if abs(x - ox) > PLACEMENT_MATCH_TOL_MM or abs(y - oy) > PLACEMENT_MATCH_TOL_MM:
        raise ValueError(
            "PLACEMENT_ABORT: ancla dxf(0,0) en UF-2 ({0:.3f}, {1:.3f}); "
            "esperado local ({2}, {3}).".format(x, y, ox, oy)
        )
    return True


def validate_dxf_origin_on_cama_b_ref(sheet_info=None, logs=None, laser_line=None):
    """
    Comprueba que dxf (0,0) mapee a la zona UF-2 de Cama B antes de generar paths.
    """
    line = laser_line or get_active_laser_line()
    uf_x, uf_y = dxf_to_uf2(0.0, 0.0, sheet_info, line)
    validate_uf2_placement_applied(
        uf_x, uf_y, laser_line=line, dxf_x=0.0, dxf_y=0.0, sheet_info=sheet_info
    )
    if logs is not None:
        width = sheet_width_mm(sheet_info)
        anchor = anchor_destinations(line, width)["cama_b_ref_esq1"]
        logs.append(
            "PLACEMENT_ANCHOR_OK dxf(0,0)->uf_local({0:.3f},{1:.3f}) mundo_esq1=({2},{3})".format(
                uf_x, uf_y, anchor[0], anchor[1]
            )
        )
    return uf_x, uf_y


def log_placement_status(logs, meta, sheet_info=None, bed_code="A"):
    placement = (meta or {}).get("placement") or {}
    ok = placement.get("placement_ok", True)
    logs.append("PLACEMENT_OK={0}".format(ok))
    for warning in placement.get("placement_warnings") or []:
        logs.append("PLACEMENT_WARNING={0}".format(warning))
    if not ok:
        logs.append(
            "PLACEMENT_ERROR=Colocacion no validada; se continua con transform acordado"
        )

    if str(bed_code).strip().upper() != "A":
        return

    line = get_active_laser_line()
    width = sheet_width_mm(sheet_info)
    dest = anchor_destinations(line, width)["cama_b_ref_esq1"]
    uf_x, uf_y = dxf_to_uf2(0.0, 0.0, sheet_info, line)
    logs.append(
        "PLACEMENT_MAP dxf_esq1(0,0) -> uf2_local=({0:.3f},{1:.3f}) laser_line={2}".format(
            uf_x, uf_y, line
        )
    )
    logs.append(
        "PLACEMENT_TARGET mundo_esq1=({0},{1}) modo=uf_local_directo".format(
            dest[0], dest[1]
        )
    )
    validate_uf2_placement_applied(
        uf_x, uf_y, laser_line=line, dxf_x=0.0, dxf_y=0.0, sheet_info=sheet_info
    )
    logs.append("PLACEMENT_ANCHOR_OK dxf(0,0) anclado en UF-2 local")
    validate_cama_b_ref_bed_preflight(sheet_info, logs)


def _contour_area(contour):
    pts = contour.get("points") if isinstance(contour, dict) else contour
    if not pts or len(pts) < 3:
        return 0.0
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[(i + 1) % n][0]), float(pts[(i + 1) % n][1])
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def select_primary_plate_contour(plate_contours):
    if not plate_contours:
        return None, 0
    if len(plate_contours) == 1:
        return plate_contours[0], 1
    best = max(plate_contours, key=_contour_area)
    return best, len(plate_contours)


def build_meta_placement(sheet_info, ref_bbox, plate_contour_count=0):
    warnings = []
    placement_ok = True

    if plate_contour_count > 1:
        warnings.append(
            "Varios contornos PLATE ({0}); se usa el de mayor area".format(
                plate_contour_count
            )
        )

    rules = {
        "cama_b_ref": {
            "anchor": "Esq1_PLATE",
            "rotate_deg": 0,
            "rotate_origin": [0.0, 0.0],
            "offset_mm": list(OFFSET_CAMA_A),
            "uf": "UF2",
        },
        "cama_b": {
            "anchor": "Esq1_PLATE",
            "rotate_deg": 90,
            "rotate_origin": [0.0, 0.0],
            "offset_mm": list(OFFSET_CAMA_B),
            "uf": "UF2",
        },
    }

    base = {
        "placement_ok": False,
        "placement_warnings": warnings,
        "rules": rules,
        "offsets_mm": {"cama_b_ref": list(OFFSET_CAMA_A), "cama_b": list(OFFSET_CAMA_B)},
        "uf_by_line": UF_BY_LINE,
        "tolerances_mm": {
            "origin_plate": ORIGIN_TOLERANCE_MM,
            "bbox_dimensions": BBOX_DIM_TOLERANCE_MM,
        },
        "destinations_by_line": {},
    }

    if not sheet_info:
        warnings.append("Aviso 2: No hay PLATE/sheet valido en el DXF")
        base["placement_warnings"] = warnings
        return base

    origin_x = float(sheet_info.get("origin_x_mm") or 0.0)
    origin_y = float(sheet_info.get("origin_y_mm") or 0.0)
    bbox = sheet_info.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    min_x = float(bbox[0])
    min_y = float(bbox[1])
    max_x = float(bbox[2])
    max_y = float(bbox[3])
    width_mm = float(sheet_info.get("width_mm") or 0.0)
    height_mm = float(sheet_info.get("height_mm") or 0.0)

    if (
        max(abs(origin_x), abs(origin_y), abs(min_x), abs(min_y))
        > ORIGIN_TOLERANCE_MM
    ):
        placement_ok = False
        warnings.append(
            "Aviso 1: Esq1 PLATE no esta en (0,0); origin=({0:.3f},{1:.3f}) bbox_min=({2:.3f},{3:.3f})".format(
                origin_x, origin_y, min_x, min_y
            )
        )

    if ref_bbox and len(bbox) >= 4:
        margin_x = float(ref_bbox[0]) - min_x
        margin_y = float(ref_bbox[1]) - min_y
        warnings.append(
            "Aviso 3: ref_bbox a {0:.1f}mm de Esq1 PLATE en X, {1:.1f}mm en Y (informativo)".format(
                margin_x, margin_y
            )
        )

    dim_err_x = abs((max_x - min_x) - width_mm)
    dim_err_y = abs((max_y - min_y) - height_mm)
    if dim_err_x > BBOX_DIM_TOLERANCE_MM or dim_err_y > BBOX_DIM_TOLERANCE_MM:
        warnings.append(
            "Aviso 4: bbox vs width/height difiere >{0}mm (dx={1:.2f}, dy={2:.2f})".format(
                BBOX_DIM_TOLERANCE_MM, dim_err_x, dim_err_y
            )
        )

    destinations = {}
    for laser_line in PIPELINE_LASER_LINES:
        destinations[laser_line] = anchor_destinations(laser_line, width_mm)

    base["placement_ok"] = placement_ok
    base["placement_warnings"] = warnings
    base["width_mm"] = width_mm
    base["height_mm"] = height_mm
    base["destinations_by_line"] = destinations
    return base


def round_coord_value(value):
    if isinstance(value, float):
        return round(value, JSON_COORD_DECIMALS)
    return value


def uf2_to_dxf_x(uf_x, sheet_info=None, laser_line=None, uf_y=None):
    ox, oy = OFFSET_CAMA_B
    if uf_y is not None:
        return round(float(uf_y) - ox, JSON_COORD_DECIMALS)
    return round(float(uf_x) - oy, JSON_COORD_DECIMALS)


def uf2_to_dxf_x(uf_x, laser_line=None):
    ox, _ = OFFSET_CAMA_A
    return round(float(uf_x) - ox, JSON_COORD_DECIMALS)


def round_contour_coords(contours):
    out = []
    for contour in contours or []:
        item = dict(contour)
        rounded_pts = []
        for point in contour.get("points") or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                rounded_pts.append(
                    [
                        round(float(point[0]), JSON_COORD_DECIMALS),
                        round(float(point[1]), JSON_COORD_DECIMALS),
                    ]
                )
            else:
                rounded_pts.append(point)
        item["points"] = rounded_pts
        out.append(item)
    return out
