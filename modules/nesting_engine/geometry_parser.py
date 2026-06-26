import math
import os

import ezdxf
from ezdxf import path
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import linemerge, polygonize

# Constantes de configuración de capas
ESCALA_DXF = 25.4
# Tolerancia CAD: máx. desviación arco→segmento (~0.05 mm).
DXF_CURVE_TOL_MM = float(os.getenv("ARGA_DXF_CURVE_TOL_MM", "0.05"))
DXF_FLATTEN_DISTANCE = DXF_CURVE_TOL_MM / ESCALA_DXF
# Área mínima de barreno (mm²). Casi cero: conservar barrenos microscópicos del DXF.
DXF_MIN_HOLE_AREA_MM2 = float(os.getenv("ARGA_MIN_HOLE_AREA_MM2", "1e-8"))

LAYER_MARK = [
    "MARK",
    "MARKING",
    "ETCH",
    "MARCADO",
    "MARCAJE",
    "GRABADO",
    "SCRIBE",
    "ENGRAVE",
    "IV_MARK",
    "TEXT",
]
LAYER_INNER = ["CUT_INNER", "INNER", "CORTE_INTERNO", "INTERIOR", "IV_INTERIOR"]
LAYER_OUTER = ["CUT_OUTER", "OUTER", "CORTE_EXTERNO", "IV_OUTER", "0"]


def _clasificar_capa(layer_name: str) -> str | None:
    u = str(layer_name or "").upper().strip()
    if any(m in u for m in LAYER_MARK):
        return "mark"
    if ("CUT_INNER" in u) or ("IV_INTERIOR" in u) or (
        "INTERIOR" in u and "OUTER" not in u
    ):
        return "inner"
    if any(x in u for x in LAYER_OUTER):
        return "outer"
    if "CUT" in u:
        return "outer"
    return None


def _segmentos_arco(r_mm: float) -> int:
    """Segmentos para que la cuerda ≤ DXF_CURVE_TOL_MM."""
    r = max(float(r_mm), 1e-9)
    tol = max(DXF_CURVE_TOL_MM, 1e-6)
    if r <= tol:
        return 12
    cos_arg = max(-1.0, min(1.0, 1.0 - tol / r))
    delta = 2.0 * math.acos(cos_arg)
    return max(12, int(math.ceil(2.0 * math.pi / max(delta, 1e-6))))


def _anillo_circulo(cx: float, cy: float, r_mm: float) -> list[tuple[float, float]]:
    n = _segmentos_arco(r_mm)
    pts = []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        pts.append((cx + r_mm * math.cos(a), cy + r_mm * math.sin(a)))
    pts.append(pts[0])
    return pts


def _anillo_arco_ccw(cx, cy, r_mm, start_deg, end_deg) -> list[tuple[float, float]]:
    sa = math.radians(float(start_deg) % 360.0)
    ea = math.radians(float(end_deg) % 360.0)
    sweep = (ea - sa) % (2.0 * math.pi)
    if sweep < 1e-12:
        sweep = 2.0 * math.pi
    arc_len = r_mm * sweep
    n = max(8, int(math.ceil(arc_len / max(DXF_CURVE_TOL_MM, 1e-6))) + 1)
    pts = []
    for i in range(n + 1):
        t = sa + sweep * (i / n)
        pts.append((cx + r_mm * math.cos(t), cy + r_mm * math.sin(t)))
    return pts


def _anillos_cerrados_entidad(entity) -> list[list[tuple[float, float]]]:
    """Anillos cerrados nativos (CIRCLE/ELLIPSE) o a partir de path flattening."""
    typ = entity.dxftype()
    escala = ESCALA_DXF
    out: list[list[tuple[float, float]]] = []

    try:
        if typ == "CIRCLE":
            c = entity.dxf.center
            r = float(entity.dxf.radius) * escala
            if r > 1e-9:
                out.append(
                    _anillo_circulo(
                        float(c.x) * escala, float(c.y) * escala, r
                    )
                )
            return out

        if typ == "ELLIPSE":
            p = path.make_path(entity)
            verts = list(p.flattening(distance=DXF_FLATTEN_DISTANCE))
            if len(verts) >= 3:
                ring = [(v[0] * escala, v[1] * escala) for v in verts]
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                out.append(ring)
            return out

        if typ == "ARC":
            c = entity.dxf.center
            r = float(entity.dxf.radius) * escala
            if r <= 1e-9:
                return out
            cx, cy = float(c.x) * escala, float(c.y) * escala
            pts = _anillo_arco_ccw(
                cx, cy, r, entity.dxf.start_angle, entity.dxf.end_angle
            )
            if len(pts) >= 2:
                out.append(pts)
            return out

        p = path.make_path(entity)
        verts = list(p.flattening(distance=DXF_FLATTEN_DISTANCE))
        if len(verts) < 2:
            return out
        ring = [(v[0] * escala, v[1] * escala) for v in verts]
        if p.is_closed or (
            len(ring) >= 3
            and math.hypot(ring[0][0] - ring[-1][0], ring[0][1] - ring[-1][1])
            < max(0.02, DXF_CURVE_TOL_MM * 2)
        ):
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            if len(ring) >= 4:
                out.append(ring)
    except Exception:
        pass
    return out


