"""
Nesting 1D para largos de cobre (CU).

- Orientación nativa del DXF (sin rotar).
- Eje X = avance a lo largo del bar; eje Y = ancho (empalme con inventario).
- Sin tolerancia: si el ancho excede la tira exacta, sube a la tira más ancha siguiente.
- Separación 3/8" entre piezas >15" de largo (eje X del DXF); piezas ≤15" siempre sin separación
  (incluso al renestear con otra distancia).
- Export DXF cobre: CUT_OUTER = láser; CUT_INNER + MARK; CUT_CU = contorno completo de pieza
  (+ marcador vertical inicio barra en export de hoja).

Cortes CUT_OUTER (láser):
  1. Pieza rectangular a ancho exacto de tira (ej. 4" en barra 4"): solo guillotinas verticales.
  2. Pieza más angosta que la tira disponible (ej. 4.2" o 4.5" en barra 5"): barra superior +
     línea horizontal CUT_OUTER (CU_CORTE__SUP__) a la altura del ancho real de la pieza.
  3. Pieza con relieve en la frontera entre rebanadas: escalón/diagonal local junto al corte
     vertical (no el techo largo del stock que atravesaría toda la pieza).
  4. CU_CORTE__V__ intermedias solo en visor; al DXF láser solo la del final de barra.
"""
from __future__ import annotations

import copy
import math
from typing import Callable, Dict, List, Optional, Tuple

from shapely.geometry import LineString
from shapely import affinity

from .cu_inventory import es_placa_largo_cu
from .efficiency_metrics import calcular_eficiencias_grupo

TOL_ANCHO_IN_MIN = 0.02
TOL_GEOM_MM = 0.15
PREFIJO_CORTE_CU = "CU_CORTE__"
# Separación por defecto entre piezas en el eje del largo (solo cobre largos).
DEFAULT_SEPARACION_CU_IN = 0.375  # 3/8"
# Piezas con largo DXF (eje X) hasta este umbral van siempre sin separación (renest no aplica).
LARGO_SIN_SEPARACION_CU_IN = 15.0
# Banda local junto a la frontera de rebanada donde vive el relieve (no todo el techo).
RELIEF_BAND_FRAC = 0.40
RELIEF_BAND_MIN_MM = 10.0


def _dims_pieza_nativo_mm(poly) -> Optional[Tuple[float, float]]:
    if poly is None or poly.is_empty:
        return None
    minx, miny, maxx, maxy = poly.bounds
    largo_x = float(maxx - minx)
    ancho_y = float(maxy - miny)
    if largo_x <= 0 or ancho_y <= 0:
        return None
    return largo_x, ancho_y


def _estandares_ancho_in(catalogo: List[dict]) -> List[float]:
    vals = sorted({round(float(b["ancho_in"]), 4) for b in catalogo if b.get("ancho_in")})
    return vals


def _resolver_barra_para_pieza(
    ancho_pieza_mm: float,
    catalogo: List[dict],
) -> Tuple[Optional[dict], float, bool]:
    """
    Devuelve (barra, corte_superior_mm, calibre_superior).
    Sin tolerancia porcentual: si no hay tira del mismo ancho, usa la más angosta que aún contenga la pieza.
    """
    if not catalogo:
        return None, 0.0, False

    a_in = float(ancho_pieza_mm) / 25.4
    estandares = _estandares_ancho_in(catalogo)
    if not estandares:
        return None, 0.0, False

    exactas = [
        b for b in catalogo
        if float(ancho_pieza_mm) <= float(b["ancho_mm"]) + 0.01
        and abs(float(b["ancho_in"]) - a_in) <= TOL_ANCHO_IN_MIN
    ]
    if exactas:
        barra = min(exactas, key=lambda b: (b.get("precio_lb", 0.0), b.get("precio", 0.0)))
        return barra, 0.0, False

    fitting = [
        b for b in catalogo
        if float(b["ancho_mm"]) + 0.01 >= float(ancho_pieza_mm)
    ]
    if not fitting:
        return None, 0.0, True

    barra = min(fitting, key=lambda b: (float(b["ancho_in"]), b.get("precio_lb", 0.0), b.get("precio", 0.0)))
    nominal_in = min(estandares, key=lambda w: abs(w - a_in))
    calibre_superior = float(barra["ancho_in"]) > nominal_in + TOL_ANCHO_IN_MIN
    corte_sup_mm = max(0.0, float(barra["ancho_mm"]) - float(ancho_pieza_mm))
    return barra, corte_sup_mm, calibre_superior


