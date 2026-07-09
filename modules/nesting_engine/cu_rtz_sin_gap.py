"""
RTZ especial de cobre sin_gap (nesting, PDF, visor y export DXF).

La solera física mide 144"; en barras sin_gap la máquina solo procesa 114"
en la placa madre. El tramo 114"–144" es RTZCU con piezas (segundo procesado).

- UI: placa madre + fila (ACCESORIOS) como nesteo normal.
- PDF: una sola página por barra (madre + zona RTZ).
- DXF: dos archivos por barra (madre + RTZ), ambos con sufijo Hn.
"""
from __future__ import annotations

import copy
from typing import List

from .geometry_parser import generar_texto_vectorial

# Solera estándar de cobre (inventario).
CU_BAR_LARGO_IN = 144.0
# Máximo que entra en la máquina láser (placa madre / primer procesado).
CU_SIN_GAP_LASER_ZONA_MAX_IN = 114.0
# Máximo empaquetado en eje X de la placa madre (sin_gap).
CU_SIN_GAP_LARGO_CORTE_MAX_IN = CU_SIN_GAP_LASER_ZONA_MAX_IN
# Tramo físico reservado para RTZCU (114" → 144").
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
    """Pieza completa al RTZ si su contorno termina después del límite máquina (114\")."""
    if not isinstance(pieza, dict):
        return False
    if not _es_pieza_real_cu(str(pieza.get("nombre") or "")):
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
    return p


def extraer_piezas_rtz_cu(hoja: dict) -> tuple[list[dict], float, float]:
    """
    Post-proceso: clasifica piezas ya nesteadas en la barra completa (144\").
    Devuelve (piezas con X relativo desde 0, minx_abs, maxx_abs).
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
    maxx_rtz = max(maxs_x)
    piezas_rtz: list[dict] = []
    for p in hoja.get("piezas") or []:
        if not isinstance(p, dict) or not p.get("cu_zona_rtz"):
            continue
        piezas_rtz.append(_pieza_coords_rtz_relativas(p, minx_rtz))

    return piezas_rtz, minx_rtz, maxx_rtz


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
    Hoja retazo virtual para UI (ACCESORIOS) y export DXF del tramo RTZ.
    No genera página aparte en el PDF.
    """
    if not isinstance(madre, dict) or madre.get("es_retazo"):
        return None
    piezas_rtz = [
        p for p in (madre.get("cu_rtz_piezas_hoja") or []) if isinstance(p, dict)
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

    gx = float(madre.get("cu_rtz_minx_mm") or madre.get("cu_rtz_inicio_mm") or rtz_zona_inicio_mm())
    borde = _rect_poligono(0.0, 0.0, largo_mm, ancho_mm)
    area_usada = sum(float(p.get("area") or 0.0) for p in piezas_rtz)
    area_rtz = largo_mm * ancho_mm
    efi = (area_usada / area_rtz * 100.0) if area_rtz > 0 else 0.0

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
        "cu_modo_separacion_barra": str(madre.get("cu_modo_separacion_barra") or "sin_gap"),
        "export_3d_format": "dxf",
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


def aplicar_rtz_sin_gap_a_hoja(hoja: dict) -> bool:
    """Activa RTZCU solo si hay piezas reales más allá de 114\" (post-proceso)."""
    if not isinstance(hoja, dict) or not hoja.get("modo_largos_cu"):
        return False
    if hoja.get("es_retazo") or hoja.get("cu_rtz_virtual"):
        return False
    if not aplica_rtz_sin_gap(str(hoja.get("cu_modo_separacion_barra") or "")):
        hoja["cu_rtz_activo"] = False
        hoja.pop("cu_rtz_piezas_hoja", None)
        return False

    largo_mm = float(hoja.get("placa_w") or 0.0)
    ancho_mm = float(hoja.get("placa_h") or 0.0)
    if largo_mm <= 0 or ancho_mm <= 0:
        return False

    inicio_rtz = rtz_zona_inicio_mm()
    piezas_rtz, minx_rtz, maxx_rtz = extraer_piezas_rtz_cu(hoja)
    hoja["cu_rtz_piezas_hoja"] = piezas_rtz
    if not piezas_rtz:
        hoja["cu_rtz_activo"] = False
        hoja.pop("cu_rtz_id", None)
        hoja.pop("cu_rtz_largo_mm", None)
        return False

    largo_span = maxx_rtz - minx_rtz
    largo_overlay = max(0.0, maxx_rtz - inicio_rtz)
    if largo_span <= 0.5:
        hoja["cu_rtz_activo"] = False
        hoja["cu_rtz_piezas_hoja"] = []
        return False

    fin_madre = inicio_rtz
    for p in hoja.get("piezas") or []:
        if not isinstance(p, dict) or p.get("cu_zona_rtz"):
            continue
        if not _es_pieza_real_cu(str(p.get("nombre") or "")):
            continue
        _minx, maxx = _bbox_x_mm(p)
        if maxx is not None:
            fin_madre = max(fin_madre, maxx)

    n_reales_rtz = sum(
        1
        for p in piezas_rtz
        if _es_pieza_real_cu(str(p.get("nombre") or ""))
    )
    hoja["cu_rtz_activo"] = True
    hoja["cu_rtz_inicio_mm"] = inicio_rtz
    hoja["cu_rtz_minx_mm"] = minx_rtz
    hoja["cu_rtz_maxx_mm"] = maxx_rtz
    hoja["cu_rtz_fin_piezas_mm"] = maxx_rtz
    hoja["cu_fin_piezas_mm"] = fin_madre
    hoja["cu_rtz_largo_mm"] = largo_span
    hoja["cu_rtz_overlay_largo_mm"] = largo_overlay
    hoja["cu_rtz_largo_in"] = largo_span / 25.4
    hoja["cu_bar_largo_in"] = largo_mm / 25.4
    hoja["cu_rtz_cant_piezas"] = n_reales_rtz
    return True


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
    """REF + guillotina en madre (guillotina fija 114\", ancho = piezas RTZ)."""
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
            aplicar_rtz_sin_gap_a_hoja(hoja)
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