def entidad_a_lineas(entity, escala=ESCALA_DXF):
    lineas = []
    for ring in _anillos_cerrados_entidad(entity):
        if len(ring) >= 2:
            lineas.append(LineString(ring))
    if lineas:
        return lineas
    try:
        p = path.make_path(entity)
        vertices = list(p.flattening(distance=DXF_FLATTEN_DISTANCE))
        if len(vertices) > 1:
            v_scaled = [(v[0] * escala, v[1] * escala) for v in vertices]
            lineas.append(LineString(v_scaled))
    except Exception:
        pass
    return lineas


def _anillo_a_poligono(ring) -> Polygon | None:
    if not ring or len(ring) < 4:
        return None
    try:
        poly = Polygon(ring)
        if poly.is_empty or poly.area < DXF_MIN_HOLE_AREA_MM2:
            return None
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area < DXF_MIN_HOLE_AREA_MM2:
            return None
        return poly
    except Exception:
        return None


def _poligonos_cerrados_de_lineas(lineas) -> list[Polygon]:
    if not lineas:
        return []
    candidatos: list[Polygon] = []
    try:
        merged = linemerge(lineas)
        geoms = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        for g in geoms:
            for poly in polygonize([g]):
                if poly.area >= DXF_MIN_HOLE_AREA_MM2:
                    candidatos.append(poly)
    except Exception:
        pass
    if candidatos:
        return candidatos
    try:
        for poly in polygonize(lineas):
            if poly.area >= DXF_MIN_HOLE_AREA_MM2:
                candidatos.append(poly)
    except Exception:
        pass
    if candidatos:
        return candidatos
    # Micro-gaps: cerrar con buffer mínimo sin engordar el contorno.
    try:
        for ln in lineas:
            g = ln.buffer(0.002, join_style=1)
            if g.is_empty:
                continue
            if g.geom_type == "Polygon":
                if g.area >= DXF_MIN_HOLE_AREA_MM2:
                    candidatos.append(g)
            elif hasattr(g, "geoms"):
                for sub in g.geoms:
                    if sub.geom_type == "Polygon" and sub.area >= DXF_MIN_HOLE_AREA_MM2:
                        candidatos.append(sub)
    except Exception:
        pass
    return candidatos


def _hueco_dentro_shell(h: Polygon, shell: Polygon) -> bool:
    if h is None or h.is_empty or shell is None or shell.is_empty:
        return False
    try:
        if h.area >= shell.area * 0.995:
            return False
        pt = h.representative_point()
        if shell.contains(pt):
            return True
        return shell.buffer(0.01).contains(pt)
    except Exception:
        return False


def _recolectar_huecos(shell_poly, candidatos_outer, candidatos_inner, anillos_inner_directos):
    holes: list[Polygon] = []
    seen: set[tuple] = set()

    def _add_poly(h: Polygon):
        if h is None or h.is_empty or h.area < DXF_MIN_HOLE_AREA_MM2:
            return
        if not _hueco_dentro_shell(h, shell_poly):
            return
        try:
            c = h.centroid
            key = (round(c.x, 3), round(c.y, 3), round(h.area, 6))
        except Exception:
            return
        if key in seen:
            return
        seen.add(key)
        holes.append(h)

    for ring in anillos_inner_directos or []:
        _add_poly(_anillo_a_poligono(ring))

    for h in candidatos_inner or []:
        _add_poly(h)

    for h in candidatos_outer or []:
        if h is shell_poly:
            continue
        _add_poly(h)

    return holes


def _filtrar_islas_outer(candidatos_outer, shell_poly: Polygon) -> list[Polygon]:
    """Descarta micro-polígonos de polygonize fallido en el contorno exterior."""
    if not candidatos_outer or shell_poly is None or shell_poly.is_empty:
        return list(candidatos_outer or [])
    shell_a = float(shell_poly.area)
    if shell_a <= 0:
        return list(candidatos_outer)
    min_isla = max(DXF_MIN_HOLE_AREA_MM2 * 100.0, shell_a * 0.001)
    return [p for p in candidatos_outer if p is shell_poly or p.area >= min_isla]


