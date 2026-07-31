"""
RTZ especial de cobre sin_gap (nesting, PDF, visor y export DXF/STEP).

La solera física mide 144"; en barras sin_gap la máquina prioriza ≤114"
en la placa madre (primer procesado). Las piezas que rebasan ese límite
pasan a RTZCU (segundo procesado, con_gap → STEP).

Excepción exclusiva cobre: piezas con perfil relieve/Z (escalón/diagonal, no
rectángulo ortogonal) NUNCA van a RTZCU. Esas se priorizan dentro de los 114"
y, si no caben, abren barra nueva. Las rectangulares con solo orilla superior
(más angostas que la tira) y las laminas verticales exactas SÍ pueden pasar a
RTZCU con gap — también para rellenar la cola libre de una barra relieve/Z
(mismo ancho) aunque el tramo empiece antes de 114\".

El inicio del RTZCU NO es una línea fija en 114\": empieza al terminar la
última pieza de la madre + gap por defecto (puede quedar antes o después
de 114\" según cómo llenó la madre).

Si con gap no caben todas las piezas del tramo RTZ hasta el final de la
solera (144\"), las que sobran se derraman a barra(s) nueva(s) — que a su
vez pueden llenarse y generar otro RTZCU.
"""
from __future__ import annotations

import copy
from typing import List

from shapely import affinity
from shapely.geometry import LineString
from shapely.ops import unary_union

from .geometry_parser import generar_texto_vectorial, reconstruir_poly_seguro

# Solera estándar de cobre (inventario).
CU_BAR_LARGO_IN = 144.0
# Máximo preferente que entra en la máquina láser (placa madre / primer procesado).
CU_SIN_GAP_LASER_ZONA_MAX_IN = 114.0
# Máximo empaquetado en eje X de la placa madre (sin_gap) — umbral de clasificación.
CU_SIN_GAP_LARGO_CORTE_MAX_IN = CU_SIN_GAP_LASER_ZONA_MAX_IN
# Tramo físico reservado para RTZCU (referencia 114\" → 144\").
CU_SIN_GAP_RTZ_ZONA_MAX_IN = CU_BAR_LARGO_IN - CU_SIN_GAP_LASER_ZONA_MAX_IN
# Alias histórico / documentación.
CU_SIN_GAP_LASER_ZONA_NOMINAL_IN = CU_SIN_GAP_LASER_ZONA_MAX_IN


def in_to_mm(val_in: float) -> float:
    return float(val_in) * 25.4


def largo_corte_max_mm_sin_gap(largo_barra_mm: float) -> float:
    """Límite de empaquetado en eje X de la placa madre (114\" máquina)."""
    _ = largo_barra_mm
    return in_to_mm(CU_SIN_GAP_LARGO_CORTE_MAX_IN)


def rtz_zona_inicio_mm() -> float:
    return in_to_mm(CU_SIN_GAP_LASER_ZONA_MAX_IN)


def rtz_zona_fin_mm(largo_barra_mm: float | None = None) -> float:
    if largo_barra_mm is not None and largo_barra_mm > 0:
        return min(float(largo_barra_mm), in_to_mm(CU_BAR_LARGO_IN))
    return in_to_mm(CU_BAR_LARGO_IN)


def aplica_rtz_sin_gap(modo_barra: str) -> bool:
    return str(modo_barra or "").strip().lower() == "sin_gap"


def pieza_prohibida_en_rtzcu(pieza: dict) -> bool:
    """True si la pieza no puede ir a RTZCU (solo relieve/Z).

    Rectángulos con orilla superior (corte_superior) SÍ pueden ir a RTZCU.
    """
    if not isinstance(pieza, dict):
        return False
    if bool(pieza.get("cu_perfil_relieve")):
        return True
    # Tras el cambio de reglas, cu_forzar_sin_gap solo se setea para relieve/Z.
    if bool(pieza.get("cu_forzar_sin_gap")):
        return True
    # Fallback geométrico: contorno no ortogonal (Z/escalón), sin usar orilla sola.
    pols = (pieza.get("poligonos") or [])
    if pols and pols[0]:
        try:
            from .cu_largos_nesting import _solo_cortes_guillotina_vertical

            if not _solo_cortes_guillotina_vertical(list(pols[0])):
                return True
        except Exception:
            pass
    return False


def es_rtz_cu_id(rtz_id: str) -> bool:
    return str(rtz_id or "").strip().upper().startswith("RTZCU")


def es_overlay_rtz_cu(nombre: str) -> bool:
    """Overlays visuales de RTZ cobre en la placa madre (no van a DXF madre)."""
    n = str(nombre or "")
    if n.startswith("RETAZO_GUILLOTINA__") and "RTZCU" in n.upper():
        return True
    if n.startswith("TATUAJE__") and "RTZCU" in n.upper():
        return True
    return bool((n.startswith("RETAZO_GUILLOTINA__") or n.startswith("TATUAJE__")) and es_rtz_cu_id(n.split("__", 1)[-1]))


