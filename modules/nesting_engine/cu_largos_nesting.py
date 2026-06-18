"""
Nesting 1D para largos de cobre (CU).

- Orientación nativa del DXF (sin rotar).
- Eje X = avance a lo largo del bar; eje Y = ancho (empalme con inventario).
- Sin tolerancia: si el ancho excede la tira exacta, sube a la tira más ancha siguiente.
- Export DXF: CUT_OUTER = programación láser (divisores + relieves); CUT_CU = contorno STEP.

Cortes CUT_OUTER (láser):
  1. Pieza rectangular a ancho exacto de tira (ej. 4" en barra 4"): solo guillotinas verticales.
  2. Pieza más angosta que la tira disponible (ej. 4.2" o 4.5" en barra 5"): barra superior +
     línea horizontal CUT_OUTER (CU_CORTE__SUP__) a la altura del ancho real de la pieza.
  3. Pieza con relieve en la frontera entre rebanadas: escalón/diagonal local junto al corte
     vertical (no el techo largo del stock que atravesaría toda la pieza).
"""
from __future__ import annotations

import copy
from typing import Callable, Dict, List, Optional, Tuple

from shapely.geometry import LineString
from shapely import affinity

from .efficiency_metrics import calcular_eficiencias_grupo

TOL_ANCHO_IN_MIN = 0.02
TOL_GEOM_MM = 0.15
PREFIJO_CORTE_CU = "CU_CORTE__"
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
        "rot_deg": 0.0,
        "shift_x": 0.0,
        "shift_y": 0.0,
        "corte_superior_mm": float(corte_superior_mm),
        "calibre_superior": bool(calibre_superior),
        "y_corte_superior_mm": y_corte,
        "x_inicio_mm": float(x_mm),
        "largo_mm": float(p_data.get("len_mm") or 0.0),
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
    if _es_rectangulo_axis_aligned(exterior, tol=0.5):
        return []

    pts = list(exterior)
    if pts[0] == pts[-1]:
        pts = pts[:-1]
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
    pts = list(exterior)
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 2:
        return []
    minx, _miny, maxx, _maxy = _bbox_poligono(pts)

    izq = _segmentos_relieve_en_frontera(exterior, minx, tol=tol) if idx > 0 else []
    der = _segmentos_relieve_en_frontera(exterior, maxx, tol=tol)

    if izq:
        return izq
    if der and (idx < n_total - 1 or n_total == 1 or idx == n_total - 1):
        return der
    return []


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


def _normalizar_barras(placas_ok: List[dict]) -> List[dict]:
    barras = []
    for placa in placas_ok or []:
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
) -> Tuple[List[dict], List[dict]]:
    catalogo = _normalizar_barras(placas_ok)
    if not catalogo:
        return [], list(piezas or [])

    items = []
    sin_colocar: List[dict] = []
    for p in piezas or []:
        dims = _dims_pieza_nativo_mm(p.get("poly"))
        if dims is None:
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
            if item["len_mm"] > restante + 0.5:
                continue
            barra["colocados"].append((item, barra["cursor_x"], 0.0))
            barra["cursor_x"] += item["len_mm"]
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
            p_final["layer_override"] = "CUT_CU"
            p_final["closed"] = True
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
            exterior = (p_final.get("poligonos") or [[]])[0]
            for seg_i, seg in enumerate(
                _segmentos_corte_laser_pieza(
                    exterior,
                    idx=idx,
                    n_total=n_col,
                )
            ):
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
) -> Tuple[str, dict]:
    _log = dbg_fn or (lambda _msg: None)
    _log(f"[CU-LARGOS] clave={clave} | piezas={len(piezas)} | wo={wo_name}")

    if not placas_ok:
        return clave, {"error": "Sin inventario CU disponible para largos."}

    hojas, sin_colocar = empaquetar_largos_cu(piezas, placas_ok)
    if sin_colocar:
        detalle = []
        for p in sin_colocar:
            ancho = round(float(p.get("ancho_in", p.get("wid_mm", 0) or 0) / 25.4), 3)
            detalle.append(f"{p.get('nombre', '?')} ({ancho}\")")
        nombres = ", ".join(sorted(detalle))
        _log(f"[CU-LARGOS][SIN-COLOCAR] {nombres}")
        if not hojas:
            return clave, {
                "error": (
                    "No se pudieron anidar piezas de cobre. "
                    "Verifique ancho exacto vs inventario CU (2–6\") y largo ≤ 144\"."
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
        "costo_total": costo_total,
        "costo_empresa": costo_empresa,
        "costo_proveedor": costo_proveedor,
        "reporte": "Nesting 1D largos de cobre (orientación DXF, sin tolerancia de ancho).",
        "modo_largos_cu": True,
        "ignorar_deduccion_cu": True,
        **efi_grupo,
    }
    if sin_colocar:
        resultado["piezas_pendientes"] = [str(p.get("nombre", "")) for p in sin_colocar]
        resultado["advertencia"] = (
            f"{len(sin_colocar)} pieza(s) CU sin barra compatible (ancho/largo)."
        )

    return clave, resultado