def _ensamblar_pieza(shell_poly: Polygon, holes: list[Polygon]) -> Polygon | None:
    if shell_poly is None or shell_poly.is_empty:
        return None
    hole_coords = []
    for h in holes:
        try:
            hole_coords.append(list(h.exterior.coords))
        except Exception:
            pass
    try:
        pieza = Polygon(shell_poly.exterior.coords, hole_coords)
        if (
            not pieza.is_valid
            or pieza.is_empty
            or pieza.area < shell_poly.area * 0.5
        ):
            raise ValueError("shell+huecos inválido")
    except Exception:
        try:
            pieza = shell_poly
            for h in holes:
                pieza = pieza.difference(h)
        except Exception:
            return shell_poly

    if not pieza.is_valid:
        try:
            from shapely.validation import make_valid

            pieza = make_valid(pieza)
            if pieza.geom_type == "MultiPolygon":
                pieza = max(pieza.geoms, key=lambda g: g.area)
            elif pieza.geom_type == "GeometryCollection":
                polys = [g for g in pieza.geoms if g.geom_type == "Polygon" and not g.is_empty]
                if polys:
                    pieza = max(polys, key=lambda g: g.area)
        except Exception:
            pieza = pieza.buffer(0)

    if pieza is None or pieza.is_empty or pieza.area < 1.0:
        return None
    return pieza


def recuperar_geometria_robusta(ruta_dxf):
    try:
        doc = ezdxf.readfile(ruta_dxf)
        msp = doc.modelspace()
        lines_outer, lines_inner, lines_mark = [], [], []
        anillos_outer_directos: list[list[tuple[float, float]]] = []
        anillos_inner_directos: list[list[tuple[float, float]]] = []

        for entity in msp:
            if entity.dxftype() not in [
                "LINE",
                "LWPOLYLINE",
                "POLYLINE",
                "ARC",
                "CIRCLE",
                "ELLIPSE",
                "SPLINE",
            ]:
                continue

            layer_name = str(entity.dxf.layer).upper().strip()
            clase = _clasificar_capa(layer_name)
            if clase is None:
                continue

            if clase == "mark":
                lines_mark.extend(entidad_a_lineas(entity))
                continue

            anillos = _anillos_cerrados_entidad(entity)
            if clase == "inner":
                anillos_inner_directos.extend(anillos)
                for ring in anillos:
                    if len(ring) >= 2:
                        lines_inner.append(LineString(ring))
                if not anillos:
                    lines_inner.extend(entidad_a_lineas(entity))
            else:
                anillos_outer_directos.extend(anillos)
                for ring in anillos:
                    if len(ring) >= 2:
                        lines_outer.append(LineString(ring))
                if not anillos:
                    lines_outer.extend(entidad_a_lineas(entity))

        if not lines_outer and lines_inner:
            lines_outer = list(lines_inner)
            lines_inner = []
            anillos_inner_directos = []

        if not lines_outer:
            return None, None

        candidatos_outer = _poligonos_cerrados_de_lineas(lines_outer)
        for ring in anillos_outer_directos:
            p = _anillo_a_poligono(ring)
            if p is not None:
                candidatos_outer.append(p)
        if not candidatos_outer:
            return None, None

        shell_poly = max(candidatos_outer, key=lambda x: x.area)
        islas_outer = _filtrar_islas_outer(candidatos_outer, shell_poly)
        candidatos_inner = _poligonos_cerrados_de_lineas(lines_inner)
        holes = _recolectar_huecos(
            shell_poly, islas_outer, candidatos_inner, anillos_inner_directos
        )

        pieza_final = _ensamblar_pieza(shell_poly, holes)
        if pieza_final is None:
            return None, None

        marcas_final = MultiLineString(lines_mark) if lines_mark else MultiLineString()
        return pieza_final, marcas_final

    except Exception as e:
        print(f"Error procesando DXF {ruta_dxf}: {e}")
        return None, None


def reconstruir_poly_seguro(lista_poligonos):
    if not lista_poligonos:
        return None
    outer = lista_poligonos[0]
    holes = lista_poligonos[1:] if len(lista_poligonos) > 1 else []
    try:
        poly = Polygon(outer, holes)
        if not poly.is_valid:
            try:
                from shapely.validation import make_valid

                poly = make_valid(poly)
                if poly.geom_type == "MultiPolygon":
                    poly = max(poly.geoms, key=lambda g: g.area)
            except Exception:
                poly = poly.buffer(0)
        return poly
    except Exception:
        return None