def _bbox_pieza_xy(pieza: dict) -> tuple[float | None, float | None, float | None, float | None]:
    pols = (pieza or {}).get("poligonos") or []
    if not pols or not pols[0]:
        return None, None, None, None
    xs = [float(t[0]) for t in pols[0]]
    ys = [float(t[1]) for t in pols[0]]
    return min(xs), min(ys), max(xs), max(ys)


def pieza_excluida_dxf_madre_cu(pieza: dict, hoja: dict | None = None) -> bool:
    """True si la pieza/corte pertenece al RTZCU y NO debe ir en el DXF de la madre.

    La madre solo corta el primer tramo (≤114\" / hasta inicio RTZ). Las piezas
    cu_zona_rtz y sus CU_CORTE van al STEP/DXF del RTZCU virtual.
    """
    if not isinstance(pieza, dict):
        return False
    nom = str(pieza.get("nombre") or "")
    if es_overlay_rtz_cu(nom) or nom.startswith("RTZCU_ZONA__"):
        return True
    if pieza.get("cu_zona_rtz"):
        return True

    hoja = hoja if isinstance(hoja, dict) else {}
    if not hoja.get("modo_largos_cu") or hoja.get("cu_rtz_virtual"):
        return False
    if not hoja.get("cu_rtz_activo"):
        return False

    try:
        inicio = float(hoja.get("cu_rtz_inicio_mm") or 0.0)
    except (TypeError, ValueError):
        inicio = 0.0
    if inicio <= 0.5:
        inicio = rtz_zona_inicio_mm()

    minx, _miny, maxx, _maxy = _bbox_pieza_xy(pieza)
    if minx is None:
        return False
    # Pieza/corte cuyo contorno empieza en la zona RTZ (o casi).
    if float(minx) >= float(inicio) - 0.5:
        return True
    # Pieza real casi toda en RTZ (por si el flag se perdió).
    if (
        maxx is not None
        and _es_pieza_real_cu(nom)
        and float(maxx) > float(inicio) + 0.5
        and float(minx) >= float(inicio) - 25.4
    ):
        return True
    return False


def largo_export_madre_cu_mm(hoja: dict) -> float | None:
    """Largo X del DXF madre: hasta el inicio del RTZCU (no toda la solera 144\")."""
    if not isinstance(hoja, dict) or not hoja.get("modo_largos_cu"):
        return None
    if hoja.get("cu_rtz_virtual") or hoja.get("es_retazo"):
        return None
    if not hoja.get("cu_rtz_activo"):
        return None
    try:
        inicio = float(hoja.get("cu_rtz_inicio_mm") or 0.0)
    except (TypeError, ValueError):
        inicio = 0.0
    if inicio <= 0.5:
        return None
    placa_w = float(hoja.get("placa_w") or 0.0)
    if placa_w <= 0.5:
        return float(inicio)
    return min(float(inicio), placa_w)

def _rect_poligono(x0: float, y0: float, x1: float, y1: float) -> list:
    return [
        (float(x0), float(y0)),
        (float(x1), float(y0)),
        (float(x1), float(y1)),
        (float(x0), float(y1)),
        (float(x0), float(y0)),
    ]


def _es_pieza_real_cu(nombre: str) -> bool:
    from .sheet_integrity import _es_pieza_real_nombre

    return _es_pieza_real_nombre(nombre)


def _bbox_x_mm(pieza: dict) -> tuple[float | None, float | None]:
    pols = (pieza or {}).get("poligonos") or []
    if not pols or not pols[0]:
        return None, None
    xs = [float(t[0]) for t in pols[0]]
    return min(xs), max(xs)


def pieza_es_rtz_sin_gap(pieza: dict, limite_mm: float | None = None) -> bool:
    """Pieza candidata a RTZ si termina después del límite máquina (114\").

    Piezas con corte lateral/relieve quedan excluidas (ver pieza_prohibida_en_rtzcu).
    """
    if not isinstance(pieza, dict):
        return False
    if not _es_pieza_real_cu(str(pieza.get("nombre") or "")):
        return False
    if pieza_prohibida_en_rtzcu(pieza):
        return False
    _minx, maxx = _bbox_x_mm(pieza)
    if maxx is None:
        return False
    limite = float(limite_mm if limite_mm is not None else rtz_zona_inicio_mm())
    return maxx > limite + 0.5


def _desplazar_poligonos_x(poligonos: list, dx: float) -> list:
    out: list = []
    for poly in poligonos or []:
        if not poly:
            out.append(poly)
            continue
        out.append([(float(x) + dx, float(y)) for x, y in poly])
    return out


def _desplazar_marcas_x(marcas: list, dx: float) -> list:
    out: list = []
    for linea in marcas or []:
        if not linea:
            out.append(linea)
            continue
        out.append([(float(x) + dx, float(y)) for x, y in linea])
    return out


