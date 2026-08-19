import json
import math
import os
import sys

try:
    import ezdxf
    DXF_BACKEND = "ezdxf"
except ImportError:
    try:
        from . import simple_r12_dxf as ezdxf
    except ImportError:
        import simple_r12_dxf as ezdxf
    DXF_BACKEND = "simple_r12_ascii_fallback"

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

# ------------------------------------------------------------
# RESOLUCION ROBUSTA DE RUTAS
# ------------------------------------------------------------
# Este lector puede vivir en cualquiera de estas formas:
#   1) <ROOT>/lector_dxf.py
#   2) <ROOT>/LECTOR DXF/lector_dxf.py
#   3) <ROOT>/dxf/official_lector/lector_dxf.py
#
# Por eso no asumimos una sola estructura. Primero agregamos la carpeta
# actual, luego carpetas historicas y luego el complemento del lector.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_THIS_DIR) if os.path.basename(_THIS_DIR).upper() == "LECTOR DXF" else _THIS_DIR

_CANDIDATE_IMPORT_DIRS = [
    _THIS_DIR,
    os.path.join(_THIS_DIR, "LECTOR DXF"),
    os.path.join(_THIS_DIR, "COMPLEMENTAR LECTOR DXF"),
    os.path.join(_ROOT_DIR, "LECTOR DXF"),
    os.path.join(_ROOT_DIR, "RPA PATHS", "ESCENA"),
    os.path.join(_ROOT_DIR, "LECTOR DXF", "COMPLEMENTAR LECTOR DXF"),
]

for _p in reversed(_CANDIDATE_IMPORT_DIRS):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
from json_source import JSON_OUTPUT_SUBDIR, resolve_output_folder, write_last_json_pointer
from scene_resolver import (
    DEFAULT_CAMA,
    DEFAULT_ESCENAS_ROOT,
    DEFAULT_LASER_LINE,
    PIPELINE_LASER_LINES,
    plate_inches_from_mm,
    resolve_scene_from_sheet,
    scene_size_key_from_plate,
)
from placement_transform import (
    JSON_COORD_DECIMALS,
    build_meta_placement,
    round_contour_coords,
    select_primary_plate_contour,
)
from path_geometry import filter_cut_outer_exclude_sheet_plate

# Carpeta inicial al abrir el selector de archivos (python lector_dxf.py).
DEFAULT_DXF_INITIAL_DIR = os.path.join(os.path.expanduser("~"), "Desktop")

# ============================================================
# CONFIGURACION DE BASE DE DATOS
# ============================================================
# Hosts a probar en orden.
# Puedes sobrescribirlos con la variable de entorno NESTING_DB_HOST,
# por ejemplo:
#   NESTING_DB_HOST=localhost,192.168.2.84,192.168.2.52
_DEFAULT_DB_HOSTS = "localhost,192.168.2.80,192.168.2.52"
DB_HOSTS = [
    h.strip()
    for h in os.getenv("NESTING_DB_HOST", _DEFAULT_DB_HOSTS).split(",")
    if h.strip()
]

DB_PORT = int(os.getenv("NESTING_DB_PORT", "5433"))
DB_NAME = os.getenv("NESTING_DB_NAME", "nestingpro_db")
DB_USER = os.getenv("NESTING_DB_USER", "postgres")
DB_PASSWORD = os.getenv("NESTING_DB_PASSWORD", "nesting123")
DB_CONNECT_TIMEOUT = int(os.getenv("NESTING_DB_CONNECT_TIMEOUT", "10"))

DB_SCHEMA = "public"
DB_TABLE_PQART_SWO = "pqart_swo"
DB_TARGET_TIPO_CORTE = os.getenv("NESTING_DB_TIPO_CORTE", "RobotLaser")
DB_STATUS_PROCESADO = "procesado"

# Si tu DXF viene en pulgadas, cambia a 25.4
SCALE = 1.0

# Margen para aceptar CUT_INNER dentro del área real
INNER_ACCEPT_MARGIN = 50.0  # mm

# Tolerancia para unir extremos de entidades abiertas
JOIN_TOL = 0.5  # mm

# Resolución de discretización
CIRCLE_SEGMENTS = 72
ARC_SEGMENTS = 36
CURVE_FLATTEN_TOL = 1.0  # mm

# ============================================================
# CONFIGURACION DE FILTRADO
# ============================================================
# True  = filtra CUT_INNER usando un bbox de referencia
# False = conserva TODO lo que venga en CUT_INNER
FILTER_CUT_INNER = False

# Modos:
# "CUT_OUTER_ONLY" = usa solo CUT_OUTER
# "CUT_OUTER_MARK" = usa CUT_OUTER + MARK
# "FULL_DXF"       = usa todo el contenido del DXF
# "NONE"           = no calcula bbox de referencia
REF_BBOX_MODE = "CUT_OUTER_ONLY"

# Si True, usa solo el CUT_OUTER cerrado de mayor area como referencia
USE_ONLY_LARGEST_CUT_OUTER = True

# ============================================================
# AJUSTES IMPORTANTES PARA MARCAJE
# ============================================================
# No stitch global para MARK
STITCH_MARK = False

# Solo considerar "auto cerrado por geometría" si hay al menos 3 puntos
AUTO_CLOSE_MIN_POINTS = 3

# ------------------------------------------------------------
# LIMPIEZA EXCLUSIVA DE LINEAS EXTRA EN MARK
# ------------------------------------------------------------
MARK_REMOVE_EXTRA_LINES = True

# Tolerancia para considerar dos puntos iguales en MARK
MARK_COORD_TOL = 0.05  # mm

# Solape mínimo para considerar que hay duplicado real
MARK_MIN_OVERLAP_MM = 0.5  # mm

# Qué porcentaje del tramo más corto debe estar solapado
# para considerar que son prácticamente la misma línea
MARK_OVERLAP_RATIO = 0.98

# Eliminar micro-segmentos de MARK por debajo de la propia
# tolerancia geométrica del sistema
MARK_REMOVE_TINY_LINES = True
MARK_MIN_USEFUL_LINE_LENGTH = MARK_COORD_TOL