def _ancho_coincide_barra(ancho_pieza_mm: float, ancho_barra_mm: float) -> bool:
    return abs((ancho_pieza_mm / 25.4) - (ancho_barra_mm / 25.4)) <= TOL_ANCHO_IN_MIN


def _poly_a_poligonos(poly) -> List:
    if poly is None or poly.is_empty:
        return []
    outer = list(poly.exterior.coords)
    holes = [list(h.coords) for h in poly.interiors]
    return [outer] + holes


def _marks_a_lista(marks) -> List:
    if marks is None or marks.is_empty:
        return []
    if marks.geom_type == "LineString":
        return [list(marks.coords)]
    if marks.geom_type == "MultiLineString":
        return [list(line.coords) for line in marks.geoms]
    return []


def _colocar_pieza_nativa(
    p_data: dict,
    x_mm: float,
    y_mm: float,
    *,
    corte_superior_mm: float = 0.0,
    calibre_superior: bool = False,
) -> dict:
    poly = p_data["poly"]
    marks = p_data.get("marks") or LineString()

    minx, miny, _, _ = poly.bounds
    poly_norm = affinity.translate(poly, -minx, -miny)
    if marks is not None and not marks.is_empty:
        marks_norm = affinity.translate(marks, -minx, -miny)
    else:
        marks_norm = LineString()

    poly_final = affinity.translate(poly_norm, x_mm, y_mm)
    marks_final = (
        affinity.translate(marks_norm, x_mm, y_mm)
        if not marks_norm.is_empty
        else marks_norm
    )

    _, ancho_y = _dims_pieza_nativo_mm(poly_norm) or (0.0, 0.0)
    y_corte = float(y_mm) + float(ancho_y)

    return {
        "nombre": str(p_data.get("nombre", "")),
        "poligonos": _poly_a_poligonos(poly_final),
        "marcas": _marks_a_lista(marks_final),
        "area": float(p_data.get("area", poly_final.area) or poly_final.area),
        "calibre": p_data.get("calibre", ""),
        "material": p_data.get("material", "CU"),
        "ruta": str(p_data.get("ruta") or ""),
        "orig_minx": float(p_data.get("orig_minx", 0.0) or 0.0),
        "orig_miny": float(p_data.get("orig_miny", 0.0) or 0.0),
        "shift_x": float(x_mm),
        "shift_y": float(y_mm),
        "rot_deg": 0.0,
        "rot_origin_cx": float(p_data.get("rot_origin_cx", 0.0) or 0.0),
        "rot_origin_cy": float(p_data.get("rot_origin_cy", 0.0) or 0.0),
        "corte_superior_mm": float(corte_superior_mm),
        "calibre_superior": bool(calibre_superior),
        "y_corte_superior_mm": y_corte,
        "x_inicio_mm": float(x_mm),
        "largo_mm": float(p_data.get("len_mm") or 0.0),
        "largo_cu_in": _largo_pieza_cu_in(p_data),
        "cu_sin_separacion": _pieza_cu_exime_separacion(p_data),
    }


def _es_rectangulo_axis_aligned(exterior: list, tol: float = 0.5) -> bool:
    if not exterior or len(exterior) < 4:
        return False
    pts = exterior[:-1] if exterior[0] == exterior[-1] else list(exterior)
    if len(pts) != 4:
        return False
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    for x, y in zip(xs, ys):
        on_edge = (
            (abs(x - minx) <= tol or abs(x - maxx) <= tol)
            and (abs(y - miny) <= tol or abs(y - maxy) <= tol)
        )
        if not on_edge:
            return False
    return True