def _pieza_coords_rtz_relativas(pieza: dict, origen_x_mm: float) -> dict:
    """Copia de pieza con coordenadas relativas al origen X del bloque RTZ."""
    p = copy.deepcopy(pieza)
    dx = -float(origen_x_mm)
    p["poligonos"] = _desplazar_poligonos_x(p.get("poligonos") or [], dx)
    p["marcas"] = _desplazar_marcas_x(p.get("marcas") or [], dx)
    p["cu_zona_rtz"] = True
    # RTZCU: gap por defecto (no sin_gap).
    p["cu_sin_separacion"] = False
    p["cu_modo_separacion_barra"] = "con_gap"
    return p


def _sep_gap_mm(separacion_in: float | None) -> float:
    try:
        sep = float(separacion_in)
    except (TypeError, ValueError):
        sep = 0.375
    if sep < 0:
        sep = 0.375
    return sep * 25.4


def _relayout_piezas_rtz_con_gap(
    piezas_rel: list[dict],
    *,
    separacion_in: float = 0.375,
) -> tuple[list[dict], float]:
    """Re-espacia piezas RTZCU con gap por defecto (misma lógica con_gap → STEP).

    Las piezas llegan con posiciones relativas heredadas del nesting sin_gap
    (pegadas). Aquí se vuelven a colocar desde X=0 con la separación de cobre.
    """
    if not piezas_rel:
        return [], 0.0

    gap_mm = _sep_gap_mm(separacion_in)
    ordered = sorted(
        [p for p in piezas_rel if isinstance(p, dict)],
        key=lambda p: (_bbox_x_mm(p)[0] is None, _bbox_x_mm(p)[0] or 0.0),
    )
    out: list[dict] = []
    cursor = 0.0
    for i, p in enumerate(ordered):
        minx, maxx = _bbox_x_mm(p)
        if minx is None or maxx is None:
            p2 = copy.deepcopy(p)
            p2["cu_sin_separacion"] = False
            p2["cu_modo_separacion_barra"] = "con_gap"
            p2["cu_zona_rtz"] = True
            out.append(p2)
            continue
        largo = max(0.0, float(maxx) - float(minx))
        if i > 0:
            cursor += gap_mm
        dx = cursor - float(minx)
        p2 = copy.deepcopy(p)
        p2["poligonos"] = _desplazar_poligonos_x(p2.get("poligonos") or [], dx)
        p2["marcas"] = _desplazar_marcas_x(p2.get("marcas") or [], dx)
        p2["cu_sin_separacion"] = False
        p2["cu_modo_separacion_barra"] = "con_gap"
        p2["cu_zona_rtz"] = True
        out.append(p2)
        cursor += largo
    return out, cursor


def _partir_rtz_por_disponible(
    piezas_madre: list[dict],
    *,
    disponible_mm: float,
    separacion_in: float = 0.375,
) -> tuple[list[dict], list[dict]]:
    """Parte piezas RTZ (refs en madre) en las que caben con gap y las que derraman."""
    if not piezas_madre:
        return [], []
    gap_mm = _sep_gap_mm(separacion_in)
    disponible = max(0.0, float(disponible_mm))
    ordered = sorted(
        [p for p in piezas_madre if isinstance(p, dict)],
        key=lambda p: (_bbox_x_mm(p)[0] is None, _bbox_x_mm(p)[0] or 0.0),
    )
    fitted: list[dict] = []
    cursor = 0.0
    for i, p in enumerate(ordered):
        minx, maxx = _bbox_x_mm(p)
        if minx is None or maxx is None:
            fitted.append(p)
            continue
        largo = max(0.0, float(maxx) - float(minx))
        need = largo if not fitted else (gap_mm + largo)
        if cursor + need > disponible + 0.5:
            return fitted, ordered[i:]
        if fitted:
            cursor += gap_mm
        cursor += largo
        fitted.append(p)
    return fitted, []


def _rebuild_marks_geom_cu(marcas) -> LineString | object:
    lineas = []
    for mk in marcas or []:
        try:
            if mk and len(mk) >= 2:
                ls = LineString(mk)
                if not ls.is_empty and ls.length > 0:
                    lineas.append(ls)
        except Exception:
            pass
    if not lineas:
        return LineString()
    try:
        return unary_union(lineas)
    except Exception:
        return lineas[0]