def area_poligonos_colocados(lista_poligonos) -> float:
    """Área neta en mm² de polígonos colocados (exterior + huecos o islas)."""
    if not lista_poligonos:
        return 0.0

    poly = reconstruir_poly_seguro(lista_poligonos)
    if poly is not None and not poly.is_empty:
        area = float(poly.area)
        if area > 0.0:
            ring0 = lista_poligonos[0]
            if ring0 and len(ring0) >= 3 and len(lista_poligonos) > 1:
                xs = [float(pt[0]) for pt in ring0 if isinstance(pt, (list, tuple)) and len(pt) >= 2]
                ys = [float(pt[1]) for pt in ring0 if isinstance(pt, (list, tuple)) and len(pt) >= 2]
                if xs and ys:
                    bbox_a = (max(xs) - min(xs)) * (max(ys) - min(ys))
                    # Anillos extra como islas (no huecos): p. ej. MultiPolygon mal serializado.
                    if bbox_a > 0 and area < bbox_a * 0.55:
                        islas = []
                        for ring in lista_poligonos:
                            try:
                                if not ring or len(ring) < 3:
                                    continue
                                g = Polygon(ring)
                                if not g.is_valid:
                                    g = g.buffer(0)
                                if not g.is_empty:
                                    islas.append(g)
                            except Exception:
                                continue
                        if islas:
                            try:
                                from shapely.ops import unary_union

                                area = float(unary_union(islas).area)
                            except Exception:
                                area = sum(float(g.area) for g in islas)
            if area > 0.0:
                return area

    geoms = []
    for ring in lista_poligonos:
        try:
            if not ring or len(ring) < 3:
                continue
            g = Polygon(ring)
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                geoms.append(g)
        except Exception:
            continue
    if not geoms:
        return 0.0
    try:
        from shapely.ops import unary_union

        return float(unary_union(geoms).area)
    except Exception:
        return sum(float(g.area) for g in geoms)


def reconstruir_marks(lista_coords_marcas):
    if not lista_coords_marcas:
        return MultiLineString()
    lines = [LineString(coords) for coords in lista_coords_marcas]
    return MultiLineString(lines)


def poligonos_desde_shapely(poly):
    """Serializa Polygon shapely → lista de anillos para el visor / export."""
    if poly is None or poly.is_empty:
        return []
    if not poly.is_valid:
        try:
            from shapely.validation import make_valid

            poly = make_valid(poly)
            if poly.geom_type == "MultiPolygon":
                poly = max(poly.geoms, key=lambda g: g.area)
        except Exception:
            poly = poly.buffer(0)
    return [list(poly.exterior.coords)] + [list(h.coords) for h in poly.interiors]


def contar_huecos_poly(poly) -> int:
    if poly is None:
        return 0
    try:
        return len(poly.interiors)
    except Exception:
        return 0


def generar_texto_vectorial(texto, cx, cy, rw, rh):
    """Mantiene Matplotlib nativo para la conversión del texto a vectores"""
    try:
        from matplotlib.textpath import TextPath
        from matplotlib.font_manager import FontProperties

        fp = FontProperties(family="sans-serif", weight="bold")
        tp = TextPath((0, 0), texto, size=10, prop=fp)

        polys = tp.to_polygons()
        if not polys:
            return []

        lineas = []
        all_x, all_y = [], []
        for poly in polys:
            coords = [(float(pt[0]), float(pt[1])) for pt in poly]
            if len(coords) > 1:
                lineas.append(coords)
                all_x.extend([pt[0] for pt in coords])
                all_y.extend([pt[1] for pt in coords])

        if not lineas:
            return []

        minx, maxx = min(all_x), max(all_x)
        miny, maxy = min(all_y), max(all_y)
        text_w = maxx - minx
        text_h = maxy - miny

        if text_h <= 0:
            text_h = 1
        if text_w <= 0:
            text_w = 1

        es_vertical = rh > rw

        if es_vertical:
            target_h = min(40.0, rw * 0.8)
            scale = target_h / text_h
            if text_w * scale > rh * 0.85:
                scale = (rh * 0.85) / text_w
        else:
            target_h = min(40.0, rh * 0.8)
            scale = target_h / text_h
            if text_w * scale > rw * 0.85:
                scale = (rw * 0.85) / text_w

        mid_x = (minx + maxx) / 2
        mid_y = (miny + maxy) / 2

        marcas_finales = []
        for l in lineas:
            trazo = []
            for v in l:
                x = (v[0] - mid_x) * scale
                y = (v[1] - mid_y) * scale
                if es_vertical:
                    x, y = -y, x
                trazo.append((x + cx, y + cy))
            marcas_finales.append(trazo)
        return marcas_finales
    except Exception as e:
        print(f"Error generando texto vectorial: {e}")
        return []