def _bbox_poligono(pts: list) -> Tuple[float, float, float, float]:
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _vertices_cerrados(exterior: list) -> list:
    if not exterior:
        return []
    pts = list(exterior)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _es_arista_horizontal(x1: float, y1: float, x2: float, y2: float, tol: float) -> bool:
    return abs(y1 - y2) <= tol


def _es_arista_vertical(x1: float, y1: float, x2: float, y2: float, tol: float) -> bool:
    return abs(x1 - x2) <= tol


def _tiene_escalon_uperior(exterior: list, tol: float = TOL_GEOM_MM) -> bool:
    """
    True si el contorno tiene un escalón/muesca (arista horizontal interior).
    Piezas laminadas rectas solo tienen techo plano a todo lo ancho.
    """
    pts = _vertices_cerrados(exterior)
    if len(pts) < 3:
        return False
    _minx, miny, _maxx, maxy = _bbox_poligono(pts)
    for i in range(len(pts)):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[(i + 1) % len(pts)][0]), float(pts[(i + 1) % len(pts)][1])
        if not _es_arista_horizontal(x1, y1, x2, y2, tol):
            continue
        y = (y1 + y2) / 2.0
        if y <= miny + tol or y >= maxy - tol:
            continue
        return True
    return False


def _solo_cortes_guillotina_vertical(exterior: list, tol: float = TOL_GEOM_MM) -> bool:
    """
    Lámina ortogonal sin escalón: basta guillotina vertical entre piezas.
    (Aplica aunque el DXF tenga muchos vértices colineales en el bbox.)
    """
    pts = _vertices_cerrados(exterior)
    if len(pts) < 3:
        return True
    minx, miny, maxx, maxy = _bbox_poligono(pts)
    ancho = maxx - minx
    alto = maxy - miny
    if ancho <= tol or alto <= tol:
        return True

    cobertura_techo = 0.0
    for i in range(len(pts)):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[(i + 1) % len(pts)][0]), float(pts[(i + 1) % len(pts)][1])
        if not (
            _es_arista_horizontal(x1, y1, x2, y2, tol)
            or _es_arista_vertical(x1, y1, x2, y2, tol)
        ):
            return False
        if _es_arista_horizontal(x1, y1, x2, y2, tol) and abs((y1 + y2) / 2.0 - maxy) <= tol:
            cobertura_techo += abs(x2 - x1)

    if cobertura_techo < ancho - max(tol, ancho * 0.02):
        return False
    if _tiene_escalon_uperior(exterior, tol=tol):
        return False
    return True


def _frontera_basta_guillotina_vertical(
    exterior: list,
    frontera_x: float,
    tol: float = TOL_GEOM_MM,
) -> bool:
    """
    En esta frontera de rebanada no hace falta relieve láser: el contorno
    presenta un canto vertical continuo (corte vertical entre piezas).
    """
    pts = _vertices_cerrados(exterior)
    if len(pts) < 3:
        return True
    minx, miny, maxx, maxy = _bbox_poligono(pts)
    alto = maxy - miny
    if alto <= tol:
        return True
    if abs(frontera_x - minx) > tol and abs(frontera_x - maxx) > tol:
        return True

    band = max((maxx - minx) * RELIEF_BAND_FRAC, RELIEF_BAND_MIN_MM)
    span_vert = 0.0

    for i in range(len(pts)):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[(i + 1) % len(pts)][0]), float(pts[(i + 1) % len(pts)][1])
        xmin_e = min(x1, x2)
        xmax_e = max(x1, x2)
        ymin_e = min(y1, y2)
        ymax_e = max(y1, y2)

        if xmax_e < frontera_x - band or xmin_e > frontera_x + band:
            continue

        if _es_arista_vertical(x1, y1, x2, y2, tol) and abs((x1 + x2) / 2.0 - frontera_x) <= tol:
            span_vert = max(span_vert, ymax_e - ymin_e)
            continue

        # Escalón local en la frontera (arista horizontal interior o diagonal).
        if _es_arista_horizontal(x1, y1, x2, y2, tol):
            y = (y1 + y2) / 2.0
            if miny + tol < y < maxy - tol:
                return False
            continue

        return False

    return span_vert >= alto - max(tol, alto * 0.05)