def colocada_a_pack_cu(pieza: dict) -> dict | None:
    """Convierte pieza colocada (poligonos absolutos) a formato pack de largos CU."""
    if not isinstance(pieza, dict):
        return None
    if not _es_pieza_real_cu(str(pieza.get("nombre") or "")):
        return None
    poly = reconstruir_poly_seguro(pieza.get("poligonos") or [])
    if poly is None or poly.is_empty:
        return None
    marks_geom = _rebuild_marks_geom_cu(pieza.get("marcas") or [])
    minx, miny, _, _ = poly.bounds
    return {
        "nombre": str(pieza.get("nombre") or ""),
        "poly": affinity.translate(poly, -minx, -miny),
        "marks": (
            affinity.translate(marks_geom, -minx, -miny)
            if marks_geom is not None and not getattr(marks_geom, "is_empty", True)
            else LineString()
        ),
        "area": float(pieza.get("area", poly.area) or poly.area),
        "calibre": pieza.get("calibre", ""),
        "material": pieza.get("material", ""),
        "ruta": pieza.get("ruta", ""),
        "cu_forzar_sin_gap": bool(pieza.get("cu_forzar_sin_gap")),
        "cu_perfil_relieve": bool(
            pieza.get("cu_perfil_relieve", pieza.get("cu_forzar_sin_gap"))
        ),
        "corte_superior_mm": float(pieza.get("corte_superior_mm") or 0.0),
        "calibre_superior": bool(pieza.get("calibre_superior")),
    }


def _sincronizar_piezas_rtz_en_madre(
    hoja: dict,
    piezas_rtz_rel: list[dict],
    *,
    origen_abs_mm: float,
) -> None:
    """Actualiza en la madre las piezas RTZ con layout con_gap (origen = fin madre + gap)."""
    if not isinstance(hoja, dict) or not piezas_rtz_rel:
        return
    origen = float(origen_abs_mm)
    madres_rtz = [
        p
        for p in (hoja.get("piezas") or [])
        if isinstance(p, dict)
        and p.get("cu_zona_rtz")
        and _es_pieza_real_cu(str(p.get("nombre") or ""))
    ]
    # Mismo criterio de orden que el relayout (por X).
    madres_rtz = sorted(
        madres_rtz,
        key=lambda p: (_bbox_x_mm(p)[0] is None, _bbox_x_mm(p)[0] or 0.0),
    )
    if len(madres_rtz) != len(piezas_rtz_rel):
        return
    for p_madre, p_rel in zip(madres_rtz, piezas_rtz_rel):
        p_madre["poligonos"] = _desplazar_poligonos_x(
            copy.deepcopy(p_rel.get("poligonos") or []), origen
        )
        p_madre["marcas"] = _desplazar_marcas_x(
            copy.deepcopy(p_rel.get("marcas") or []), origen
        )
        p_madre["cu_sin_separacion"] = False
        p_madre["cu_modo_separacion_barra"] = "con_gap"
        p_madre["cu_zona_rtz"] = True


def extraer_piezas_rtz_cu(hoja: dict) -> tuple[list[dict], float, float]:
    """
    Post-proceso: clasifica piezas ya nesteadas en la barra completa (144\").
    Devuelve (piezas con X relativo desde 0 ya con_gap, minx_abs_original, maxx_abs_gapped).
    """
    if not isinstance(hoja, dict):
        return [], 0.0, 0.0

    limite = rtz_zona_inicio_mm()
    mins_x: list[float] = []
    maxs_x: list[float] = []

    for p in hoja.get("piezas") or []:
        if not isinstance(p, dict):
            continue
        nom = str(p.get("nombre") or "")
        if _es_pieza_real_cu(nom):
            if pieza_es_rtz_sin_gap(p, limite):
                p["cu_zona_rtz"] = True
                minx, maxx = _bbox_x_mm(p)
                if minx is not None and maxx is not None:
                    mins_x.append(minx)
                    maxs_x.append(maxx)
            continue
        if p.get("cu_zona_rtz"):
            continue
        minx, maxx = _bbox_x_mm(p)
        if minx is not None and minx >= limite - 0.5:
            p["cu_zona_rtz"] = True
            if maxx is not None:
                mins_x.append(minx)
                maxs_x.append(maxx)

    if not mins_x or not maxs_x:
        return [], 0.0, 0.0

    minx_rtz = min(mins_x)
    piezas_rtz: list[dict] = []
    for p in hoja.get("piezas") or []:
        if not isinstance(p, dict) or not p.get("cu_zona_rtz"):
            continue
        if not _es_pieza_real_cu(str(p.get("nombre") or "")):
            # Overlays u otros: solo desplazar relativos (sin gap).
            piezas_rtz.append(_pieza_coords_rtz_relativas(p, minx_rtz))
            continue
        piezas_rtz.append(_pieza_coords_rtz_relativas(p, minx_rtz))

    # Solo piezas reales entran al relayout con_gap; overlays se descartan del span útil.
    reales = [
        p
        for p in piezas_rtz
        if _es_pieza_real_cu(str(p.get("nombre") or ""))
    ]
    try:
        sep_cu = float(hoja.get("separacion_cu_in"))
    except (TypeError, ValueError):
        sep_cu = 0.375
    if sep_cu < 0:
        sep_cu = 0.375

    reales_gap, largo_gap = _relayout_piezas_rtz_con_gap(reales, separacion_in=sep_cu)
    return reales_gap, minx_rtz, float(minx_rtz) + float(largo_gap)


