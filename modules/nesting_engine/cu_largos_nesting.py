"""
Nesting 1D para largos de cobre (CU).

- Orientación exacta elegida en PARTS: el empaquetador no vuelve a girar la pieza.
- Eje X = avance a lo largo del bar; eje Y = ancho (empalme con inventario).
- Sin tolerancia: si el ancho excede la tira exacta, sube a la tira más ancha siguiente.
- Gap: 3/8" siempre entre piezas normales. Sin umbral por largo.
  Excepciones sin_gap (independiente del largo):
  1) Perfil relieve/Z (escalón/diagonal) — auto por geometría.
  2) Pieza marcada especial en PARTS (`cu_especial_vertical`) — barra vertical CyPTube
     con cortes + MARK (sin barrenos); export AMADA/FIXTURA con colchón +10\" + barrenos.
  Relieve/Z y especiales se priorizan al inicio y NUNCA van a RTZCU (si no caben
  en ≤114\", abren barra nueva). Amada (PARTS) y Z NUNCA comparten la misma barra:
  Amada ESP. solo admite barras de 5\" de ancho. Otras piezas rectangulares en
  la misma barra Amada/Z van a RTZCU con gap.
  Rectángulos con orilla o laminas verticales exactas SÍ pueden ir a RTZCU.
  Cada pieza usa su tira objetivo (exacta o la mínima del catálogo).
- Export DXF cobre: CUT_OUTER = láser; CUT_INNER + MARK (con_gap / Amada).
- sin_gap madre (Z): DXF vertical CyPTube + MARK + BAR_START.
- especial PARTS: AMADA/VERTICAL (barra CyPTube: cortes + MARK, sin barrenos) +
  AMADA/FIXTURA (1 DXF por pieza ESP.: colchón +10\" join + barrenos, sin marcaje).
  El RTZCU también exporta a DXF/STEP normal.
- con_gap: CUT_OUTER cerrado por pieza + STEP.

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

TOL_ANCHO_IN_MIN = 0.02  # ~1/50": DXF/medición (ej. 2.0015" sigue en tira 2")
# Fixturas Amada: solo admiten barras de exactamente 5" de ancho.
AMADA_FIXTURA_ANCHO_IN = 5.0
# Largo máximo absoluto (fallback): el mayor canal del catálogo (~35.33").
# El valor real se toma de listar_fixturas_amada / amada_fixtura_largo_max_in().
AMADA_FIXTURA_LARGO_MAX_IN = 35.33
TOL_GEOM_MM = 0.15
PREFIJO_CORTE_CU = "CU_CORTE__"


def amada_fixtura_largo_max_in() -> float:
    """Mayor canal Amada disponible (entre topes), en pulgadas."""
    try:
        from modules.dxf_export.amada_fixture import amada_fixtura_largo_max_in as _max

        w = float(_max())
        if w > 1.0:
            return w
    except Exception:
        pass
    return float(AMADA_FIXTURA_LARGO_MAX_IN)


def amada_fixtura_elegir(largo_pieza_in: float) -> dict | None:
    """Fixtura más justa que aún cabe el largo (None si no cabe en ninguna)."""
    try:
        from modules.dxf_export.amada_fixture import elegir_fixtura_amada

        return elegir_fixtura_amada(float(largo_pieza_in))
    except Exception:
        return None


def _tol_ancho_mm() -> float:
    return float(TOL_ANCHO_IN_MIN) * 25.4


# Separación por defecto entre piezas en el eje del largo (solo cobre largos).
DEFAULT_SEPARACION_CU_IN = 0.375  # 3/8"
# Legacy: el umbral por largo ya no decide gap (siempre 3/8" salvo Z/especial).
# Se conserva la constante/API por compatibilidad de renesteo y tests.
LARGO_SIN_SEPARACION_CU_IN = 0.0
MAYORIA_BARRA_CU_FRACCION = 0.80  # legacy; ya no se usa para decidir gap
# Barras madre con menos de este avance en X se consideran "cola" para re-empaque conjunto.
UMBRAL_COLA_LONGITUD_CU_FRAC = 0.80
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


def _orientar_pieza_para_catalogo(
    poly,
    catalogo: List[dict],
) -> Optional[Tuple[object, float, float, dict, float, bool, float]]:
    """
    Conserva la orientación recibida desde PARTS y resuelve la tira compatible.

    La rotación manual de cobre se aplica antes, al leer el DXF. Rotar de nuevo
    aquí cambiaría 5" × 2" a 2" × 5" sin autorización y también rompería la
    paridad entre nesting, renesteos y workspaces .arganest.

    Devuelve (poly_orientado, len_mm, wid_mm, barra, corte_sup_mm, calibre_sup, rot_deg).
    """
    dims = _dims_pieza_nativo_mm(poly)
    if dims is None or not catalogo:
        return None
    lx, wy = dims
    barra, corte_sup, cal_sup = _resolver_barra_para_pieza(wy, catalogo)
    if barra is None:
        return None
    minx, miny, _, _ = poly.bounds
    poly_orientado = affinity.translate(poly, -minx, -miny)
    return (
        poly_orientado,
        float(lx),
        float(wy),
        barra,
        float(corte_sup),
        bool(cal_sup),
        0.0,
    )


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
    tol_mm = _tol_ancho_mm()
    estandares = _estandares_ancho_in(catalogo)
    if not estandares:
        return None, 0.0, False

    # Exacta: nominal ±TOL (2.0015" → 2", no 3").
    exactas = [
        b for b in catalogo
        if float(ancho_pieza_mm) <= float(b["ancho_mm"]) + tol_mm
        and abs(float(b["ancho_in"]) - a_in) <= TOL_ANCHO_IN_MIN
    ]
    if exactas:
        barra = min(exactas, key=lambda b: (b.get("precio_lb", 0.0), b.get("precio", 0.0)))
        return barra, 0.0, False

    fitting = [
        b for b in catalogo
        if float(b["ancho_mm"]) + tol_mm >= float(ancho_pieza_mm)
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
        "rot_deg": float(p_data.get("rot_deg", 0.0) or 0.0),
        "rot_origin_cx": float(p_data.get("rot_origin_cx", 0.0) or 0.0),
        "rot_origin_cy": float(p_data.get("rot_origin_cy", 0.0) or 0.0),
        "corte_superior_mm": float(corte_superior_mm),
        "calibre_superior": bool(calibre_superior),
        "cu_especial_vertical": bool(p_data.get("cu_especial_vertical")),
        "cu_forzar_sin_gap": bool(p_data.get("cu_forzar_sin_gap")),
        "cu_perfil_relieve": bool(
            p_data.get("cu_perfil_relieve", p_data.get("cu_forzar_sin_gap"))
        ),
        "y_corte_superior_mm": y_corte,
        "x_inicio_mm": float(x_mm),
        "largo_mm": float(p_data.get("len_mm") or 0.0),
        "largo_cu_in": _largo_pieza_cu_in(p_data),
        "cu_sin_separacion": _cu_sin_separacion_efectiva(
            p_data, str(p_data.get("cu_modo_separacion_barra") or "hibrido")
        ),
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


def _pieza_cu_exime_separacion(
    item: dict,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> bool:
    """Legacy no-op por largo. Ver _pieza_cu_forzar_sin_gap para exención real."""
    _ = item
    _ = largo_sin_separacion_in
    return False


def _exterior_item_cu(item: dict) -> list:
    """Contorno exterior en coords locales (poly o poligonos ya colocados)."""
    poly = item.get("poly")
    if poly is not None and not getattr(poly, "is_empty", True):
        try:
            return list(poly.exterior.coords)
        except Exception:
            pass
    polys = item.get("poligonos") or []
    if polys and polys[0]:
        return list(polys[0])
    return []


def _pieza_cu_es_relieve_z(item: dict) -> bool:
    """True solo para perfil tipo Z / escalón / diagonal (no rectángulo ortogonal).

    El simple recorte de orilla (pieza rectangular más angosta que la tira) NO cuenta:
    esas pueden usar gap normal y entrar a RTZCU.
    """
    exterior = _exterior_item_cu(item)
    if not exterior:
        return False
    return not _solo_cortes_guillotina_vertical(exterior)


def _pieza_cu_es_especial_vertical(item: dict) -> bool:
    """True si PARTS marcó la pieza para corte vertical sin gap (VERTICAL + AMADA)."""
    return bool(item.get("cu_especial_vertical"))


def _pieza_cu_forzar_sin_gap(item: dict) -> bool:
    """
    True si la pieza fuerza sin_gap y queda excluida de RTZCU:
    - perfiles con relieve/escalón/Z (auto), o
    - piezas marcadas especiales en PARTS (cu_especial_vertical).

    Con cu_force_dxf_step (Config Global) nunca fuerza sin_gap.
    """
    try:
        from .nest_runtime_prefs import is_cu_force_dxf_step_enabled

        if is_cu_force_dxf_step_enabled():
            return False
    except Exception:
        pass
    if _pieza_cu_es_especial_vertical(item):
        return True
    exterior = _exterior_item_cu(item)
    if exterior:
        return _pieza_cu_es_relieve_z(item)
    if bool(item.get("cu_forzar_sin_gap")):
        return True
    return False


def _marcar_forzar_sin_gap(item: dict) -> dict:
    """Fija cu_forzar_sin_gap, cu_perfil_relieve y conserva cu_especial_vertical."""
    out = dict(item)
    try:
        from .nest_runtime_prefs import is_cu_force_dxf_step_enabled

        if is_cu_force_dxf_step_enabled():
            especial = _pieza_cu_es_especial_vertical(item)
            exterior = _exterior_item_cu(item)
            relieve = (
                _pieza_cu_es_relieve_z(item)
                if exterior
                else bool(item.get("cu_perfil_relieve"))
            )
            out["cu_especial_vertical"] = bool(especial)
            out["cu_perfil_relieve"] = bool(relieve)
            out["cu_forzar_sin_gap"] = False
            out["cu_force_dxf_step"] = True
            return out
    except Exception:
        pass
    especial = _pieza_cu_es_especial_vertical(item)
    exterior = _exterior_item_cu(item)
    relieve = _pieza_cu_es_relieve_z(item) if exterior else bool(item.get("cu_perfil_relieve"))
    flag = bool(especial or relieve)
    out["cu_especial_vertical"] = bool(especial)
    out["cu_perfil_relieve"] = bool(relieve)
    out["cu_forzar_sin_gap"] = flag
    return out


def _familia_corte_cu(item: dict) -> str:
    """Familias de corte: Amada (PARTS), relieve/Z, o guillotina rectangular.

    Amada y Z NUNCA comparten barra: la fixtura Amada es solo rectangular.
    Con cu_force_dxf_step todas van a guillotina (gap + STEP).
    """
    try:
        from .nest_runtime_prefs import is_cu_force_dxf_step_enabled

        if is_cu_force_dxf_step_enabled():
            return "guillotina"
    except Exception:
        pass
    if _pieza_cu_es_especial_vertical(item):
        return "amada"
    if _pieza_cu_es_relieve_z(item) or (
        bool(item.get("cu_perfil_relieve")) and not _exterior_item_cu(item)
    ):
        return "sin_gap_laser"
    if _pieza_cu_forzar_sin_gap(item) and not _pieza_cu_es_especial_vertical(item):
        # Legacy forzar sin especial → tratar como Z.
        return "sin_gap_laser"
    return "guillotina"


def _barra_acepta_familia_corte(barra: dict, item: dict) -> bool:
    """Amada sola; Z sola; rectángulares pueden rellenar cola RTZ de Amada o de Z.

    Nunca se mezclan piezas Amada (PARTS) con relieve/Z en la misma barra.
    """
    colocados = barra.get("colocados") or []
    if not colocados:
        return True
    fam_item = _familia_corte_cu(item)
    fams = {_familia_corte_cu(c[0]) for c in colocados}

    if fam_item == "amada":
        # Solo barras Amada puras (aún sin cola rectangular).
        return fams == {"amada"}
    if fam_item == "sin_gap_laser":
        # Solo barras Z puras.
        return fams == {"sin_gap_laser"}

    # guillotina rectangular: sola, o cola RTZ de Amada XOR de Z.
    if "amada" in fams and "sin_gap_laser" in fams:
        return False
    if fams == {"guillotina"}:
        return True
    if fams <= {"amada", "guillotina"}:
        return True
    if fams <= {"sin_gap_laser", "guillotina"}:
        return True
    return False


def _barra_tiene_relieve_cu(colocados: List[tuple] | None) -> bool:
    """True si la barra tiene piezas sin_gap forzado (Z o Amada) → cola a RTZCU."""
    return any(_pieza_cu_forzar_sin_gap(c[0]) for c in (colocados or []))


def _gap_mm_entre_piezas_cu(
    modo: str,
    prev_item: dict | None,
    next_item: dict | None,
    separacion_in: float,
    *,
    tiene_relieve: bool,
) -> float:
    """Gap entre piezas; en barra Z/Amada la cola ortogonal siempre lleva separación."""
    if prev_item is None or next_item is None:
        return 0.0
    if tiene_relieve:
        prev_sg = _pieza_cu_forzar_sin_gap(prev_item)
        next_sg = _pieza_cu_forzar_sin_gap(next_item)
        # Misma familia sin_gap (Amada↔Amada o Z↔Z): pegadas.
        if prev_sg and next_sg:
            return 0.0
        # sin_gap → rectangular u orilla→orilla: gap (van / siguen en RTZCU).
        return max(0.0, float(separacion_in or 0.0)) * 25.4
    return _gap_mm_segun_modo(modo, prev_item, next_item, separacion_in)


def _cu_sin_separacion_efectiva(item: dict, modo_barra: str) -> bool:
    modo = str(modo_barra or "hibrido").strip().lower()
    return modo == "sin_gap"


def _modo_separacion_barra(
    items: List[dict],
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> str:
    """
    Gap siempre (con_gap) salvo que la barra tenga relieve/Z o pieza especial PARTS
    → sin_gap en madre (≤114\") y compañeros ortogonales a RTZCU con gap.
    El parámetro largo_sin_separacion_in se ignora (API legacy).

    Con cu_force_dxf_step (Config Global) siempre con_gap.
    """
    _ = largo_sin_separacion_in
    try:
        from .nest_runtime_prefs import is_cu_force_dxf_step_enabled

        if is_cu_force_dxf_step_enabled():
            return "con_gap"
    except Exception:
        pass
    if not items:
        return "con_gap"
    if any(_pieza_cu_forzar_sin_gap(it) for it in items):
        return "sin_gap"
    return "con_gap"


def _gap_mm_segun_modo(
    modo: str,
    prev_item: dict | None,
    next_item: dict | None,
    separacion_in: float,
) -> float:
    if prev_item is None or next_item is None:
        return 0.0
    modo_n = str(modo or "hibrido").strip().lower()
    if modo_n == "sin_gap":
        return 0.0
    # hibrido y con_gap: separación entre todas las piezas consecutivas.
    return max(0.0, float(separacion_in or 0.0)) * 25.4


def _categoria_pieza_cu(
    item: dict,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> str:
    return (
        "corta"
        if _pieza_cu_exime_separacion(item, largo_sin_separacion_in)
        else "larga"
    )


def _categoria_barra_abierta(
    colocados: List[tuple],
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> str | None:
    if not colocados:
        return None
    cats = {_categoria_pieza_cu(c[0], largo_sin_separacion_in) for c in colocados}
    if len(cats) == 1:
        return next(iter(cats))
    return "mixta"


def _simular_encaje_en_barra(
    barra: dict,
    item: dict,
    separacion_in: float,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> tuple[bool, float, float, float]:
    """Prueba colocación con el mismo recálculo final de posiciones/gaps."""
    trial = list(barra["colocados"]) + [(item, 0.0, 0.0)]
    nuevos, cursor, _ = _recalcular_colocados_barra(
        trial,
        separacion_in=separacion_in,
        largo_mm=float(barra["largo_mm"]),
        largo_sin_separacion_in=largo_sin_separacion_in,
    )
    if cursor > float(barra["largo_mm"]) + 0.5:
        return False, 0.0, 0.0, cursor
    x_pos = float(nuevos[-1][1])
    if len(nuevos) < 2:
        gap_before = 0.0
    else:
        prev_item, prev_x, _ = nuevos[-2]
        gap_before = x_pos - (prev_x + float(prev_item["len_mm"]))
    # Relieve/Z: solo dentro de zona máquina (114"); no empujar a RTZCU.
    # Orilla rectangular SÍ puede pasar de 114\" hacia RTZCU.
    if _pieza_cu_forzar_sin_gap(item):
        from .cu_rtz_sin_gap import rtz_zona_inicio_mm

        x_end = x_pos + float(item.get("len_mm") or 0.0)
        if x_end > float(rtz_zona_inicio_mm()) + 0.5:
            return False, 0.0, 0.0, cursor
    return True, gap_before, x_pos, cursor


def _encaje_en_barra(
    barra: dict,
    item: dict,
    separacion_in: float,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> tuple[bool, float, float]:
    ok, gap_before, x_pos, _cursor = _simular_encaje_en_barra(
        barra, item, separacion_in, largo_sin_separacion_in
    )
    if not ok:
        return False, 0.0, 0.0
    return True, gap_before, x_pos


def _ancho_objetivo_item_in(item: dict) -> float:
    """Ancho de tira resuelto para la pieza (catálogo), no el Y del DXF."""
    try:
        objetivo = float(item.get("barra_objetivo_in") or 0.0)
    except (TypeError, ValueError):
        objetivo = 0.0
    if objetivo > 0.0:
        return objetivo
    try:
        return float(item.get("wid_mm") or 0.0) / 25.4
    except (TypeError, ValueError):
        return 0.0


def _pieza_cabe_en_ancho_barra(item: dict, barra: dict) -> bool:
    """True solo si la tira es el ancho objetivo de la pieza.

    No permite subir 4\"/5\" a 6\" porque el lote ya abrió una tira más ancha.
    El salto de calibre (ej. 5.25\" → 6\") ya quedó fijado en barra_objetivo_in
    al resolver contra el catálogo.
    """
    try:
        wid = float(item.get("wid_mm") or 0.0)
        ancho_b = float(barra.get("ancho_mm") or 0.0)
        if ancho_b + _tol_ancho_mm() < wid:
            return False
        return abs(float(barra.get("ancho_in") or 0.0) - _ancho_objetivo_item_in(item)) <= TOL_ANCHO_IN_MIN
    except (TypeError, ValueError):
        return False


def _adaptar_item_a_barra(item: dict, barra: dict) -> dict:
    """Ajusta corte superior / calibre cuando la pieza es más angosta que su tira objetivo."""
    out = dict(item)
    ancho_b = float(barra.get("ancho_mm") or 0.0)
    wid = float(item.get("wid_mm") or 0.0)
    corte = max(0.0, ancho_b - wid)
    out["corte_superior_mm"] = corte
    out["calibre_superior"] = corte > 0.5
    # Conserva el objetivo original; la tira debe coincidir con él.
    out["barra_objetivo_in"] = _ancho_objetivo_item_in(item) or float(
        barra.get("ancho_in") or 0.0
    )
    return _marcar_forzar_sin_gap(out)


def _score_barra_para_item(
    barra: dict,
    item: dict,
    separacion_in: float,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> float | None:
    """
    Mayor puntaje = preferir rellenar barras ya abiertas del mismo ancho objetivo.
    Luego homogeneidad corta/larga. Familias laser/guillotina no se mezclan.
    """
    if not _pieza_cabe_en_ancho_barra(item, barra):
        return None
    if not _barra_acepta_familia_corte(barra, item):
        return None
    item_eff = _adaptar_item_a_barra(item, barra)
    cabe, gap_before, x_pos = _encaje_en_barra(
        barra, item_eff, separacion_in, largo_sin_separacion_in
    )
    if not cabe:
        return None

    cat_barra = _categoria_barra_abierta(barra["colocados"], largo_sin_separacion_in)
    cat_item = _categoria_pieza_cu(item_eff, largo_sin_separacion_in)
    # Rellenar barra abierta del mismo ancho siempre gana frente a abrir otra tira.
    if cat_barra is None:
        score = 500.0
    elif cat_barra == cat_item:
        score = 2000.0
    elif cat_barra == "mixta":
        score = 800.0
    else:
        score = 50.0

    # Preferir llenar cola RTZCU de barras relieve/Z antes que abrir tira nueva.
    if _barra_tiene_relieve_cu(barra.get("colocados")) and not _pieza_cu_forzar_sin_gap(
        item_eff
    ):
        score += 3000.0

    restante = float(barra["largo_mm"]) - (x_pos + float(item_eff["len_mm"]))
    score -= restante * 0.002
    score -= gap_before * 0.001
    return score


def _aplicar_pieza_en_barra(
    barra: dict,
    item: dict,
    separacion_in: float,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> bool:
    if not _pieza_cabe_en_ancho_barra(item, barra):
        return False
    if not _barra_acepta_familia_corte(barra, item):
        return False
    item_eff = _adaptar_item_a_barra(item, barra)
    ok, _gap, _x, cursor = _simular_encaje_en_barra(
        barra, item_eff, separacion_in, largo_sin_separacion_in
    )
    if not ok:
        return False
    trial = list(barra["colocados"]) + [(item_eff, 0.0, 0.0)]
    nuevos, cursor, modo = _recalcular_colocados_barra(
        trial,
        separacion_in=separacion_in,
        largo_mm=float(barra["largo_mm"]),
        largo_sin_separacion_in=largo_sin_separacion_in,
    )
    barra["colocados"] = nuevos
    barra["cursor_x"] = cursor
    barra["cu_modo_separacion"] = modo
    return True


def _gap_proyectado_en_barra(
    colocados: List[tuple],
    next_item: dict,
    separacion_in: float,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> float:
    """Gap antes de colocar next_item según modo proyectado de la barra (+ pieza nueva)."""
    if not colocados:
        return 0.0
    items = [c[0] for c in colocados] + [next_item]
    modo = _modo_separacion_barra(items, largo_sin_separacion_in)
    return _gap_mm_segun_modo(modo, colocados[-1][0], next_item, separacion_in)


def _recalcular_colocados_barra(
    colocados: List[tuple],
    *,
    separacion_in: float,
    largo_mm: float,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> Tuple[List[tuple], float, str]:
    """Recalcula posiciones X: gap fijo 3/8" salvo Z/especial (sin_gap madre).

    La barra madre conserva su modo (sin_gap / con_gap). Si es sin_gap y rebasa
    114\", el tramo sobrante se separa como RTZCU con gap por defecto.
    """
    if not colocados:
        return colocados, 0.0, "con_gap"

    items = [c[0] for c in colocados]
    modo = _modo_separacion_barra(items, largo_sin_separacion_in)
    tiene_relieve = any(_pieza_cu_forzar_sin_gap(it) for it in items)
    nuevos: List[tuple] = []
    cursor = 0.0
    for i, item in enumerate(items):
        if i == 0:
            item_mod = {**item, "cu_modo_separacion_barra": modo}
            nuevos.append((item_mod, 0.0, 0.0))
            cursor = float(item["len_mm"])
            continue
        gap = _gap_mm_entre_piezas_cu(
            modo,
            items[i - 1],
            item,
            separacion_in,
            tiene_relieve=tiene_relieve,
        )
        x_pos = cursor + gap
        item_mod = {**item, "cu_modo_separacion_barra": modo}
        nuevos.append((item_mod, x_pos, 0.0))
        cursor = x_pos + float(item["len_mm"])
    return nuevos, cursor, modo


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


def _elegir_candidato_barra(candidatos: dict) -> tuple | None:
    for bucket in ("homogenea", "mixta", "vacia", "contamina"):
        cand = candidatos.get(bucket)
        if cand is not None:
            return cand
    return None


def _consolidar_barras_con_pieza_sola(
    barras_abiertas: List[dict],
    *,
    separacion_in: float,
    largo_sin_separacion_in: float,
    max_uso_frac: float = 0.20,
) -> None:
    """Reubica piezas solas en otra barra del mismo ancho objetivo donde quepan."""
    cambiado = True
    while cambiado:
        cambiado = False
        for i, barra in enumerate(list(barras_abiertas)):
            colocados = barra.get("colocados") or []
            if len(colocados) != 1:
                continue
            item = colocados[0][0]
            largo_mm = float(barra.get("largo_mm") or 0.0)
            if largo_mm <= 0:
                continue
            uso_frac = float(item.get("len_mm") or 0.0) / largo_mm
            if uso_frac > max_uso_frac:
                continue
            for j, dest in enumerate(barras_abiertas):
                if j == i:
                    continue
                if not dest.get("colocados"):
                    continue
                if not _pieza_cabe_en_ancho_barra(item, dest):
                    continue
                if not _barra_acepta_familia_corte(dest, item):
                    continue
                item_eff = _adaptar_item_a_barra(item, dest)
                ok, _, _, _ = _simular_encaje_en_barra(
                    dest, item_eff, separacion_in, largo_sin_separacion_in
                )
                if not ok:
                    continue
                if _aplicar_pieza_en_barra(
                    dest, item, separacion_in, largo_sin_separacion_in
                ):
                    barra["colocados"] = []
                    barra["cursor_x"] = 0.0
                    cambiado = True
                    break
            if cambiado:
                break
        barras_abiertas[:] = [b for b in barras_abiertas if b.get("colocados")]


def empaquetar_largos_cu(
    piezas: List[dict],
    placas_ok: List[dict],
    *,
    separacion_in: float = DEFAULT_SEPARACION_CU_IN,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> Tuple[List[dict], List[dict]]:
    catalogo = _normalizar_barras(placas_ok)
    if not catalogo:
        return [], list(piezas or [])

    largo_umbral = max(0.0, float(largo_sin_separacion_in or LARGO_SIN_SEPARACION_CU_IN))
    items = []
    sin_colocar: List[dict] = []
    for p in piezas or []:
        oriented = _orientar_pieza_para_catalogo(p.get("poly"), catalogo)
        if oriented is None:
            sin_colocar.append(copy.deepcopy(p))
            continue
        poly_o, largo_x, ancho_y, barra, corte_sup, cal_sup, rot_deg = oriented
        item = {
            **copy.deepcopy(p),
            "poly": poly_o,
            "len_mm": largo_x,
            "wid_mm": ancho_y,
            "ancho_in": ancho_y / 25.4,
            "barra_objetivo_in": float(barra["ancho_in"]),
            "corte_superior_mm": corte_sup,
            "calibre_superior": cal_sup,
            "rot_deg": float(rot_deg),
        }
        item = _marcar_forzar_sin_gap(item)
        # Marcas: rotar igual que el contorno si aplica.
        marks = p.get("marks")
        if marks is not None and not getattr(marks, "is_empty", True) and rot_deg:
            marks_r = affinity.rotate(marks, 90.0, origin="centroid", use_radians=False)
            minx, miny, _, _ = poly_o.bounds
            # poly_o ya está en origen; alinear marks al mismo origen del poly rotado crudo.
            # Recalcular desde poly original rotado:
            poly_raw = affinity.rotate(p.get("poly"), 90.0, origin="centroid", use_radians=False)
            mx0, my0, _, _ = poly_raw.bounds
            item["marks"] = affinity.translate(marks_r, -mx0, -my0)
        items.append(item)

    # Agrupa por ancho; Amada y Z no se mezclan (familias aparte); largas primero.
    def _prio_familia(x: dict) -> int:
        fam = _familia_corte_cu(x)
        if fam == "amada":
            return 0
        if fam == "sin_gap_laser":
            return 1
        return 2

    items.sort(
        key=lambda x: (
            float(x["barra_objetivo_in"]),
            _prio_familia(x),
            -x["len_mm"],
        )
    )

    barras_abiertas: List[dict] = []

    for item in items:
        candidatos: dict[str, tuple[dict, float, float, float]] = {}

        for barra in barras_abiertas:
            if not _pieza_cabe_en_ancho_barra(item, barra):
                continue
            score = _score_barra_para_item(
                barra, item, separacion_in, largo_umbral
            )
            if score is None:
                continue
            item_eff = _adaptar_item_a_barra(item, barra)
            cabe, gap_before, x_pos = _encaje_en_barra(
                barra, item_eff, separacion_in, largo_umbral
            )
            if not cabe:
                continue
            if score >= 1500.0:
                bucket = "homogenea"
            elif score >= 500.0:
                bucket = "vacia"
            elif score >= 100.0:
                bucket = "mixta"
            else:
                bucket = "contamina"
            prev = candidatos.get(bucket)
            if prev is None or score > prev[3]:
                candidatos[bucket] = (barra, gap_before, x_pos, score)

        elegido = _elegir_candidato_barra(candidatos)

        # Siempre abrir tira del ancho objetivo de la pieza (no heredar 6" del lote).
        stock = next(
            (
                b
                for b in catalogo
                if abs(float(b["ancho_in"]) - float(item["barra_objetivo_in"]))
                <= TOL_ANCHO_IN_MIN
            ),
            None,
        )

        if elegido is not None:
            barra, _gap_before, _x_pos, _score = elegido
            if _aplicar_pieza_en_barra(barra, item, separacion_in, largo_umbral):
                continue

        if stock is not None:
            item0 = _adaptar_item_a_barra(item, stock)
            barras_abiertas.append(
                {
                    "stock": stock,
                    "largo_mm": stock["largo_mm"],
                    "ancho_mm": stock["ancho_mm"],
                    "ancho_in": stock["ancho_in"],
                    "cursor_x": item0["len_mm"],
                    "colocados": [(item0, 0.0, 0.0)],
                    "cu_modo_separacion": _modo_separacion_barra([item0], largo_umbral),
                }
            )
            continue

        if item not in sin_colocar:
            sin_colocar.append(item)

    _consolidar_barras_con_pieza_sola(
        barras_abiertas,
        separacion_in=separacion_in,
        largo_sin_separacion_in=largo_umbral,
    )

    hojas: List[dict] = []
    for barra in barras_abiertas:
        colocados, cursor_x, modo_barra = _recalcular_colocados_barra(
            barra["colocados"],
            separacion_in=separacion_in,
            largo_mm=float(barra["largo_mm"]),
            largo_sin_separacion_in=largo_umbral,
        )
        barra["colocados"] = colocados
        barra["cursor_x"] = cursor_x
        barra["cu_modo_separacion"] = modo_barra

        if cursor_x > float(barra["largo_mm"]) + 0.5:
            sin_colocar.extend(copy.deepcopy(c[0]) for c in colocados)
            continue

        stock = barra["stock"]
        piezas_hoja = []
        area_usada = 0.0
        requiere_corte_sup = False
        tiene_relieve_barra = _barra_tiene_relieve_cu(barra.get("colocados"))
        tiene_especial_barra = any(
            _pieza_cu_es_especial_vertical(c[0]) for c in (barra.get("colocados") or [])
        )

        piezas_reales: List[dict] = []
        for p_data, x_mm, y_mm in barra["colocados"]:
            p_final = _colocar_pieza_nativa(
                p_data,
                x_mm,
                y_mm,
                corte_superior_mm=float(p_data.get("corte_superior_mm") or 0.0),
                calibre_superior=bool(p_data.get("calibre_superior")),
            )
            # Orilla/vertical en cola de barra Z/especial → RTZCU (no DXF madre).
            if tiene_relieve_barra and not _pieza_cu_forzar_sin_gap(p_data):
                p_final["cu_zona_rtz"] = True
                p_final["cu_sin_separacion"] = False
                p_final["cu_modo_separacion_barra"] = "con_gap"
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
                "largo_sin_separacion_cu_in": largo_umbral,
                "mayoria_barra_cu_frac": MAYORIA_BARRA_CU_FRACCION,
                "cu_modo_separacion_barra": barra.get("cu_modo_separacion", "con_gap"),
                "cu_barra_especial": bool(tiene_especial_barra),
                "requiere_corte_superior": requiere_corte_sup,
                "ignorar_deduccion": True,
            }
        )

    return hojas, sin_colocar


def _es_hoja_madre_cu(hoja: dict) -> bool:
    return (
        isinstance(hoja, dict)
        and hoja.get("modo_largos_cu")
        and not hoja.get("es_retazo")
        and not hoja.get("cu_rtz_virtual")
    )


def ordenar_hojas_largos_cu_por_ancho(hojas: List[dict] | None) -> List[dict]:
    """Ordena barras madre de cobre de menor a mayor ancho (1.75\" → 6\").

    Las hojas virtuales RTZCU quedan inmediatamente después de su madre.
    """
    if not hojas:
        return list(hojas or [])

    madres: List[dict] = []
    virtuales: List[dict] = []
    otros: List[dict] = []
    for h in hojas:
        if not isinstance(h, dict):
            otros.append(h)
            continue
        if h.get("cu_rtz_virtual"):
            virtuales.append(h)
            continue
        if h.get("modo_largos_cu") and not h.get("es_retazo"):
            madres.append(h)
            continue
        otros.append(h)

    madres.sort(
        key=lambda h: (
            float(h.get("placa_h") or 0.0),
            str(h.get("placa_id") or ""),
            -float(h.get("area_usada") or 0.0),
        )
    )

    by_uid: Dict[object, List[dict]] = {}
    by_seq: Dict[object, List[dict]] = {}
    huerfanos: List[dict] = []
    for v in virtuales:
        uid = v.get("madre_sheet_uid")
        seq = v.get("sheet_seq")
        if uid is not None:
            by_uid.setdefault(uid, []).append(v)
        elif seq is not None:
            by_seq.setdefault(seq, []).append(v)
        else:
            huerfanos.append(v)

    out: List[dict] = []
    usados: set[int] = set()
    for h in madres:
        out.append(h)
        for grupo in (
            by_uid.get(h.get("sheet_uid")) or [],
            by_seq.get(h.get("sheet_seq")) or [],
        ):
            for v in grupo:
                vid = id(v)
                if vid in usados:
                    continue
                out.append(v)
                usados.add(vid)
    for v in virtuales:
        if id(v) not in usados:
            out.append(v)
    out.extend(otros)
    return out


def _uso_longitudinal_hoja_cu(hoja: dict) -> float:
    """Fracción de placa_w usada por piezas reales (eje X)."""
    largo = float(hoja.get("placa_w") or 0.0)
    if largo <= 0.5:
        return 1.0
    fin = 0.0
    from .sheet_integrity import piezas_reales_en_hoja

    for p in piezas_reales_en_hoja(hoja):
        pols = (p or {}).get("poligonos") or []
        if not pols or not pols[0]:
            continue
        xs = [float(t[0]) for t in pols[0]]
        if xs:
            fin = max(fin, max(xs))
    return min(1.0, fin / largo)


def _pack_piezas_reales_hoja_cu(hoja: dict) -> List[dict]:
    from .cu_rtz_sin_gap import colocada_a_pack_cu
    from .sheet_integrity import piezas_reales_en_hoja

    out: List[dict] = []
    for p in piezas_reales_en_hoja(hoja):
        pp = colocada_a_pack_cu(p)
        if pp is not None:
            out.append(pp)
    return out


def _familia_hoja_madre_cu(hoja: dict) -> str:
    """Familia dominante de una barra madre para no fusionar Amada con Z/normal."""
    from .sheet_integrity import piezas_reales_en_hoja

    fams = set()
    for p in piezas_reales_en_hoja(hoja):
        if bool(p.get("cu_especial_vertical")):
            fams.add("amada")
        elif bool(p.get("cu_perfil_relieve")) or (
            bool(p.get("cu_forzar_sin_gap")) and not bool(p.get("cu_especial_vertical"))
        ):
            fams.add("sin_gap_laser")
        else:
            fams.add("guillotina")
    if not fams:
        return "guillotina"
    if "amada" in fams:
        return "amada"
    if "sin_gap_laser" in fams:
        return "sin_gap_laser"
    return "guillotina"


def consolidar_barras_cu_baja_ocupacion(
    hojas: List[dict],
    placas_ok: List[dict],
    *,
    separacion_in: float = DEFAULT_SEPARACION_CU_IN,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
    umbral_long_frac: float = UMBRAL_COLA_LONGITUD_CU_FRAC,
    dbg_fn: Optional[Callable[[str], None]] = None,
    max_pasadas: int = 12,
) -> List[dict]:
    """Re-empaqueta juntas varias barras madre del mismo ancho con poco avance en X.

    Solo fusiona barras de la misma familia (Amada / Z / rectangular). Así la
    cola post-RTZ no mezcla fixtura Amada con relieve/Z.
    """
    _log = dbg_fn or (lambda _msg: None)
    out = list(hojas or [])
    sep_in = max(0.0, float(separacion_in if separacion_in is not None else DEFAULT_SEPARACION_CU_IN))
    largo_umbral = max(
        0.0,
        float(
            largo_sin_separacion_in
            if largo_sin_separacion_in is not None
            else LARGO_SIN_SEPARACION_CU_IN
        ),
    )
    umbral = max(0.35, min(0.95, float(umbral_long_frac)))

    for pasada in range(max(1, int(max_pasadas or 1))):
        # clave = (ancho_mm_redondeado, familia)
        por_grupo: Dict[tuple, List[tuple[int, dict, float]]] = {}
        for i, h in enumerate(out):
            if not _es_hoja_madre_cu(h):
                continue
            uso = _uso_longitudinal_hoja_cu(h)
            key = (
                round(float(h.get("placa_h") or 0.0), 1),
                _familia_hoja_madre_cu(h),
            )
            por_grupo.setdefault(key, []).append((i, h, uso))

        fusion_hecha = False
        for (ancho_key, fam_key), grupo in por_grupo.items():
            bajas = [(i, h, u) for i, h, u in grupo if u < umbral - 1e-6]
            if len(bajas) < 2:
                continue

            pack: List[dict] = []
            quitar: set[int] = set()
            for i, h, _u in sorted(bajas, key=lambda t: t[0]):
                quitar.add(i)
                pack.extend(_pack_piezas_reales_hoja_cu(h))
            if len(pack) < 2:
                continue

            nuevas, sin = empaquetar_largos_cu(
                pack,
                placas_ok,
                separacion_in=sep_in,
                largo_sin_separacion_in=largo_umbral,
            )
            if sin or not nuevas:
                continue
            if len(nuevas) >= len(quitar):
                # Sin mejora en número de barras: solo aceptar si sube el mínimo uso longitudinal.
                uso_viejos = [u for _i, _h, u in bajas]
                uso_nuevos = [_uso_longitudinal_hoja_cu(h) for h in nuevas if _es_hoja_madre_cu(h)]
                if not uso_nuevos:
                    continue
                if min(uso_nuevos) <= min(uso_viejos) + 0.02:
                    continue

            _log(
                f"[CU-LARGOS][COLA] pasada={pasada + 1} | ancho={ancho_key:.1f}mm | "
                f"fam={fam_key} | barras {len(quitar)} → {len(nuevas)} | piezas={len(pack)}"
            )
            out = [h for k, h in enumerate(out) if k not in quitar]
            out.extend(nuevas)
            fusion_hecha = True
            break

        if not fusion_hecha:
            break
    return out


def resolver_derrames_rtz_cu(
    hojas: List[dict],
    placas_ok: List[dict],
    *,
    separacion_in: float = DEFAULT_SEPARACION_CU_IN,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
    dbg_fn: Optional[Callable[[str], None]] = None,
    max_iter: int = 24,
) -> Tuple[List[dict], List[dict]]:
    """Si el RTZCU con gap no cabe, derrama sobrantes a barra(s) nueva(s).

    Las barras nuevas se empaquetan con la misma lógica LARGOS CU y pueden
    volver a generar RTZCU (bucle hasta estabilizar).
    """
    from .cu_rtz_sin_gap import aplicar_rtz_sin_gap_a_hoja_con_derrame

    _log = dbg_fn or (lambda _msg: None)
    out = list(hojas or [])
    sin_colocar: List[dict] = []
    sep_in = max(0.0, float(separacion_in if separacion_in is not None else DEFAULT_SEPARACION_CU_IN))
    largo_umbral = max(
        0.0,
        float(
            largo_sin_separacion_in
            if largo_sin_separacion_in is not None
            else LARGO_SIN_SEPARACION_CU_IN
        ),
    )

    for it in range(max(1, int(max_iter or 1))):
        derrame: List[dict] = []
        for h in out:
            if not isinstance(h, dict):
                continue
            if h.get("es_retazo") or h.get("cu_rtz_virtual"):
                continue
            if not h.get("modo_largos_cu"):
                continue
            _activo, overflow = aplicar_rtz_sin_gap_a_hoja_con_derrame(h)
            if overflow:
                derrame.extend(overflow)

        if not derrame:
            if it > 0:
                _log(f"[CU-LARGOS][RTZ-DERRAME] estable tras {it} pasada(s)")
            break

        _log(
            f"[CU-LARGOS][RTZ-DERRAME] pasada={it + 1} | "
            f"piezas={len(derrame)} → empaquetar barras nuevas"
        )
        nuevas, sin = empaquetar_largos_cu(
            derrame,
            placas_ok,
            separacion_in=sep_in,
            largo_sin_separacion_in=largo_umbral,
        )
        if nuevas:
            out.extend(nuevas)
        if sin:
            sin_colocar.extend(sin)
            _log(f"[CU-LARGOS][RTZ-DERRAME] sin colocar tras derrame: {len(sin)}")
            break
    return out, sin_colocar


def procesar_grupo_largos_cu(
    clave: str,
    piezas: List[dict],
    placas_ok: List[dict],
    wo_name: str = "PENDIENTE",
    dbg_fn: Optional[Callable[[str], None]] = None,
    exigir_colocacion_total: bool = False,
    separacion_in: float = DEFAULT_SEPARACION_CU_IN,
    largo_sin_separacion_in: float = LARGO_SIN_SEPARACION_CU_IN,
) -> Tuple[str, dict]:
    _log = dbg_fn or (lambda _msg: None)
    try:
        from .nest_runtime_prefs import is_cu_force_dxf_step_enabled

        _cu_force = bool(is_cu_force_dxf_step_enabled())
    except Exception:
        _cu_force = False
    sep_in = max(0.0, float(separacion_in if separacion_in is not None else DEFAULT_SEPARACION_CU_IN))
    largo_umbral = max(
        0.0,
        float(
            largo_sin_separacion_in
            if largo_sin_separacion_in is not None
            else LARGO_SIN_SEPARACION_CU_IN
        ),
    )
    _log(
        f"[CU-LARGOS] clave={clave} | piezas={len(piezas)} | wo={wo_name} | "
        f"separacion={sep_in:.4f}in | largo_sin_gap={largo_umbral:.2f}in"
        f"{' | FORCE_DXF_STEP=1' if _cu_force else ''}"
    )

    if not placas_ok:
        return clave, {"error": "Sin inventario CU disponible para largos."}

    hojas, sin_colocar = empaquetar_largos_cu(
        piezas,
        placas_ok,
        separacion_in=sep_in,
        largo_sin_separacion_in=largo_umbral,
    )
    # RTZCU con gap: lo que no cabe en el tramo → barras nuevas (pueden generar otro RTZCU).
    hojas, sin_derrame = resolver_derrames_rtz_cu(
        hojas,
        placas_ok,
        separacion_in=sep_in,
        largo_sin_separacion_in=largo_umbral,
        dbg_fn=_log,
    )
    hojas = consolidar_barras_cu_baja_ocupacion(
        hojas,
        placas_ok,
        separacion_in=sep_in,
        largo_sin_separacion_in=largo_umbral,
        dbg_fn=_log,
    )
    hojas, sin_derrame2 = resolver_derrames_rtz_cu(
        hojas,
        placas_ok,
        separacion_in=sep_in,
        largo_sin_separacion_in=largo_umbral,
        dbg_fn=_log,
    )
    if sin_derrame2:
        sin_derrame = list(sin_derrame or []) + list(sin_derrame2)
    if sin_derrame:
        sin_colocar = list(sin_colocar or []) + list(sin_derrame)
    hojas = ordenar_hojas_largos_cu_por_ancho(hojas)
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
        "largo_sin_separacion_cu_in": largo_umbral,
        "ignorar_deduccion_cu": True,
        **efi_grupo,
    }
    if sin_colocar:
        resultado["piezas_pendientes"] = [str(p.get("nombre", "")) for p in sin_colocar]
        resultado["advertencia"] = (
            f"{len(sin_colocar)} pieza(s) CU sin barra compatible (ancho/largo)."
        )

    return clave, resultado