def _segmentos_relieve_en_frontera(
    exterior: list,
    frontera_x: float,
    tol: float = TOL_GEOM_MM,
) -> List[list]:
    """
    Solo aristas del relieve en la zona local de una frontera de rebanada.
    No incluye el techo largo del stock ni la guillotina vertical completa.
    """
    if not exterior or len(exterior) < 2:
        return []
    if _solo_cortes_guillotina_vertical(exterior, tol=tol):
        return []
    if _frontera_basta_guillotina_vertical(exterior, frontera_x, tol=tol):
        return []
    if _es_rectangulo_axis_aligned(exterior, tol=0.5):
        return []

    pts = _vertices_cerrados(exterior)
    n = len(pts)
    if n < 2:
        return []

    minx, miny, maxx, maxy = _bbox_poligono(pts)
    band = max((maxx - minx) * RELIEF_BAND_FRAC, RELIEF_BAND_MIN_MM)

    def _es_horizontal(y1: float, y2: float, y_ref: float) -> bool:
        return abs(y1 - y_ref) <= tol and abs(y2 - y_ref) <= tol

    def _es_vertical(x1: float, x2: float, x_ref: float) -> bool:
        return abs(x1 - x_ref) <= tol and abs(x2 - x_ref) <= tol

    segmentos: List[list] = []
    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        xmin_e = min(x1, x2)
        xmax_e = max(x1, x2)
        ymin_e = min(y1, y2)
        ymax_e = max(y1, y2)
        seg_len = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if seg_len <= tol:
            continue

        if _es_horizontal(y1, y2, miny):
            continue

        if xmax_e < frontera_x - band or xmin_e > frontera_x + band:
            continue

        if _es_vertical(x1, x2, frontera_x) and (ymax_e - ymin_e) > (maxy - miny) * 0.45:
            continue

        if _es_horizontal(y1, y2, maxy):
            if abs(xmax_e - xmin_e) > band + tol:
                continue
            centro_x = (x1 + x2) / 2.0
            if abs(centro_x - frontera_x) > band * 0.65:
                continue

        if _es_arista_horizontal(x1, y1, x2, y2, tol) or _es_arista_vertical(x1, y1, x2, y2, tol):
            segmentos.append([(x1, y1), (x2, y2)])
        elif seg_len > tol * 2:
            segmentos.append([(x1, y1), (x2, y2)])

    return segmentos


def _segmentos_corte_laser_pieza(
    exterior: list,
    *,
    idx: int,
    n_total: int,
    tol: float = TOL_GEOM_MM,
) -> List[list]:
    """
    Cortes láser de relieve por pieza: solo el perfil local en la frontera de
    rebanada (escalón/diagonal junto al corte vertical), nunca el techo largo
    que atravesaría toda la pieza en vacío.
    """
    if not exterior:
        return []
    if _solo_cortes_guillotina_vertical(exterior, tol=tol):
        return []

    pts = _vertices_cerrados(exterior)
    if len(pts) < 2:
        return []
    minx, _miny, maxx, _maxy = _bbox_poligono(pts)

    izq: List[list] = []
    der: List[list] = []
    segmentos: List[list] = []
    if idx > 0 and not _frontera_basta_guillotina_vertical(exterior, minx, tol=tol):
        izq = _segmentos_relieve_en_frontera(exterior, minx, tol=tol)
    if idx < n_total - 1 and not _frontera_basta_guillotina_vertical(exterior, maxx, tol=tol):
        der = _segmentos_relieve_en_frontera(exterior, maxx, tol=tol)

    if izq:
        segmentos.extend(izq)
    if der:
        segmentos.extend(der)
    return segmentos