def construir_overlays_rtz_cu(
    *,
    rtz_id: str,
    x_inicio_mm: float,
    largo_rtz_mm: float,
    ancho_mm: float,
    calibre: str = "",
    material: str = "CU",
) -> List[dict]:
    """Overlays en madre: misma convención que RTZ acero (guillotina + tatuaje)."""
    rid = str(rtz_id or "").strip()
    if not rid or largo_rtz_mm <= 0.5 or ancho_mm <= 0.5:
        return []

    x0 = float(x_inicio_mm)
    x1 = x0 + float(largo_rtz_mm)
    y0, y1 = 0.0, float(ancho_mm)
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    w_texto = max(50.0, min(float(largo_rtz_mm) * 0.85, 400.0))
    h_texto = max(15.0, min(float(ancho_mm) * 0.85, 40.0))
    marks = generar_texto_vectorial(rid, cx, cy, w_texto, h_texto)
    dummy = _rect_poligono(cx - 1.0, cy - 1.0, cx + 1.0, cy + 1.0)

    return [
        {
            "nombre": f"RETAZO_GUILLOTINA__{rid}",
            "poligonos": [_rect_poligono(x0, y0, x1, y1)],
            "marcas": [],
            "area": 0.0,
            "calibre": calibre,
            "material": material,
            "es_rtz_cu_overlay": True,
            "rtz_overlay_id": rid,
        },
        {
            "nombre": f"TATUAJE__{rid}",
            "poligonos": [dummy],
            "marcas": marks,
            "area": 0.0,
            "calibre": calibre,
            "material": material,
            "es_rtz_cu_overlay": True,
            "rtz_overlay_id": rid,
        },
    ]


def construir_hoja_rtz_cu_virtual(madre: dict) -> dict | None:
    """
    Hoja retazo virtual para UI (ACCESORIOS) y export DXF/STEP del tramo RTZ.
    Siempre con_gap → STEP (horizontal, CUT_OUTER cerrado); no CyPTube vertical.
    No genera página aparte en el PDF.
    """
    if not isinstance(madre, dict) or madre.get("es_retazo"):
        return None
    # Solo piezas reales: sin CU_CORTE / divisorias del nesting sin_gap.
    piezas_rtz = [
        p
        for p in (madre.get("cu_rtz_piezas_hoja") or [])
        if isinstance(p, dict) and _es_pieza_real_cu(str(p.get("nombre") or ""))
    ]
    if not piezas_rtz:
        return None

    rid = str(madre.get("cu_rtz_id") or "").strip()
    if not rid:
        return None

    largo_mm = float(madre.get("cu_rtz_largo_mm") or 0.0)
    ancho_mm = float(madre.get("placa_h") or 0.0)
    if largo_mm <= 0.5 or ancho_mm <= 0.5:
        return None

    gx = float(madre.get("cu_rtz_inicio_mm") or rtz_zona_inicio_mm())
    borde = _rect_poligono(0.0, 0.0, largo_mm, ancho_mm)
    area_usada = sum(float(p.get("area") or 0.0) for p in piezas_rtz)
    area_rtz = largo_mm * ancho_mm
    efi = (area_usada / area_rtz * 100.0) if area_rtz > 0 else 0.0

    try:
        sep_cu = float(madre.get("separacion_cu_in"))
    except (TypeError, ValueError):
        sep_cu = 0.375
    if sep_cu < 0:
        sep_cu = 0.375

    return {
        "piezas": copy.deepcopy(piezas_rtz),
        "poly_borde_retazo": borde,
        "area_usada": area_usada,
        "eficiencia": efi,
        "placa_id": rid,
        "placa_w": largo_mm,
        "placa_h": ancho_mm,
        "precio_placa": 0.0,
        "kerf_usado": 0.0,
        "margin_usado": 0.0,
        "opt_usado": "RTZ CU",
        "corner_usado": "INFERIOR IZQUIERDA",
        "es_retazo": True,
        "cu_rtz_virtual": True,
        "modo_largos_cu": True,
        # RTZCU: misma lógica STEP que con_gap (nunca sin_gap / vertical).
        "cu_modo_separacion_barra": "con_gap",
        "separacion_cu_in": sep_cu,
        "export_3d_format": "step",
        "global_x": gx,
        "global_y": 0.0,
        "retazo_tipo": "HOLE",
        "madre_sheet_uid": madre.get("sheet_uid"),
        "sheet_seq": madre.get("sheet_seq"),
        "sheet_code": rid,
        "ignorar_deduccion": True,
        "area_placa_mm2": area_rtz,
        "cu_rtz_largo_in": float(madre.get("cu_rtz_largo_in") or 0.0),
    }


def _gap_rtz_mm(hoja: dict | None = None) -> float:
    """Gap por defecto entre fin de madre e inicio de primera pieza RTZCU."""
    try:
        sep = float((hoja or {}).get("separacion_cu_in"))
    except (TypeError, ValueError, AttributeError):
        sep = 0.375
    if sep < 0:
        sep = 0.375
    return sep * 25.4