# ============================================================
# FUNCIONES NUEVAS: BASE DE DATOS / RESOLUCION DEL DXF
# ============================================================
def make_json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def sanitize_db_row(row):
    if not row:
        return None
    return {k: make_json_safe(v) for k, v in row.items()}


def normalize_db_path(path_value):
    if path_value is None:
        return None

    path_str = str(path_value).strip().strip('"').strip("'")
    if not path_str:
        return None

    return os.path.normpath(path_str)


def get_db_connection():
    if psycopg2 is None:
        raise RuntimeError(
            "psycopg2 no está instalado. La conexión a base de datos queda desactivada "
            "en este paquete de prueba. Para activarla después instala psycopg2-binary."
        )

    """
    Intenta conectarse a PostgreSQL probando varios hosts en orden.

    Regresa:
        (conn, host_usado)

    Esto evita que el lector dependa únicamente de una IP fija.
    Si la BD está local en Docker, primero probará localhost;
    si no, probará las IP configuradas en DB_HOSTS.
    """
    errors = []

    for host in DB_HOSTS:
        try:
            conn = psycopg2.connect(
                host=host,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                connect_timeout=DB_CONNECT_TIMEOUT,
            )
            return conn, host
        except Exception as exc:
            errors.append(f"{host}:{DB_PORT} -> {exc}")

    raise ConnectionError(
        "No se pudo conectar a nestingpro_db. Hosts probados: "
        + ", ".join(DB_HOSTS)
        + "\n"
        + "\n".join(errors)
        + "\nSugerencia: usa NESTING_DB_HOST=localhost si Postgres corre en Docker aquí, "
        + "o verifica el puerto con NESTING_DB_PORT."
    )


def fetch_next_robotlaser_row():
    query = f"""
        SELECT
            id,
            nombre_swo,
            nombre_dxf,
            ruta,
            tipo_corte,
            ls,
            sheet_uid,
            sheet_code,
            sheet_display_name,
            created_at,
            updated_at,
            origen_proceso_pqart
        FROM {DB_SCHEMA}.{DB_TABLE_PQART_SWO}
        WHERE LOWER(BTRIM(tipo_corte)) = LOWER(BTRIM(%s))
          AND COALESCE(LOWER(BTRIM(ls)), '') <> %s
        ORDER BY id ASC
        LIMIT 1;
    """

    conn, host_used = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (DB_TARGET_TIPO_CORTE, DB_STATUS_PROCESADO))
            row = cur.fetchone()
            if not row:
                return None

            out = sanitize_db_row(dict(row))
            if out is not None:
                out["_db_host"] = host_used
            return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_next_dxf_from_database():
    row = fetch_next_robotlaser_row()

    if not row:
        raise RuntimeError(
            f"No se encontró ningún registro pendiente en "
            f"{DB_SCHEMA}.{DB_TABLE_PQART_SWO} con tipo_corte='{DB_TARGET_TIPO_CORTE}'."
        )

    dxf_path = normalize_db_path(row.get("ruta"))
    row_id = row.get("id")

    if not dxf_path:
        raise RuntimeError(
            f"El registro id={row_id} no contiene una ruta válida en la columna 'ruta'."
        )

    if not os.path.exists(dxf_path):
        raise FileNotFoundError(
            f"La ruta del DXF del registro id={row_id} no existe físicamente: {dxf_path}"
        )

    row["ruta"] = dxf_path

    return {
        "dxf_file": dxf_path,
        "db_row": row,
    }


def ensure_dir(path_: str) -> None:
    if not os.path.isdir(path_):
        os.makedirs(path_)


def dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def same_pt(a, b, tol=JOIN_TOL) -> bool:
    return dist(a, b) <= tol


def clean_points(pts, tol=1e-9):
    if not pts:
        return []

    out = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
            out.append(p)
    return out


def append_joined_points(dst, src, tol=JOIN_TOL):
    if not src:
        return dst

    if not dst:
        dst.extend(src)
        return dst

    if same_pt(dst[-1], src[0], tol):
        dst.extend(src[1:])
    elif same_pt(dst[-1], src[-1], tol):
        dst.extend(list(reversed(src[:-1])))
    else:
        dst.extend(src)

    return dst


def normalize_layer_name(name):
    s = (name or "").strip().upper()
    s = s.replace(" ", "").replace("-", "_")

    aliases = {
        "CUT_OUTER": "CUT_OUTER",
        "CUTOUTER": "CUT_OUTER",
        "CUT_OUT": "CUT_OUTER",
        "OUTER": "CUT_OUTER",

        "CUT_INNER": "CUT_INNER",
        "CUTINNER": "CUT_INNER",
        "CUT_IN": "CUT_INNER",
        "INNER": "CUT_INNER",

        "MARK": "MARK",
        "MARCADO": "MARK",
        "MARCAJE": "MARK",

        "PLATE": "PLATE",
        "PLACA": "PLATE",
        "SHEET": "PLATE",
    }
    return aliases.get(s, s)


def vec_to_xy(v):
    if hasattr(v, "x") and hasattr(v, "y"):
        return [float(v.x) * SCALE, float(v.y) * SCALE]
    return [float(v[0]) * SCALE, float(v[1]) * SCALE]


def flatten_entity(entity):
    pts = []
    try:
        for v in entity.flattening(CURVE_FLATTEN_TOL):
            pts.append(vec_to_xy(v))
    except Exception:
        return []
    return clean_points(pts)


def arc_points_from_entity(entity):
    pts = []

    c = entity.dxf.center
    r = float(entity.dxf.radius) * SCALE
    a0 = math.radians(float(entity.dxf.start_angle))
    a1 = math.radians(float(entity.dxf.end_angle))

    if a1 < a0:
        a1 += 2.0 * math.pi

    sweep = abs(a1 - a0)
    frac = sweep / (2.0 * math.pi)
    segs = max(8, int(math.ceil(CIRCLE_SEGMENTS * frac)))

    for i in range(segs + 1):
        a = a0 + (a1 - a0) * i / segs
        x = float(c.x) * SCALE + r * math.cos(a)
        y = float(c.y) * SCALE + r * math.sin(a)
        pts.append([x, y])

    return clean_points(pts)