def _arista_en_relieve_frontera(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    frontera_x: float,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    tol: float = TOL_GEOM_MM,
) -> bool:
    """True si el segmento es corte láser de relieve local en una frontera de rebanada."""
    xmin_e = min(x1, x2)
    xmax_e = max(x1, x2)
    ymin_e = min(y1, y2)
    ymax_e = max(y1, y2)
    seg_len = math.hypot(x2 - x1, y2 - y1)
    if seg_len <= tol:
        return False

    band = max((maxx - minx) * RELIEF_BAND_FRAC, RELIEF_BAND_MIN_MM)

    if _es_arista_horizontal(x1, y1, x2, y2, tol) and abs((y1 + y2) / 2.0 - miny) <= tol:
        return False

    if xmax_e < frontera_x - band or xmin_e > frontera_x + band:
        return False

    if (
        _es_arista_vertical(x1, y1, x2, y2, tol)
        and abs((x1 + x2) / 2.0 - frontera_x) <= tol
        and (ymax_e - ymin_e) > (maxy - miny) * 0.45
    ):
        return False

    if _es_arista_horizontal(x1, y1, x2, y2, tol) and abs((y1 + y2) / 2.0 - maxy) <= tol:
        if abs(xmax_e - xmin_e) > band + tol:
            return False
        centro_x = (x1 + x2) / 2.0
        if abs(centro_x - frontera_x) > band * 0.65:
            return False

    if _es_arista_horizontal(x1, y1, x2, y2, tol) or _es_arista_vertical(x1, y1, x2, y2, tol):
        return True
    return seg_len > tol * 2


def segmentos_laser_relieve_desde_contorno(
    exterior: list,
    *,
    idx: int,
    n_total: int,
    tol: float = TOL_GEOM_MM,
) -> List[list]:
    """
    Aristas de relieve láser (CUT_OUTER) en fronteras izq/der del contorno colocado.
    Usa el mismo criterio que el nesting; devuelve ambos lados si aplican.
    """
    if not exterior:
        return []
    if _solo_cortes_guillotina_vertical(exterior, tol=tol):
        return []

    pts = _vertices_cerrados(exterior)
    if len(pts) < 2:
        return []
    minx, miny, maxx, maxy = _bbox_poligono(pts)
    out: List[list] = []

    def _collect(frontera_x: float) -> None:
        if _frontera_basta_guillotina_vertical(exterior, frontera_x, tol=tol):
            return
        n = len(pts)
        for i in range(n):
            x1, y1 = float(pts[i][0]), float(pts[i][1])
            x2, y2 = float(pts[(i + 1) % n][0]), float(pts[(i + 1) % n][1])
            if _arista_en_relieve_frontera(
                x1, y1, x2, y2, frontera_x, minx, miny, maxx, maxy, tol
            ):
                out.append([(x1, y1), (x2, y2)])

    if idx > 0:
        _collect(minx)
    if idx < n_total - 1:
        _collect(maxx)
    return out


def _linea_corte_segmento(seg: list, tag: str, pieza_nom: str) -> dict:
    return {
        "nombre": f"{PREFIJO_CORTE_CU}{tag}__{pieza_nom}",
        "poligonos": [list(seg)],
        "marcas": [],
        "area": 0.0,
        "calibre": "",
        "material": "CU",
        "rot_deg": 0.0,
        "shift_x": 0.0,
        "shift_y": 0.0,
        "es_corte_cu": True,
        "layer_override": "CUT_OUTER",
        "closed": False,
    }


def _guillotina_superior_pieza(p_colocada: dict) -> Optional[dict]:
    corte = float(p_colocada.get("corte_superior_mm") or 0.0)
    if corte <= 0.5:
        return None

    x0 = float(p_colocada.get("x_inicio_mm") or 0.0)
    largo = float(p_colocada.get("largo_mm") or 0.0)
    y0 = float(p_colocada.get("y_corte_superior_mm") or 0.0)
    if largo <= 0:
        return None

    nom_base = str(p_colocada.get("nombre", "PIEZA"))
    return {
        "nombre": f"{PREFIJO_CORTE_CU}SUP__{nom_base}",
        "poligonos": [[(x0, y0), (x0 + largo, y0)]],
        "marcas": [],
        "area": 0.0,
        "calibre": p_colocada.get("calibre", ""),
        "material": p_colocada.get("material", "CU"),
        "rot_deg": 0.0,
        "shift_x": 0.0,
        "shift_y": 0.0,
        "es_corte_cu": True,
        "layer_override": "CUT_OUTER",
        "closed": False,
    }