def _fin_piezas_madre_mm(hoja: dict) -> float:
    """X máximo de piezas reales que NO son zona RTZ (fin de placa madre)."""
    fin = 0.0
    for p in (hoja or {}).get("piezas") or []:
        if not isinstance(p, dict) or p.get("cu_zona_rtz"):
            continue
        if not _es_pieza_real_cu(str(p.get("nombre") or "")):
            continue
        _minx, maxx = _bbox_x_mm(p)
        if maxx is not None:
            fin = max(fin, float(maxx))
    return fin


def _calcular_inicio_rtz_mm(
    hoja: dict,
    *,
    fin_madre_mm: float,
    largo_rtz_mm: float,
    largo_barra_mm: float,
) -> float:
    """
    Inicio del bloque RTZCU: justo después de la madre + gap.

    - Si la madre no llenó hasta 114\", el RTZ puede empezar antes de 114\".
    - Si con gap el bloque no cabe hasta el final de la solera, se corre hacia
      atrás (sin solapar la madre) para aprovechar el tramo disponible.
    """
    gap = _gap_rtz_mm(hoja)
    inicio = float(fin_madre_mm) + gap
    largo_rtz = max(0.0, float(largo_rtz_mm))
    largo_bar = float(largo_barra_mm)

    if largo_rtz > 0.5 and largo_bar > 0.5:
        # Si no cabe hacia el final, empezar antes (hasta fin_madre+gap mínimo).
        if inicio + largo_rtz > largo_bar + 0.5:
            inicio_min = float(fin_madre_mm) + gap
            inicio_cab = max(0.0, largo_bar - largo_rtz)
            inicio = max(inicio_min, inicio_cab) if inicio_cab >= inicio_min else inicio_min

    return max(0.0, inicio)