def line_points_from_entity(entity):
    s = entity.dxf.start
    e = entity.dxf.end
    return clean_points([
        [float(s.x) * SCALE, float(s.y) * SCALE],
        [float(e.x) * SCALE, float(e.y) * SCALE]
    ])


def circle_points_from_entity(entity):
    pts = []
    c = entity.dxf.center
    r = float(entity.dxf.radius) * SCALE

    for i in range(CIRCLE_SEGMENTS + 1):
        a = 2.0 * math.pi * i / CIRCLE_SEGMENTS
        x = float(c.x) * SCALE + r * math.cos(a)
        y = float(c.y) * SCALE + r * math.sin(a)
        pts.append([x, y])

    return clean_points(pts)



def _round_native_point(point):
    return [round(float(point[0]), JSON_COORD_DECIMALS), round(float(point[1]), JSON_COORD_DECIMALS)]


def _arc_sweep_ccw_deg(start_deg, end_deg):
    sweep = (float(end_deg) - float(start_deg)) % 360.0
    return 360.0 if abs(sweep) <= 1e-9 else sweep


def native_segment_from_entity(entity):
    """Conserva la primitiva DXF antes de discretizarla a puntos."""
    t = entity.dxftype()
    layer = normalize_layer_name(entity.dxf.layer if hasattr(entity.dxf, "layer") else "")
    if t == "LINE":
        start = vec_to_xy(entity.dxf.start)
        end = vec_to_xy(entity.dxf.end)
        return {
            "type": "line",
            "source_etype": "LINE",
            "layer": layer,
            "start_dxf": _round_native_point(start),
            "end_dxf": _round_native_point(end),
        }
    if t == "ARC":
        c = entity.dxf.center
        cx = float(c.x) * SCALE
        cy = float(c.y) * SCALE
        radius = float(entity.dxf.radius) * SCALE
        start_angle = float(entity.dxf.start_angle)
        end_angle = float(entity.dxf.end_angle)
        start_rad = math.radians(start_angle)
        end_rad = math.radians(end_angle)
        start = [cx + radius * math.cos(start_rad), cy + radius * math.sin(start_rad)]
        end = [cx + radius * math.cos(end_rad), cy + radius * math.sin(end_rad)]
        return {
            "type": "arc",
            "source_etype": "ARC",
            "layer": layer,
            "center_dxf": _round_native_point([cx, cy]),
            "radius_mm": round(radius, JSON_COORD_DECIMALS),
            "start_angle_deg": round(start_angle, 6),
            "end_angle_deg": round(end_angle, 6),
            "direction": "ccw",
            "sweep_deg": round(_arc_sweep_ccw_deg(start_angle, end_angle), 6),
            "start_dxf": _round_native_point(start),
            "end_dxf": _round_native_point(end),
        }
    if t == "CIRCLE":
        c = entity.dxf.center
        return {
            "type": "circle",
            "source_etype": "CIRCLE",
            "layer": layer,
            "center_dxf": _round_native_point([float(c.x) * SCALE, float(c.y) * SCALE]),
            "radius_mm": round(float(entity.dxf.radius) * SCALE, JSON_COORD_DECIMALS),
        }
    return None


def _reverse_native_segment(segment):
    seg = dict(segment or {})
    if not seg:
        return seg
    start = seg.get("start_dxf")
    end = seg.get("end_dxf")
    if start is not None or end is not None:
        seg["start_dxf"], seg["end_dxf"] = end, start
    if seg.get("type") == "arc":
        seg["start_angle_deg"], seg["end_angle_deg"] = seg.get("end_angle_deg"), seg.get("start_angle_deg")
        seg["direction"] = "cw" if str(seg.get("direction") or "ccw").lower() == "ccw" else "ccw"
    return seg


def _reverse_native_segments(segments):
    return [_reverse_native_segment(seg) for seg in reversed(list(segments or []))]


def _append_native_segments(target, segments, reverse=False):
    source = _reverse_native_segments(segments) if reverse else [dict(s) for s in (segments or [])]
    target.extend(source)


def subentity_to_points(entity):
    t = entity.dxftype()

    if t == "LINE":
        return line_points_from_entity(entity)

    elif t == "ARC":
        return arc_points_from_entity(entity)

    elif t == "CIRCLE":
        return circle_points_from_entity(entity)

    elif t in ("SPLINE", "ELLIPSE"):
        return flatten_entity(entity)

    return []


def polyline_to_points_via_virtual_entities(entity):
    pts = []

    try:
        virtuals = list(entity.virtual_entities())
    except Exception:
        return []

    for sub in virtuals:
        sub_pts = subentity_to_points(sub)
        sub_pts = clean_points(sub_pts)
        if len(sub_pts) >= 2:
            append_joined_points(pts, sub_pts, JOIN_TOL)

    return clean_points(pts)


def entity_to_contour(entity):
    """
    Regresa:
    {
        "points": [[x,y], ...],
        "closed": bool,
        "etype": "LINE"/"ARC"/...
    }
    """
    pts = []
    closed = False
    t = entity.dxftype()

    if t == "LWPOLYLINE":
        pts = polyline_to_points_via_virtual_entities(entity)

        if not pts:
            for p in entity.get_points():
                pts.append([float(p[0]) * SCALE, float(p[1]) * SCALE])

        closed = bool(entity.closed)

    elif t == "POLYLINE":
        pts = polyline_to_points_via_virtual_entities(entity)

        if not pts:
            try:
                for v in entity.vertices:
                    loc = v.dxf.location
                    pts.append([float(loc.x) * SCALE, float(loc.y) * SCALE])
            except Exception:
                return None

        try:
            closed = bool(entity.is_closed)
        except Exception:
            closed = False

    elif t == "LINE":
        pts = line_points_from_entity(entity)
        closed = False

    elif t == "CIRCLE":
        pts = circle_points_from_entity(entity)
        closed = True

    elif t == "ARC":
        pts = arc_points_from_entity(entity)
        closed = False

    elif t in ("SPLINE", "ELLIPSE"):
        pts = flatten_entity(entity)
        closed = False

    else:
        return None

    pts = clean_points(pts)
    if len(pts) < 2:
        return None

    # Solo auto-cerrar si realmente parece contorno, no una línea de 2 puntos
    if (not closed) and len(pts) >= AUTO_CLOSE_MIN_POINTS and same_pt(pts[0], pts[-1], JOIN_TOL):
        closed = True

    if closed and not same_pt(pts[0], pts[-1], 1e-9):
        pts.append(pts[0])

    native = native_segment_from_entity(entity)
    result = {
        "points": pts,
        "closed": closed,
        "etype": t
    }
    if native is not None:
        result["native_segments"] = [native]
        result["native_geometry_preserved"] = True
    return result