def _linea_corte_vertical(x_mm: float, alto_mm: float, indice: int) -> dict:
    return {
        "nombre": f"{PREFIJO_CORTE_CU}V__{indice}",
        "poligonos": [[(float(x_mm), 0.0), (float(x_mm), float(alto_mm))]],
        "marcas": [],
        "area": 0.0,
        "calibre": "",
        "material": "CU",
        "rot_deg": 0.0,
        "shift_x": 0.0,
        "shift_y": 0.0,
        "es_corte_cu": True,
        "layer_override": "CUT_OUTER",
        "closed": False,
    }


def _largo_pieza_cu_in(item: dict) -> float:
    """Largo nativo del DXF en el eje X (avance de barra), en pulgadas."""
    if item is None:
        return 0.0
    len_mm = item.get("len_mm")
    if len_mm is None:
        dims = _dims_pieza_nativo_mm(item.get("poly"))
        if dims:
            len_mm = dims[0]
    try:
        return max(0.0, float(len_mm or 0.0) / 25.4)
    except (TypeError, ValueError):
        return 0.0


def _pieza_cu_exime_separacion(item: dict) -> bool:
    """True si el largo DXF es ≤15\": siempre gap 0 con vecinos."""
    return _largo_pieza_cu_in(item) <= LARGO_SIN_SEPARACION_CU_IN


def _gap_entre_piezas_cu(prev_item: dict, next_item: dict, separacion_in: float) -> float:
    """
    Separación en mm entre dos piezas consecutivas en la barra.
    Piezas ≤15\" (largo DXF) fuerzan gap 0; si ambas son >15\" usa separacion_in (p. ej. 3/8\").
    """
    if prev_item is None or next_item is None:
        return 0.0
    if _pieza_cu_exime_separacion(prev_item) or _pieza_cu_exime_separacion(next_item):
        return 0.0
    return max(0.0, float(separacion_in or 0.0)) * 25.4


def _normalizar_barras(placas_ok: List[dict]) -> List[dict]:
    barras = []
    for placa in placas_ok or []:
        if not es_placa_largo_cu(placa):
            continue
        w_mm = float(placa.get("w") or 0.0)
        h_mm = float(placa.get("h") or 0.0)
        if w_mm <= 0 or h_mm <= 0:
            continue
        largo_mm = max(w_mm, h_mm)
        ancho_mm = min(w_mm, h_mm)
        barras.append(
            {
                **placa,
                "largo_mm": largo_mm,
                "ancho_mm": ancho_mm,
                "largo_in": largo_mm / 25.4,
                "ancho_in": ancho_mm / 25.4,
            }
        )
    barras.sort(key=lambda b: (b["ancho_in"], b.get("precio_lb", 0.0), b.get("precio", 0.0)))
    return barras