def aplicar_rtz_sin_gap_a_hoja_con_derrame(hoja: dict) -> tuple[bool, list[dict]]:
    """Activa RTZCU; si con gap no caben todas, quita el sobrante y lo devuelve como pack.

    Returns:
        (activo, derrame_pack): piezas que no cupieron en el tramo RTZ (origen local).
    """
    if not isinstance(hoja, dict) or not hoja.get("modo_largos_cu"):
        return False, []
    if hoja.get("es_retazo") or hoja.get("cu_rtz_virtual"):
        return False, []
    if not aplica_rtz_sin_gap(str(hoja.get("cu_modo_separacion_barra") or "")):
        hoja["cu_rtz_activo"] = False
        hoja.pop("cu_rtz_piezas_hoja", None)
        return False, []

    largo_mm = float(hoja.get("placa_w") or 0.0)
    ancho_mm = float(hoja.get("placa_h") or 0.0)
    if largo_mm <= 0 or ancho_mm <= 0:
        return False, []

    limite = rtz_zona_inicio_mm()
    try:
        sep_cu = float(hoja.get("separacion_cu_in"))
    except (TypeError, ValueError):
        sep_cu = 0.375
    if sep_cu < 0:
        sep_cu = 0.375

    # Clasificar piezas reales más allá de 114\".
    # Relieve/Z: fuera de RTZCU → derrame a barra nueva.
    # Orilla rectangular: sí puede marcarse RTZCU.
    derrame_pack: list[dict] = []
    prohibidas_past: list[dict] = []
    for p in hoja.get("piezas") or []:
        if not isinstance(p, dict):
            continue
        if not _es_pieza_real_cu(str(p.get("nombre") or "")):
            continue
        _minx, maxx = _bbox_x_mm(p)
        past_limite = maxx is not None and maxx > limite + 0.5
        if past_limite and pieza_prohibida_en_rtzcu(p):
            p.pop("cu_zona_rtz", None)
            prohibidas_past.append(p)
        elif pieza_prohibida_en_rtzcu(p):
            p.pop("cu_zona_rtz", None)
        elif pieza_es_rtz_sin_gap(p, limite) or p.get("cu_zona_rtz"):
            # cu_zona_rtz ya marcado: orilla/vertical en cola de barra relieve/Z
            # (puede empezar antes de 114\" al terminar la madre Z).
            p["cu_zona_rtz"] = True
        else:
            p.pop("cu_zona_rtz", None)

    if prohibidas_past:
        ids_proh = {id(p) for p in prohibidas_past}
        nombres_proh = {
            str(p.get("nombre") or "")
            for p in prohibidas_past
            if str(p.get("nombre") or "")
        }
        for p in prohibidas_past:
            pack = colocada_a_pack_cu(p)
            if pack is not None:
                derrame_pack.append(pack)
        hoja["piezas"] = [
            p
            for p in (hoja.get("piezas") or [])
            if id(p) not in ids_proh
            and not _es_corte_auxiliar_de_nombres(p, nombres_proh)
        ]

    rtz_madre = [
        p
        for p in (hoja.get("piezas") or [])
        if isinstance(p, dict)
        and p.get("cu_zona_rtz")
        and _es_pieza_real_cu(str(p.get("nombre") or ""))
    ]
    if not rtz_madre:
        hoja["cu_rtz_activo"] = False
        hoja.pop("cu_rtz_piezas_hoja", None)
        hoja.pop("cu_rtz_id", None)
        hoja.pop("cu_rtz_largo_mm", None)
        return False, derrame_pack

    fin_madre = _fin_piezas_madre_mm(hoja)
    gap = _gap_rtz_mm(hoja)
    disponible = max(0.0, largo_mm - (fin_madre + gap))

    fitted_madre, overflow_madre = _partir_rtz_por_disponible(
        rtz_madre,
        disponible_mm=disponible,
        separacion_in=sep_cu,
    )

    if overflow_madre:
        ids_out = {id(p) for p in overflow_madre}
        nombres_out = {
            str(p.get("nombre") or "")
            for p in overflow_madre
            if str(p.get("nombre") or "")
        }
        for p in overflow_madre:
            pack = colocada_a_pack_cu(p)
            if pack is not None:
                derrame_pack.append(pack)
        # Quitar piezas derramadas y cortes auxiliares ligados a ellas.
        hoja["piezas"] = [
            p
            for p in (hoja.get("piezas") or [])
            if id(p) not in ids_out
            and not _es_corte_auxiliar_de_nombres(p, nombres_out)
        ]
        for p in overflow_madre:
            p.pop("cu_zona_rtz", None)

    if not fitted_madre:
        hoja["cu_rtz_activo"] = False
        hoja.pop("cu_rtz_piezas_hoja", None)
        hoja.pop("cu_rtz_id", None)
        hoja.pop("cu_rtz_largo_mm", None)
        return False, derrame_pack

    # Relayout con_gap de las que sí caben (coords relativas desde 0).
    minx_rtz = min(
        (mx for mx, _ in (_bbox_x_mm(p) for p in fitted_madre) if mx is not None),
        default=0.0,
    )
    piezas_rel = [_pieza_coords_rtz_relativas(p, minx_rtz) for p in fitted_madre]
    piezas_rtz, largo_span = _relayout_piezas_rtz_con_gap(
        piezas_rel, separacion_in=sep_cu
    )
    if largo_span <= 0.5:
        hoja["cu_rtz_activo"] = False
        hoja["cu_rtz_piezas_hoja"] = []
        return False, derrame_pack

    inicio_rtz = _calcular_inicio_rtz_mm(
        hoja,
        fin_madre_mm=fin_madre,
        largo_rtz_mm=largo_span,
        largo_barra_mm=largo_mm,
    )

    _sincronizar_piezas_rtz_en_madre(hoja, piezas_rtz, origen_abs_mm=inicio_rtz)
    hoja["cu_rtz_piezas_hoja"] = piezas_rtz

    maxx_abs = inicio_rtz + largo_span
    largo_overlay = max(0.0, min(largo_span, max(0.0, largo_mm - inicio_rtz)))
    n_reales_rtz = len(piezas_rtz)

    hoja["cu_rtz_activo"] = True
    hoja["cu_rtz_inicio_mm"] = inicio_rtz
    hoja["cu_rtz_minx_mm"] = inicio_rtz
    hoja["cu_rtz_maxx_mm"] = maxx_abs
    hoja["cu_rtz_fin_piezas_mm"] = maxx_abs
    hoja["cu_fin_piezas_mm"] = fin_madre
    hoja["cu_rtz_largo_mm"] = largo_span
    hoja["cu_rtz_overlay_largo_mm"] = largo_overlay
    hoja["cu_rtz_largo_in"] = largo_span / 25.4
    hoja["cu_bar_largo_in"] = largo_mm / 25.4
    hoja["cu_rtz_cant_piezas"] = n_reales_rtz
    return True, derrame_pack


def _es_corte_auxiliar_de_nombres(pieza: dict, nombres: set[str]) -> bool:
    """Cortes CU_CORTE / guillotinas ligadas a piezas derramadas."""
    if not nombres or not isinstance(pieza, dict):
        return False
    nom = str(pieza.get("nombre") or "")
    if not nom.startswith("CU_CORTE__"):
        return False
    for n in nombres:
        if n and n in nom:
            return True
    return False


def aplicar_rtz_sin_gap_a_hoja(hoja: dict) -> bool:
    """Activa RTZCU si hay piezas reales más allá del límite máquina (114\")."""
    activo, _derrame = aplicar_rtz_sin_gap_a_hoja_con_derrame(hoja)
    return activo