def bbox_of_points(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_of_contours(contours):
    xs = []
    ys = []
    for c in contours:
        pts = c["points"] if isinstance(c, dict) else c
        for p in pts:
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def center_of_bbox(bb):
    return ((bb[0] + bb[2]) * 0.5, (bb[1] + bb[3]) * 0.5)


def inside_bbox(x, y, bb, margin):
    return (
        x >= bb[0] - margin and x <= bb[2] + margin and
        y >= bb[1] - margin and y <= bb[3] + margin
    )


def polygon_area(points):
    """
    Area absoluta por shoelace.
    Requiere contorno cerrado o casi cerrado.
    """
    if not points or len(points) < 3:
        return 0.0

    pts = points[:]
    if not same_pt(pts[0], pts[-1], 1e-9):
        pts.append(pts[0])

    area2 = 0.0
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        area2 += (x1 * y2) - (x2 * y1)

    return abs(area2) * 0.5


def largest_closed_contour(contours):
    best = None
    best_area = -1.0

    for c in contours:
        if not c.get("closed", False):
            continue
        area = polygon_area(c["points"])
        if area > best_area:
            best_area = area
            best = c

    return best


def stitch_open_contours(contours, tol=JOIN_TOL):
    """Une cadenas abiertas preservando el orden de primitivas LINE/ARC.

    `points` sigue siendo la representación compatible y de respaldo. Cuando
    todas las entidades conservan `native_segments`, también se entrega una
    cadena orientada con las mismas inversiones realizadas durante el stitch.
    """
    closed_items = []
    open_items = []

    for c in contours:
        pts = clean_points(c["points"])
        is_closed = bool(c.get("closed", False))
        native_segments = [dict(seg) for seg in (c.get("native_segments") or [])]

        if len(pts) < 2:
            continue

        base = {
            "points": pts,
            "closed": is_closed,
            "etype": c.get("etype", "UNKNOWN"),
            "native_segments": native_segments,
            "native_geometry_preserved": bool(native_segments),
            "source_etypes": [c.get("etype", "UNKNOWN")],
        }

        if is_closed or (len(pts) >= AUTO_CLOSE_MIN_POINTS and same_pt(pts[0], pts[-1], tol)):
            if not same_pt(pts[0], pts[-1], 1e-9):
                pts.append(pts[0])
            base["points"] = pts
            base["closed"] = True
            base["native_segment_count"] = len(native_segments)
            closed_items.append(base)
        else:
            open_items.append(base)

    used = [False] * len(open_items)
    merged = []

    for i in range(len(open_items)):
        if used[i]:
            continue

        chain = list(open_items[i]["points"])
        chain_segments = [dict(seg) for seg in open_items[i].get("native_segments") or []]
        source_etypes = list(open_items[i].get("source_etypes") or [])
        native_complete = bool(chain_segments)
        used[i] = True

        changed = True
        while changed:
            changed = False

            for j in range(len(open_items)):
                if used[j]:
                    continue

                item = open_items[j]
                pts = item["points"]
                segs = item.get("native_segments") or []
                a0 = chain[0]
                a1 = chain[-1]
                b0 = pts[0]
                b1 = pts[-1]

                if same_pt(a1, b0, tol):
                    chain.extend(pts[1:])
                    _append_native_segments(chain_segments, segs, reverse=False)
                elif same_pt(a1, b1, tol):
                    chain.extend(list(reversed(pts[:-1])))
                    _append_native_segments(chain_segments, segs, reverse=True)
                elif same_pt(a0, b1, tol):
                    chain = pts[:-1] + chain
                    chain_segments = [dict(seg) for seg in segs] + chain_segments
                elif same_pt(a0, b0, tol):
                    chain = list(reversed(pts[1:])) + chain
                    chain_segments = _reverse_native_segments(segs) + chain_segments
                else:
                    continue

                native_complete = native_complete and bool(segs)
                source_etypes.extend(item.get("source_etypes") or [])
                used[j] = True
                changed = True
                break

        chain = clean_points(chain)
        chain_closed = len(chain) >= AUTO_CLOSE_MIN_POINTS and same_pt(chain[0], chain[-1], tol)
        if chain_closed and not same_pt(chain[0], chain[-1], 1e-9):
            chain.append(chain[0])

        item = {
            "points": chain,
            "closed": chain_closed,
            "etype": "STITCHED",
            "source_etypes": source_etypes,
        }
        if native_complete and chain_segments:
            item["native_segments"] = chain_segments
            item["native_segment_count"] = len(chain_segments)
            item["native_geometry_preserved"] = True
        else:
            item["native_geometry_preserved"] = False
        merged.append(item)

    return closed_items + merged


def normalize_contours_no_stitch(contours):
    """
    Para MARK:
    - no unir entidades entre sí
    - no inventar cierres raros
    - respetar solamente lo que ya viene limpio
    """
    out = []

    for c in contours:
        pts = clean_points(c["points"])
        if len(pts) < 2:
            continue

        is_closed = bool(c.get("closed", False))

        if is_closed and not same_pt(pts[0], pts[-1], 1e-9):
            pts.append(pts[0])

        out.append({
            "points": pts,
            "closed": is_closed,
            "etype": c.get("etype", "UNKNOWN")
        })

    return out


def mark_is_simple_line(contour):
    """
    Solo limpiar agresivamente trazos simples de 2 puntos.
    No tocar polilíneas ni contornos complejos.
    """
    pts = contour.get("points", [])
    return (not contour.get("closed", False)) and len(pts) == 2


def mark_line_length(contour):
    pts = contour["points"]
    return dist(pts[0], pts[1])


def mark_quantized_point(p, tol):
    """
    Cuantiza el punto para comparar con tolerancia sin depender
    de decimales flotantes exactos.
    """
    if tol <= 0:
        return (p[0], p[1])
    return (int(round(p[0] / tol)), int(round(p[1] / tol)))


def mark_exact_line_key(contour, tol):
    """
    Regresa una llave única para detectar duplicados exactos,
    aunque la línea venga invertida.
    """
    p0, p1 = contour["points"][0], contour["points"][1]
    a = mark_quantized_point(p0, tol)
    b = mark_quantized_point(p1, tol)
    return tuple(sorted((a, b)))


def mark_almost_duplicate_line(a, b, coord_tol, min_overlap_mm, overlap_ratio):
    """
    Detecta si dos líneas de MARK son casi la misma:
    - paralelas
    - colineales
    - con solape casi total del tramo más corto
    """
    if not mark_is_simple_line(a) or not mark_is_simple_line(b):
        return False, 0.0

    p0, p1 = a["points"][0], a["points"][1]
    q0, q1 = b["points"][0], b["points"][1]

    ax = p1[0] - p0[0]
    ay = p1[1] - p0[1]
    bx = q1[0] - q0[0]
    by = q1[1] - q0[1]

    la = math.hypot(ax, ay)
    lb = math.hypot(bx, by)

    if la <= coord_tol or lb <= coord_tol:
        return False, 0.0

    uax = ax / la
    uay = ay / la
    ubx = bx / lb
    uby = by / lb

    # Paralelismo
    parallel_cross = abs(uax * uby - uay * ubx)
    if parallel_cross > 1e-3:
        return False, 0.0

    def point_line_distance(pt, line_p0, ux, uy):
        vx = pt[0] - line_p0[0]
        vy = pt[1] - line_p0[1]
        return abs(vx * uy - vy * ux)

    # Colinealidad: los extremos de B deben caer cerca de la recta de A
    if point_line_distance(q0, p0, uax, uay) > coord_tol:
        return False, 0.0
    if point_line_distance(q1, p0, uax, uay) > coord_tol:
        return False, 0.0

    def proj_interval(r0, r1, base, ux, uy):
        t0 = (r0[0] - base[0]) * ux + (r0[1] - base[1]) * uy
        t1 = (r1[0] - base[0]) * ux + (r1[1] - base[1]) * uy
        return (min(t0, t1), max(t0, t1))

    ia0, ia1 = proj_interval(p0, p1, p0, uax, uay)
    ib0, ib1 = proj_interval(q0, q1, p0, uax, uay)

    overlap = min(ia1, ib1) - max(ia0, ib0)
    if overlap < min_overlap_mm:
        return False, max(0.0, overlap)

    shorter = min(la, lb)

    # Solo borrar si prácticamente toda la línea corta está montada
    if overlap + coord_tol < shorter * overlap_ratio:
        return False, overlap

    return True, overlap


def remove_extra_mark_lines(contours, coord_tol, min_overlap_mm, overlap_ratio):
    """
    Limpieza exclusiva para MARK:
    1) elimina duplicados exactos/invertidos
    2) elimina líneas casi idénticas por solape colineal
    """
    base = normalize_contours_no_stitch(contours)

    stats = {
        "removed_exact_duplicates": 0,
        "removed_overlap_duplicates": 0
    }

    kept = []
    seen_exact = set()

    # 1) Eliminar duplicados exactos
    for c in base:
        if not mark_is_simple_line(c):
            kept.append(c)
            continue

        key = mark_exact_line_key(c, coord_tol)
        if key in seen_exact:
            stats["removed_exact_duplicates"] += 1
            continue

        seen_exact.add(key)
        kept.append(c)

    # 2) Eliminar duplicados por solape casi total
    removed = [False] * len(kept)

    for i in range(len(kept)):
        if removed[i] or (not mark_is_simple_line(kept[i])):
            continue

        for j in range(i + 1, len(kept)):
            if removed[j] or (not mark_is_simple_line(kept[j])):
                continue

            is_dup, overlap = mark_almost_duplicate_line(
                kept[i], kept[j],
                coord_tol=coord_tol,
                min_overlap_mm=min_overlap_mm,
                overlap_ratio=overlap_ratio
            )

            if not is_dup:
                continue

            li = mark_line_length(kept[i])
            lj = mark_line_length(kept[j])

            # conservar la más larga; si son casi iguales, conservar la primera
            if lj > li + coord_tol:
                removed[i] = True
                stats["removed_overlap_duplicates"] += 1
                break
            else:
                removed[j] = True
                stats["removed_overlap_duplicates"] += 1

    final_out = [c for idx, c in enumerate(kept) if not removed[idx]]
    return final_out, stats


def remove_tiny_mark_lines(contours, min_length):
    """
    Elimina únicamente líneas simples de MARK cuya longitud
    sea menor o igual al umbral mínimo útil.

    Esto NO toca polilíneas complejas ni contornos cerrados.
    """
    out = []
    removed_count = 0

    for c in contours:
        if mark_is_simple_line(c):
            length = mark_line_length(c)
            if length <= min_length:
                removed_count += 1
                continue

        out.append(c)

    return out, removed_count


def build_reference_bbox(raw_data, mode, use_only_largest_outer):
    if mode == "NONE":
        return None

    if mode == "FULL_DXF":
        all_contours = raw_data["CUT_OUTER"] + raw_data["CUT_INNER"] + raw_data["MARK"]
        return bbox_of_contours(all_contours)

    if mode == "CUT_OUTER_ONLY":
        if use_only_largest_outer:
            outer = largest_closed_contour(raw_data["CUT_OUTER"])
            if outer:
                return bbox_of_contours([outer])
        return bbox_of_contours(raw_data["CUT_OUTER"])

    if mode == "CUT_OUTER_MARK":
        if use_only_largest_outer:
            outer = largest_closed_contour(raw_data["CUT_OUTER"])
            if outer:
                return bbox_of_contours([outer] + raw_data["MARK"])
        return bbox_of_contours(raw_data["CUT_OUTER"] + raw_data["MARK"])

    return None


def build_sheet_bbox(raw_data):
    """
    Bounding box de la placa en coordenadas DXF (mm).

    Prioridad:
    1) Layer PLATE (contorno de lámina del nesting / PDF)
    2) Fallback: CUT_OUTER cerrado de mayor área
    """
    plate_contours = raw_data.get("PLATE") or []
    if plate_contours:
        primary_plate, _ = select_primary_plate_contour(plate_contours)
        if primary_plate:
            bb = bbox_of_contours([primary_plate])
            if bb:
                return bb, "PLATE"

    # Sin layer PLATE no se debe confundir la pieza exterior mas grande con la placa.
    # Se usa el bbox de toda la geometria exterior solo como estimacion demostrativa.
    bb = bbox_of_contours(raw_data["CUT_OUTER"])
    if bb:
        return bb, "GEOMETRY_BBOX_ESTIMATE"

    return None, None


def sheet_info_from_bbox(bb, source=None, db_row=None):
    if not bb:
        return None

    min_x, min_y, max_x, max_y = bb
    width_mm = round(max_x - min_x, 3)
    height_mm = round(max_y - min_y, 3)
    plate_dims = plate_inches_from_mm(width_mm, height_mm)
    try:
        size_key, width_code, _ = scene_size_key_from_plate(width_mm, height_mm)
    except ValueError:
        # Un bbox estimado sin PLATE puede no coincidir con una medida comercial.
        # No debe detener la lectura; el clasificador asignara la envolvente V3.
        size_key, width_code = None, None

    info = {
        "source_layer": source,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "width_in": plate_dims["width_in"],
        "height_in": plate_dims["height_in"],
        "plate_size_in": "{0}x{1}".format(
            int(plate_dims["width_in"]), int(plate_dims["height_in"])
        ),
        "scene_size_key": size_key,
        "scene_width_code_in": width_code,
        "origin_x_mm": round(min_x, 3),
        "origin_y_mm": round(min_y, 3),
        "bbox": [min_x, min_y, max_x, max_y],
    }

    if db_row:
        sheet_uid = db_row.get("sheet_uid")
        sheet_code = db_row.get("sheet_code")
        sheet_display_name = db_row.get("sheet_display_name")
        if sheet_uid:
            info["uid"] = sheet_uid
        if sheet_code:
            info["code"] = sheet_code
        if sheet_display_name:
            info["display_name"] = sheet_display_name

    return info


def process_dxf(dxf_file, output_folder=None, db_row=None):
    if not dxf_file:
        raise ValueError("No se recibió ruta de DXF.")
    if not os.path.exists(dxf_file):
        raise FileNotFoundError(f"No existe el archivo DXF: {dxf_file}")

    output_folder = resolve_output_folder(dxf_file, output_folder)

    dxf_name = os.path.splitext(os.path.basename(dxf_file))[0]
    output_file = os.path.join(output_folder, f"{dxf_name}.json")

    doc = ezdxf.readfile(dxf_file)
    msp = doc.modelspace()

    raw = {
        "PLATE": [],
        "CUT_OUTER": [],
        "CUT_INNER": [],
        "MARK": []
    }

    ignored = []

    for e in msp:
        layer = normalize_layer_name(e.dxf.layer if hasattr(e.dxf, "layer") else "")
        if layer not in raw:
            continue

        c = entity_to_contour(e)
        if c is None:
            ignored.append((e.dxftype(), layer))
            continue

        raw[layer].append(c)

    # ============================================================
    # PROCESAMIENTO POR LAYER
    # ============================================================
    raw["CUT_OUTER"] = stitch_open_contours(raw["CUT_OUTER"], tol=JOIN_TOL)
    raw["CUT_INNER"] = stitch_open_contours(raw["CUT_INNER"], tol=JOIN_TOL)

    # MARK no se stitcha globalmente, pero sí se limpia de líneas extra
    mark_cleanup_stats = {
        "removed_exact_duplicates": 0,
        "removed_overlap_duplicates": 0,
        "removed_tiny_lines": 0
    }

    if STITCH_MARK:
        raw["MARK"] = stitch_open_contours(raw["MARK"], tol=JOIN_TOL)
    else:
        raw["MARK"] = normalize_contours_no_stitch(raw["MARK"])

    if MARK_REMOVE_EXTRA_LINES:
        raw["MARK"], mark_cleanup_stats_tmp = remove_extra_mark_lines(
            raw["MARK"],
            coord_tol=MARK_COORD_TOL,
            min_overlap_mm=MARK_MIN_OVERLAP_MM,
            overlap_ratio=MARK_OVERLAP_RATIO
        )
        mark_cleanup_stats["removed_exact_duplicates"] = mark_cleanup_stats_tmp["removed_exact_duplicates"]
        mark_cleanup_stats["removed_overlap_duplicates"] = mark_cleanup_stats_tmp["removed_overlap_duplicates"]

    if MARK_REMOVE_TINY_LINES:
        raw["MARK"], removed_tiny = remove_tiny_mark_lines(
            raw["MARK"],
            min_length=MARK_MIN_USEFUL_LINE_LENGTH
        )
        mark_cleanup_stats["removed_tiny_lines"] = removed_tiny

    # bbox de referencia configurable
    ref_bb = build_reference_bbox(raw, REF_BBOX_MODE, USE_ONLY_LARGEST_CUT_OUTER)
    sheet_bb, sheet_source = build_sheet_bbox(raw)
    sheet_info = sheet_info_from_bbox(sheet_bb, source=sheet_source, db_row=db_row)
    plate_contour_count = len(raw.get("PLATE") or [])
    placement_info = build_meta_placement(sheet_info, ref_bb, plate_contour_count)

    scenes_info = {}
    if sheet_info:
        for laser_line in PIPELINE_LASER_LINES:
            try:
                scenes_info[laser_line] = resolve_scene_from_sheet(
                    sheet_info,
                    escenas_root=DEFAULT_ESCENAS_ROOT,
                    laser_line=laser_line,
                    cama=DEFAULT_CAMA,
                )
            except Exception as exc:
                scenes_info[laser_line] = {
                    "error": str(exc),
                    "laser_line": laser_line,
                    "cama": DEFAULT_CAMA,
                    "escenas_root": DEFAULT_ESCENAS_ROOT,
                }

    scene_info = scenes_info.get(DEFAULT_LASER_LINE)

    # Filtrar CUT_INNER
    filtered_inner = []
    removed = 0

    if FILTER_CUT_INNER and ref_bb:
        for c in raw["CUT_INNER"]:
            bb = bbox_of_points(c["points"])
            if not bb:
                continue

            cx, cy = center_of_bbox(bb)
            if inside_bbox(cx, cy, ref_bb, INNER_ACCEPT_MARGIN):
                filtered_inner.append(c)
            else:
                removed += 1
    else:
        filtered_inner = raw["CUT_INNER"]

    meta = {
        "source_dxf": dxf_file,
        "scale": SCALE,
        "inner_accept_margin": INNER_ACCEPT_MARGIN,
        "join_tol": JOIN_TOL,
        "circle_segments": CIRCLE_SEGMENTS,
        "arc_segments": ARC_SEGMENTS,
        "curve_flatten_tol": CURVE_FLATTEN_TOL,
        "filter_cut_inner": FILTER_CUT_INNER,
        "ref_bbox_mode": REF_BBOX_MODE,
        "use_only_largest_cut_outer": USE_ONLY_LARGEST_CUT_OUTER,
        "stitch_mark": STITCH_MARK,
        "auto_close_min_points": AUTO_CLOSE_MIN_POINTS,
        "mark_remove_extra_lines": MARK_REMOVE_EXTRA_LINES,
        "mark_coord_tol": MARK_COORD_TOL,
        "mark_min_overlap_mm": MARK_MIN_OVERLAP_MM,
        "mark_overlap_ratio": MARK_OVERLAP_RATIO,
        "mark_remove_tiny_lines": MARK_REMOVE_TINY_LINES,
        "mark_min_useful_line_length": MARK_MIN_USEFUL_LINE_LENGTH,
        "mark_removed_exact_duplicates": mark_cleanup_stats["removed_exact_duplicates"],
        "mark_removed_overlap_duplicates": mark_cleanup_stats["removed_overlap_duplicates"],
        "mark_removed_tiny_lines": mark_cleanup_stats["removed_tiny_lines"],
        "removed_inner": removed,
        "ref_bbox": ref_bb,
        "sheet": sheet_info,
        "placement": placement_info,
        "scene": scene_info,
        "scenes": scenes_info,
        "pipeline_laser_lines": list(PIPELINE_LASER_LINES),
        "json_coord_decimals": JSON_COORD_DECIMALS,
        "ignored_entities": [{"etype": t, "layer": lyr} for t, lyr in ignored]
    }

    if db_row:
        meta["db_source"] = {
            "schema": DB_SCHEMA,
            "table": DB_TABLE_PQART_SWO,
            "row": db_row
        }

    # Solo se excluye el contorno de placa cuando la fuente fue realmente PLATE.
    # Si PLATE falta, conservar todos los CUT_OUTER y reportar el bbox como estimado.
    if sheet_info and str(sheet_info.get("source_layer") or "").upper() == "PLATE":
        cut_outer_for_json = filter_cut_outer_exclude_sheet_plate(
            raw["CUT_OUTER"], sheet_info
        )
    else:
        cut_outer_for_json = list(raw["CUT_OUTER"])
        if sheet_info:
            sheet_info["dimensions_confirmed"] = False
            sheet_info["estimated_minimum_width_mm"] = sheet_info.get("width_mm")
            sheet_info["estimated_minimum_height_mm"] = sheet_info.get("height_mm")
            placement_warnings = placement_info.setdefault("placement_warnings", [])
            warning = (
                "No se encontro layer PLATE; las dimensiones mostradas provienen "
                "del bbox de geometria y son solo una estimacion minima."
            )
            if warning not in placement_warnings:
                placement_warnings.append(warning)
            placement_info["placement_ok"] = False
            placement_info["normalization_allowed"] = True
    meta["removed_plate_cut_outer"] = len(raw["CUT_OUTER"]) - len(cut_outer_for_json)

    data = {
        "cut_outer": round_contour_coords(cut_outer_for_json),
        "cut_inner": round_contour_coords(filtered_inner),
        "mark": round_contour_coords(raw["MARK"]),
        "meta": meta
    }

    ensure_dir(output_folder)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    write_last_json_pointer(output_file)

    result = {
        "ok": True,
        "cancelled": False,
        "message": "JSON generado correctamente.",
        "dxf_file": dxf_file,
        "output_file": output_file,
        "cut_outer_count": len(data["cut_outer"]),
        "cut_inner_count": len(data["cut_inner"]),
        "mark_count": len(data["mark"]),
        "removed_inner_count": removed,
        "mark_removed_exact_duplicates": mark_cleanup_stats["removed_exact_duplicates"],
        "mark_removed_overlap_duplicates": mark_cleanup_stats["removed_overlap_duplicates"],
        "mark_removed_tiny_lines": mark_cleanup_stats["removed_tiny_lines"],
        "filter_cut_inner": FILTER_CUT_INNER,
        "ref_bbox_mode": REF_BBOX_MODE,
        "stitch_mark": STITCH_MARK,
        "auto_close_min_points": AUTO_CLOSE_MIN_POINTS,
        "ref_bbox": ref_bb,
        "sheet": sheet_info,
        "placement": placement_info,
        "scene": scene_info,
        "scenes": scenes_info,
        "pipeline_laser_lines": list(PIPELINE_LASER_LINES),
        "ignored_entities_count": len(ignored),
        "data": data,
    }

    if db_row:
        result["db_row"] = db_row
        result["db_row_id"] = db_row.get("id")
        result["db_nombre_swo"] = db_row.get("nombre_swo")
        result["db_nombre_dxf"] = db_row.get("nombre_dxf")
        result["db_sheet_code"] = db_row.get("sheet_code")
        result["db_sheet_display_name"] = db_row.get("sheet_display_name")

    return result


def pick_dxf_file(initial_dir=DEFAULT_DXF_INITIAL_DIR):
    """Abre un diálogo para que el usuario elija un archivo DXF."""
    from tkinter import Tk, filedialog

    start_dir = initial_dir if initial_dir and os.path.isdir(initial_dir) else os.getcwd()

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()

    selected = filedialog.askopenfilename(
        title="Seleccionar archivo DXF",
        initialdir=start_dir,
        filetypes=[("Archivos DXF", "*.dxf"), ("Todos los archivos", "*.*")],
    )
    root.destroy()

    return selected or None


def run(dxf_file=None, output_folder=None, initial_dir=DEFAULT_DXF_INITIAL_DIR, use_database=False):
    """
    Ejecuta el lector de DXF.

    Flujo actual para LS READY:
    - Si se pasa dxf_file, procesa esa ruta directamente.
    - Si NO se pasa dxf_file, abre selector de archivos iniciando en Escritorio.
    - La base de datos queda desactivada por defecto; se podrá reactivar después
      llamando run(use_database=True).
    """
    db_row = None

    if dxf_file is None and not use_database:
        dxf_file = pick_dxf_file(initial_dir=initial_dir)
        if not dxf_file:
            return {
                "ok": False,
                "cancelled": True,
                "message": "Operación cancelada: no se seleccionó ningún DXF.",
                "dxf_file": None,
                "output_file": None,
            }

    if dxf_file is None and use_database:
        row = fetch_next_robotlaser_row()
        if not row:
            return {
                "ok": False,
                "cancelled": False,
                "queue_empty": True,
                "message": (
                    f"No se encontró ningún registro pendiente en "
                    f"{DB_SCHEMA}.{DB_TABLE_PQART_SWO} con tipo_corte='{DB_TARGET_TIPO_CORTE}'."
                ),
                "dxf_file": None,
                "output_file": None,
            }

        dxf_path = normalize_db_path(row.get("ruta"))
        row_id = row.get("id")

        if not dxf_path:
            raise RuntimeError(
                f"El registro id={row_id} no contiene una ruta válida en la columna 'ruta'."
            )

        if not os.path.exists(dxf_path):
            raise FileNotFoundError(
                f"La ruta del DXF del registro id={row_id} no existe físicamente: {dxf_path}"
            )

        row["ruta"] = dxf_path
        dxf_file = dxf_path
        db_row = row

    if not dxf_file:
        return {
            "ok": False,
            "cancelled": False,
            "message": "No se pudo resolver ningún archivo DXF para procesar.",
            "dxf_file": None,
            "output_file": None,
        }

    return process_dxf(dxf_file=dxf_file, output_folder=output_folder, db_row=db_row)


def print_summary(result):
    if not result:
        print("No se recibió resultado.")
        return

    if result.get("cancelled"):
        print(result.get("message", "Operación cancelada."))
        return

    if not result.get("ok"):
        print(result.get("message", "La operación no se completó correctamente."))
        return

    print("JSON generado:", result["output_file"])
    print("Archivo DXF seleccionado:", result["dxf_file"])
    print("CUT_OUTER:", result["cut_outer_count"])
    print("CUT_INNER:", result["cut_inner_count"], "(removidos:", result["removed_inner_count"], ")")
    print("MARK:", result["mark_count"])
    print("MARK exact duplicates removed:", result["mark_removed_exact_duplicates"])
    print("MARK overlap duplicates removed:", result["mark_removed_overlap_duplicates"])
    print("MARK tiny lines removed:", result["mark_removed_tiny_lines"])
    print("FILTER_CUT_INNER:", result["filter_cut_inner"])
    print("REF_BBOX_MODE:", result["ref_bbox_mode"])
    print("STITCH_MARK:", result["stitch_mark"])
    print("AUTO_CLOSE_MIN_POINTS:", result["auto_close_min_points"])

    if result.get("ref_bbox"):
        print("REF_BBOX:", result["ref_bbox"])

    sheet = result.get("sheet")
    if sheet:
        print(
            "PLACA:",
            sheet.get("width_mm"),
            "x",
            sheet.get("height_mm"),
            "mm",
            "(",
            sheet.get("plate_size_in"),
            "in | layer:",
            sheet.get("source_layer"),
            ")",
        )
        if sheet.get("scene_size_key"):
            print("ESCENA SIZE KEY:", sheet.get("scene_size_key"))
        if sheet.get("code") or sheet.get("display_name"):
            print("PLACA ID:", sheet.get("code"), "-", sheet.get("display_name"))

    scene = result.get("scene")
    if scene:
        if scene.get("error"):
            print("ESCENA: ERROR ->", scene.get("error"))
        else:
            print("ESCENA:", scene.get("scene_filename"))
            print("ESCENA PATH:", scene.get("scene_path"))

    if result.get("ignored_entities_count", 0) > 0:
        print("Entidades ignoradas:", result["ignored_entities_count"])

    if result.get("db_row_id") is not None:
        print("DB row id:", result.get("db_row_id"))
        print("DB host:", (result.get("db_row") or {}).get("_db_host"))
        print("DB nombre_swo:", result.get("db_nombre_swo"))
        print("DB nombre_dxf:", result.get("db_nombre_dxf"))
        print("DB sheet_code:", result.get("db_sheet_code"))
        print("DB sheet_display_name:", result.get("db_sheet_display_name"))


def main():
    try:
        dxf_file = pick_dxf_file()
        if not dxf_file:
            print("Operación cancelada: no se seleccionó ningún DXF.")
            return 0

        result = run(dxf_file=dxf_file)
        print_summary(result)
        if result.get("cancelled"):
            return 0
        return 0 if result.get("ok") else 1
    except Exception as e:
        print(f"Error en lector_dxf: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())