def empaquetar_largos_cu(
    piezas: List[dict],
    placas_ok: List[dict],
    *,
    separacion_in: float = DEFAULT_SEPARACION_CU_IN,
) -> Tuple[List[dict], List[dict]]:
    catalogo = _normalizar_barras(placas_ok)
    if not catalogo:
        return [], list(piezas or [])

    items = []
    sin_colocar: List[dict] = []
    for p in piezas or []:
        dims = _dims_pieza_nativo_mm(p.get("poly"))
        if dims is None:
            sin_colocar.append(copy.deepcopy(p))
            continue
        largo_x, ancho_y = dims
        barra, corte_sup, cal_sup = _resolver_barra_para_pieza(ancho_y, catalogo)
        if barra is None:
            sin_colocar.append({**copy.deepcopy(p), "ancho_in": ancho_y / 25.4})
            continue
        items.append(
            {
                **copy.deepcopy(p),
                "len_mm": largo_x,
                "wid_mm": ancho_y,
                "ancho_in": ancho_y / 25.4,
                "barra_objetivo_in": float(barra["ancho_in"]),
                "corte_superior_mm": corte_sup,
                "calibre_superior": cal_sup,
            }
        )

    items.sort(key=lambda x: (x["barra_objetivo_in"], -x["len_mm"]))

    barras_abiertas: List[dict] = []

    for item in items:
        colocado = False
        for barra in barras_abiertas:
            if abs(barra["ancho_in"] - item["barra_objetivo_in"]) > TOL_ANCHO_IN_MIN:
                continue
            restante = barra["largo_mm"] - barra["cursor_x"]
            prev_item = barra["colocados"][-1][0] if barra["colocados"] else None
            gap_before = (
                _gap_entre_piezas_cu(prev_item, item, separacion_in)
                if barra["colocados"]
                else 0.0
            )
            needed = gap_before + item["len_mm"]
            if needed > restante + 0.5:
                continue
            x_pos = barra["cursor_x"] + gap_before
            barra["colocados"].append((item, x_pos, 0.0))
            barra["cursor_x"] = x_pos + item["len_mm"]
            colocado = True
            break

        if colocado:
            continue

        stock = next(
            (b for b in catalogo if abs(b["ancho_in"] - item["barra_objetivo_in"]) <= TOL_ANCHO_IN_MIN),
            None,
        )
        if stock is None:
            if item not in sin_colocar:
                sin_colocar.append(item)
            continue

        barras_abiertas.append(
            {
                "stock": stock,
                "largo_mm": stock["largo_mm"],
                "ancho_mm": stock["ancho_mm"],
                "ancho_in": stock["ancho_in"],
                "cursor_x": item["len_mm"],
                "colocados": [(item, 0.0, 0.0)],
            }
        )

    hojas: List[dict] = []
    for barra in barras_abiertas:
        stock = barra["stock"]
        piezas_hoja = []
        area_usada = 0.0
        requiere_corte_sup = False

        piezas_reales: List[dict] = []
        for p_data, x_mm, y_mm in barra["colocados"]:
            p_final = _colocar_pieza_nativa(
                p_data,
                x_mm,
                y_mm,
                corte_superior_mm=float(p_data.get("corte_superior_mm") or 0.0),
                calibre_superior=bool(p_data.get("calibre_superior")),
            )
            piezas_hoja.append(p_final)
            piezas_reales.append(p_final)
            area_usada += float(p_final.get("area") or 0.0)

            if float(p_data.get("corte_superior_mm") or 0.0) > 0.5:
                requiere_corte_sup = True
                guill = _guillotina_superior_pieza(p_final)
                if guill:
                    piezas_hoja.append(guill)

        n_col = len(piezas_reales)
        for idx, p_final in enumerate(piezas_reales):
            p_final["cu_slice_idx"] = idx
            p_final["cu_slice_count"] = n_col
            exterior = (p_final.get("poligonos") or [[]])[0]
            tiene_dxf = bool(str(p_final.get("ruta") or "").strip())
            for seg_i, seg in enumerate(
                _segmentos_corte_laser_pieza(
                    exterior,
                    idx=idx,
                    n_total=n_col,
                )
            ):
                if tiene_dxf:
                    # Relieves exactos desde DXF fuente en export_nest_to_dxf.
                    continue
                piezas_hoja.append(
                    _linea_corte_segmento(
                        seg,
                        f"REL_{idx}_{seg_i}",
                        str(p_final.get("nombre", "PIEZA")),
                    )
                )

        if len(barra["colocados"]) > 1:
            for idx_corte, (_p_data, x_mm, _y_mm) in enumerate(barra["colocados"]):
                if idx_corte <= 0:
                    continue
                piezas_hoja.append(
                    _linea_corte_vertical(x_mm, float(barra["ancho_mm"]), idx_corte)
                )

        if barra["colocados"]:
            x_fin = float(barra["cursor_x"])
            piezas_hoja.append(
                _linea_corte_vertical(
                    x_fin,
                    float(barra["ancho_mm"]),
                    len(barra["colocados"]),
                )
            )

        largo_mm = float(barra["largo_mm"])
        ancho_mm = float(barra["ancho_mm"])
        area_total = largo_mm * ancho_mm if largo_mm > 0 and ancho_mm > 0 else 0.0
        hojas.append(
            {
                "piezas": piezas_hoja,
                "area_usada": area_usada,
                "eficiencia": (area_usada / area_total * 100.0) if area_total > 0 else 0.0,
                "placa_id": stock.get("id"),
                "placa_w": largo_mm,
                "placa_h": ancho_mm,
                "precio_placa": stock.get("precio", 0.0),
                "kerf_usado": 0.0,
                "margin_usado": 0.0,
                "opt_usado": "LARGOS CU",
                "corner_usado": "INFERIOR IZQUIERDA",
                "es_retazo": False,
                "origen_placa": stock.get("origen", "EMPRESA"),
                "modo_largos_cu": True,
                "separacion_cu_in": max(0.0, float(separacion_in or 0.0)),
                "largo_sin_separacion_cu_in": LARGO_SIN_SEPARACION_CU_IN,
                "requiere_corte_superior": requiere_corte_sup,
                "ignorar_deduccion": True,
            }
        )

    return hojas, sin_colocar