def insertar_hojas_rtz_cu_virtuales(hojas: list) -> None:
    """Inserta hoja (ACCESORIOS) justo después de cada madre con RTZ de cobre."""
    if not isinstance(hojas, list):
        return

    limpias: list = []
    for h in hojas:
        if not isinstance(h, dict):
            limpias.append(h)
            continue
        if h.get("cu_rtz_virtual"):
            continue
        limpias.append(h)

    nuevas: list = []
    for h in limpias:
        nuevas.append(h)
        if (
            isinstance(h, dict)
            and h.get("modo_largos_cu")
            and not h.get("es_retazo")
            and h.get("cu_rtz_activo")
            and (h.get("cu_rtz_piezas_hoja") or [])
        ):
            virtual = construir_hoja_rtz_cu_virtual(h)
            if virtual is not None:
                nuevas.append(copy.deepcopy(virtual))
    hojas[:] = nuevas


def sincronizar_overlays_rtz_cu_madre(madre: dict, virtual: dict) -> None:
    """REF + guillotina en madre (inicio = fin madre + gap; no línea fija 114\")."""
    if not isinstance(madre, dict) or not isinstance(virtual, dict):
        return
    rid = str(madre.get("cu_rtz_id") or virtual.get("placa_id") or "").strip()
    if not rid:
        return

    inicio = float(madre.get("cu_rtz_inicio_mm") or rtz_zona_inicio_mm())
    overlay_mm = float(
        madre.get("cu_rtz_overlay_largo_mm")
        or madre.get("cu_rtz_largo_mm")
        or virtual.get("placa_w")
        or 0.0
    )
    ancho_mm = float(madre.get("placa_h") or virtual.get("placa_h") or 0.0)
    if overlay_mm <= 0.5 or ancho_mm <= 0.5:
        return

    cal = ""
    mat = "CU"
    for p in (madre.get("piezas") or []) + (virtual.get("piezas") or []):
        if _es_pieza_real_cu(str((p or {}).get("nombre") or "")):
            cal = str(p.get("calibre") or cal)
            mat = str(p.get("material") or mat)
            break

    prefijos = (f"RETAZO_GUILLOTINA__{rid}", f"TATUAJE__{rid}")
    madre["piezas"] = [
        p
        for p in (madre.get("piezas") or [])
        if not str((p or {}).get("nombre") or "").startswith(prefijos)
    ]
    madre.setdefault("piezas", []).extend(
        construir_overlays_rtz_cu(
            rtz_id=rid,
            x_inicio_mm=inicio,
            largo_rtz_mm=overlay_mm,
            ancho_mm=ancho_mm,
            calibre=cal,
            material=mat,
        )
    )

    try:
        from .rtz_overlays import _reconstruir_overlays_rtz_en_madre

        _reconstruir_overlays_rtz_en_madre(madre, virtual)
    except Exception:
        pass


def asignar_rtz_cu_sin_gap_ids(resultados) -> int:
    """Asigna RTZCU{n}-H{m}, actualiza overlays e inserta hojas virtuales RTZ."""
    from .sheet_numbering import iterar_grupos_nesting_ordenados

    seq = 0
    for _clave, grupo in iterar_grupos_nesting_ordenados(resultados):
        if not isinstance(grupo, dict):
            continue
        hojas = grupo.get("hojas")
        if not isinstance(hojas, list):
            continue

        for hoja in hojas:
            if not isinstance(hoja, dict) or hoja.get("es_retazo"):
                continue
            if not hoja.get("modo_largos_cu"):
                continue
            if not aplica_rtz_sin_gap(str(hoja.get("cu_modo_separacion_barra") or "")):
                continue
            # Derrame ya debió resolverse en procesar_grupo_largos_cu; aquí solo layout/IDs.
            activo, derrame = aplicar_rtz_sin_gap_a_hoja_con_derrame(hoja)
            if derrame:
                # Seguridad: no debería ocurrir si el packing ya derramó a barras nuevas.
                grupo.setdefault("cu_rtz_derrame_sin_barra", []).extend(
                    str(p.get("nombre") or "?") for p in derrame
                )
            if not hoja.get("cu_rtz_activo"):
                continue

            sheet_seq = hoja.get("sheet_seq")
            try:
                h_suffix = int(sheet_seq)
            except (TypeError, ValueError):
                h_suffix = 0

            seq += 1
            rtz_id = f"RTZCU{seq}-H{h_suffix}"
            hoja["cu_rtz_id"] = rtz_id
            hoja["cu_rtz_seq"] = seq

        insertar_hojas_rtz_cu_virtuales(hojas)

        for i, hoja in enumerate(hojas):
            if not isinstance(hoja, dict) or hoja.get("cu_rtz_virtual"):
                continue
            if not hoja.get("cu_rtz_activo"):
                continue
            rtz_hijas = [
                h
                for h in hojas[i + 1 :]
                if isinstance(h, dict)
                and h.get("cu_rtz_virtual")
                and str(h.get("madre_sheet_uid") or "") == str(hoja.get("sheet_uid") or "")
            ]
            if rtz_hijas:
                sincronizar_overlays_rtz_cu_madre(hoja, rtz_hijas[0])

    return seq