def procesar_grupo_largos_cu(
    clave: str,
    piezas: List[dict],
    placas_ok: List[dict],
    wo_name: str = "PENDIENTE",
    dbg_fn: Optional[Callable[[str], None]] = None,
    exigir_colocacion_total: bool = False,
    separacion_in: float = DEFAULT_SEPARACION_CU_IN,
) -> Tuple[str, dict]:
    _log = dbg_fn or (lambda _msg: None)
    sep_in = max(0.0, float(separacion_in if separacion_in is not None else DEFAULT_SEPARACION_CU_IN))
    _log(
        f"[CU-LARGOS] clave={clave} | piezas={len(piezas)} | wo={wo_name} | "
        f"separacion={sep_in:.4f}in"
    )

    if not placas_ok:
        return clave, {"error": "Sin inventario CU disponible para largos."}

    hojas, sin_colocar = empaquetar_largos_cu(piezas, placas_ok, separacion_in=sep_in)
    if sin_colocar:
        detalle = []
        for p in sin_colocar:
            ancho = round(float(p.get("ancho_in", p.get("wid_mm", 0) or 0) / 25.4), 3)
            detalle.append(f"{p.get('nombre', '?')} ({ancho}\")")
        nombres = ", ".join(sorted(detalle))
        _log(f"[CU-LARGOS][SIN-COLOCAR] {nombres}")
        if exigir_colocacion_total or not hojas:
            msg_base = (
                "No se pudieron colocar todas las piezas en largos CU (144\" × 2–6\")."
                if exigir_colocacion_total
                else "No se pudieron anidar piezas de cobre."
            )
            return clave, {
                "error": (
                    f"{msg_base} "
                    f"Sin colocar: {len(sin_colocar)} — {nombres}. "
                    "Verifique ancho (2–6\") y largo ≤ 144\"."
                )
            }

    costo_total = sum(float(h.get("precio_placa") or 0.0) for h in hojas)
    costo_empresa = sum(
        float(h.get("precio_placa") or 0.0)
        for h in hojas
        if str(h.get("origen_placa", "EMPRESA")).upper() in ("", "EMPRESA")
    )
    costo_proveedor = costo_total - costo_empresa
    efi_grupo = calcular_eficiencias_grupo(hojas)

    _log(f"[CU-LARGOS][OK] barras={len(hojas)} | pendientes={len(sin_colocar)}")

    resultado = {
        "placa": "Largos CU",
        "dim": "1D",
        "hojas": hojas,
        "piezas_pool": [
            {"nombre": str(p.get("nombre") or "")}
            for p in (piezas or [])
            if str(p.get("nombre") or "")
        ],
        "piezas_pool_engine": True,
        "costo_total": costo_total,
        "costo_empresa": costo_empresa,
        "costo_proveedor": costo_proveedor,
        "reporte": "Nesting 1D largos de cobre (orientación DXF, sin tolerancia de ancho).",
        "modo_largos_cu": True,
        "separacion_cu_in": sep_in,
        "largo_sin_separacion_cu_in": LARGO_SIN_SEPARACION_CU_IN,
        "ignorar_deduccion_cu": True,
        **efi_grupo,
    }
    if sin_colocar:
        resultado["piezas_pendientes"] = [str(p.get("nombre", "")) for p in sin_colocar]
        resultado["advertencia"] = (
            f"{len(sin_colocar)} pieza(s) CU sin barra compatible (ancho/largo)."
        )

    return clave, resultado
