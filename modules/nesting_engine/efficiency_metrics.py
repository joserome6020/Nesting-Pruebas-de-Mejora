"""
Métricas de eficiencia de nesting.

Eficiencia directa (por placa):
    Placa madre: área de piezas anidadas en el layout principal / área rectangular de la placa.
    RTZ: área de piezas en el retazo / rectángulo del retazo (placa_w × placa_h).

Eficiencia real (por placa):
    Placa madre: aprovechamiento absoluto del material comprado =
        (piezas en madre + piezas en sus RTZ asociados) / área de la placa madre.
    RTZ: área de piezas / área real del polígono del retazo (poly_borde_retazo).

Eficiencia del tanque (por calibre/grupo):
    real    -> suma área piezas reales (sin duplicar REF__) / suma área placas madre.
    directa -> promedio ponderado de eficiencia directa de cada placa madre.
"""

from shapely.geometry import Polygon

from .geometry_parser import area_poligonos_colocados

# Umbral para ofrecer "ignorar placa" en deducción de inventario/compras.
UMBRAL_EFICIENCIA_IGNORAR_PLACA = 25.0


def _es_pieza_real_nombre(nombre: str) -> bool:
    n = str(nombre or "")
    return not (
        n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
        or n.startswith("RTZCU_ZONA__")
        or n.startswith("CU_CORTE__")
        or n.startswith("REMANENTE__")
    )


def _area_pieza_colocada(p) -> float:
    """Área neta en mm² desde polígonos colocados (fuente de verdad visual)."""
    pols = (p or {}).get("poligonos") or []
    area_geom = area_poligonos_colocados(pols)
    if area_geom > 0.0:
        return area_geom
    return float((p or {}).get("area", 0.0) or 0.0)


def area_piezas_reales_hoja(hoja) -> float:
    total = 0.0
    for p in (hoja or {}).get("piezas") or []:
        if not _es_pieza_real_nombre(p.get("nombre", "")):
            continue
        a = _area_pieza_colocada(p)
        if a > 0.0:
            p["area"] = a
        total += a
    return total


def _area_placa_rect(hoja) -> float:
    w = float((hoja or {}).get("placa_w", 0.0) or 0.0)
    h = float((hoja or {}).get("placa_h", 0.0) or 0.0)
    return max(0.0, w * h)


def _area_retazo_real(hoja) -> float:
    if not (hoja or {}).get("es_retazo"):
        return 0.0
    if (hoja or {}).get("poly_borde_retazo"):
        try:
            poly = Polygon((hoja or {}).get("poly_borde_retazo") or [])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly is not None and (not poly.is_empty):
                return float(poly.area)
        except Exception:
            pass
    return _area_placa_rect(hoja)


def _indices_madre(hojas):
    return [i for i, h in enumerate(hojas or []) if isinstance(h, dict) and not h.get("es_retazo")]


def _rtz_por_madre(hojas):
    madres = _indices_madre(hojas)
    mapa = {idx: [] for idx in madres}
    for i, hoja in enumerate(hojas or []):
        if not isinstance(hoja, dict) or not hoja.get("es_retazo"):
            continue
        madre_idx = None
        for m in madres:
            if m < i:
                madre_idx = m
            else:
                break
        if madre_idx is not None:
            mapa.setdefault(madre_idx, []).append(hoja)
    return mapa


def _metricas_hoja_aislada(hoja) -> dict:
    area_piezas = area_piezas_reales_hoja(hoja)
    area_placa = _area_placa_rect(hoja)

    if (hoja or {}).get("es_retazo"):
        area_real = _area_retazo_real(hoja)
        efi_directa = (area_piezas / area_placa * 100.0) if area_placa > 0.0 else 0.0
        efi_real = (area_piezas / area_real * 100.0) if area_real > 0.0 else 0.0
    else:
        efi_directa = (area_piezas / area_placa * 100.0) if area_placa > 0.0 else 0.0
        efi_real = efi_directa

    return {
        "area_piezas_mm2": area_piezas,
        "area_placa_mm2": area_placa,
        "area_util_real_mm2": _area_retazo_real(hoja) if (hoja or {}).get("es_retazo") else area_placa,
        "eficiencia_directa": efi_directa,
        "eficiencia_real": min(100.0, efi_real),
        "eficiencia": efi_directa,
    }


def calcular_eficiencias_hoja(hoja, hojas_grupo=None) -> dict:
    if not isinstance(hoja, dict):
        return {}

    hojas = hojas_grupo if isinstance(hojas_grupo, list) else None
    if not hojas:
        return _metricas_hoja_aislada(hoja)

    rtz_map = _rtz_por_madre(hojas)
    try:
        idx = hojas.index(hoja)
    except ValueError:
        return _metricas_hoja_aislada(hoja)

    area_piezas = area_piezas_reales_hoja(hoja)
    area_placa = _area_placa_rect(hoja)

    if hoja.get("es_retazo"):
        area_real = _area_retazo_real(hoja)
        efi_directa = (area_piezas / area_placa * 100.0) if area_placa > 0.0 else 0.0
        efi_real = (area_piezas / area_real * 100.0) if area_real > 0.0 else 0.0
        return {
            "area_piezas_mm2": area_piezas,
            "area_placa_mm2": area_placa,
            "area_util_real_mm2": area_real,
            "eficiencia_directa": efi_directa,
            "eficiencia_real": min(100.0, efi_real),
            "eficiencia": efi_directa,
        }

    area_rtz = sum(area_piezas_reales_hoja(r) for r in rtz_map.get(idx, []))
    area_total_fisica = area_piezas + area_rtz
    efi_directa = (area_piezas / area_placa * 100.0) if area_placa > 0.0 else 0.0
    efi_real = (area_total_fisica / area_placa * 100.0) if area_placa > 0.0 else 0.0

    return {
        "area_piezas_mm2": area_piezas,
        "area_placa_mm2": area_placa,
        "area_util_real_mm2": area_placa,
        "area_piezas_con_rtz_mm2": area_total_fisica,
        "eficiencia_directa": efi_directa,
        "eficiencia_real": min(100.0, efi_real),
        "eficiencia": efi_directa,
    }


def actualizar_eficiencias_hoja(hoja, hojas_grupo=None) -> dict:
    if not isinstance(hoja, dict):
        return hoja
    hoja.update(calcular_eficiencias_hoja(hoja, hojas_grupo=hojas_grupo))
    return hoja


def calcular_eficiencias_grupo(hojas) -> dict:
    hojas = [h for h in (hojas or []) if isinstance(h, dict)]
    rtz_map = _rtz_por_madre(hojas)

    area_solo_madre = 0.0
    area_total_fisica = 0.0
    area_madre_total = 0.0

    for i, hoja in enumerate(hojas):
        if hoja.get("es_retazo"):
            continue

        actualizar_eficiencias_hoja(hoja, hojas_grupo=hojas)

        area_m = area_piezas_reales_hoja(hoja)
        area_r = sum(area_piezas_reales_hoja(r) for r in rtz_map.get(i, []))
        area_placa = _area_placa_rect(hoja)

        area_solo_madre += area_m
        area_total_fisica += area_m + area_r
        area_madre_total += area_placa

    for hoja in hojas:
        if not hoja.get("es_retazo"):
            continue
        actualizar_eficiencias_hoja(hoja, hojas_grupo=hojas)

    efi_tanque_directa = (
        (area_solo_madre / area_madre_total * 100.0) if area_madre_total > 0.0 else 0.0
    )
    efi_tanque_real = (
        (area_total_fisica / area_madre_total * 100.0) if area_madre_total > 0.0 else 0.0
    )

    return {
        "eficiencia_tanque_real": min(100.0, efi_tanque_real),
        "eficiencia_tanque_directa": efi_tanque_directa,
        "area_piezas_tanque_mm2": area_total_fisica,
        "area_piezas_solo_madre_mm2": area_solo_madre,
        "area_placas_madre_mm2": area_madre_total,
    }


def _es_pieza_contable(pieza) -> bool:
    nom = str((pieza or {}).get("nombre", "") or "")
    return not (
        nom.startswith("REMANENTE__")
        or nom.startswith("REF__")
        or nom.startswith("RETAZO_GUILLOTINA__")
        or nom.startswith("CU_CORTE__")
        or nom.startswith("TATUAJE__")
    )


def contar_piezas_hoja(hoja) -> int:
    piezas = (hoja or {}).get("piezas") or []
    excl_rtz_en_madre = bool((hoja or {}).get("modo_largos_cu")) and not (
        (hoja or {}).get("cu_rtz_virtual") or (hoja or {}).get("es_retazo")
    )
    n = 0
    for p in piezas:
        if not _es_pieza_contable(p):
            continue
        if excl_rtz_en_madre and p.get("cu_zona_rtz"):
            continue
        n += 1
    return n


def contar_piezas_grupo(info_grupo) -> int:
    if not isinstance(info_grupo, dict):
        return 0
    total = 0
    for hoja in info_grupo.get("hojas") or []:
        if not isinstance(hoja, dict):
            continue
        if hoja.get("cu_rtz_virtual"):
            total += contar_piezas_hoja(hoja)
        elif not hoja.get("es_retazo"):
            total += contar_piezas_hoja(hoja)
        else:
            total += contar_piezas_hoja(hoja)
    return total


def formatear_eficiencias_placa(hoja) -> str:
    d = float((hoja or {}).get("eficiencia_directa", (hoja or {}).get("eficiencia", 0.0)) or 0.0)
    r = float((hoja or {}).get("eficiencia_real", d) or 0.0)
    n = contar_piezas_hoja(hoja)
    return f"DIR {d:.1f}% | REAL {r:.1f}% · {n} PZAS"


def formatear_eficiencias_tanque(info_grupo) -> str:
    if not isinstance(info_grupo, dict):
        return ""
    d = float(info_grupo.get("eficiencia_tanque_directa", 0.0) or 0.0)
    r = float(info_grupo.get("eficiencia_tanque_real", 0.0) or 0.0)
    n = contar_piezas_grupo(info_grupo)
    return f"TANQUE DIR {d:.1f}% | REAL {r:.1f}% · {n} PZAS"


def eficiencia_para_umbral_ignorar(hoja, hojas_grupo=None) -> float:
    """
    Métrica para decidir si ofrecer ignorar la placa en deducción.
    Usa eficiencia REAL (madre + RTZ / área placa madre): material total aprovechado
    del laminado comprado, no solo lo visible en el layout principal.
    """
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return 100.0
    if hojas_grupo:
        return float(calcular_eficiencias_hoja(hoja, hojas_grupo=hojas_grupo).get("eficiencia_real", 0.0) or 0.0)
    return float(
        hoja.get("eficiencia_real", hoja.get("eficiencia_directa", hoja.get("eficiencia", 0.0))) or 0.0
    )


def hoja_cuenta_para_deduccion(hoja) -> bool:
    """Placas madre marcadas como ignoradas no descuentan inventario ni costo."""
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return False
    return not bool(hoja.get("ignorar_deduccion", False))


def placa_debe_mostrar_opcion_ignorar(hoja, hojas_grupo=None) -> bool:
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return False
    if hoja.get("ignorar_deduccion"):
        return True
    return eficiencia_para_umbral_ignorar(hoja, hojas_grupo) < UMBRAL_EFICIENCIA_IGNORAR_PLACA


def hoja_es_sobrante_sin_compra(hoja) -> bool:
    """Placa madre con switch Sobrante ON: no genera pedido de compra."""
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return False
    return bool(hoja.get("ignorar_deduccion", False))


def hoja_excluida_de_rtz_sobrante(hoja) -> bool:
    """
    Cobre en largos (CU): siempre ignora deducción de inventario, pero no debe
    renombrarse como RTZ ni tratarse como retazo de láser/plasma.
    """
    return bool((hoja or {}).get("modo_largos_cu"))


def _limpiar_nomenclatura_rtz_en_hoja(hoja: dict) -> None:
    """Quita flags/nombres RTZ y restaura el código de stock si existía."""
    if not isinstance(hoja, dict):
        return
    original = str(hoja.get("_placa_id_antes_sobrante") or "").strip()
    pid = str(hoja.get("placa_id") or "").strip()
    if original:
        hoja["placa_id"] = original
    elif pid.upper().startswith("RTZ"):
        # Sin respaldo: conservar id actual (mejor que inventar otro).
        pass
    hoja.pop("sheet_display_name", None)
    hoja["is_rtz"] = False
    hoja.pop("is_rtz_plasma_sobrante", None)
    hoja.pop("rtz_tipo", None)
    hoja.pop("_rtz_nombre_sobrante", None)
    hoja.pop("_rtzc_nombre_sobrante", None)


def hoja_cuenta_como_rtz(hoja) -> bool:
    """True si la hoja es retazo real o sobrante láser/plasma renombrado RTZ."""
    if not isinstance(hoja, dict):
        return False
    if hoja.get("es_retazo"):
        return True
    if hoja_excluida_de_rtz_sobrante(hoja):
        return False
    if bool(hoja.get("is_rtz")):
        return True
    return hoja_es_sobrante_sin_compra(hoja)


def hoja_es_sobrante_plasma_compensado(hoja) -> bool:
    """Sobrante de piso con piezas compensadas para plasma: solo Robot Plasma."""
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return False
    return bool(hoja.get("ignorar_deduccion")) and bool(
        hoja.get("plasma_compensado_manual")
    )


def hoja_export_solo_plasma(hoja) -> bool:
    """Placa que solo exporta a Robot Plasma (sin DXF láser duplicado)."""
    if not isinstance(hoja, dict):
        return False
    if hoja_es_sobrante_plasma_compensado(hoja) or bool(hoja.get("is_rtz_plasma_sobrante")):
        return True
    # Cualquier hoja compensada manualmente (madre o RTZ) va solo a Robot Plasma.
    if bool(hoja.get("plasma_compensado_manual")):
        return True
    return False


def nombre_rtz_para_placa(
    contador_rtz: int,
    calibre: str,
    wo_name: str,
    *,
    largo_mm: float | None = None,
    ancho_mm: float | None = None,
) -> str:
    """
    Nomenclatura RTZ: RTZ{n}-{espesor}-{largo}x{ancho}-{wo} (pulgadas en medidas).
    Sin medidas válidas conserva RTZ{n}-{espesor}-{wo}.
    """
    cal = str(calibre or "").strip() or "NA"
    wo = str(wo_name or "").strip() or "W.O."
    w_mm = float(ancho_mm or 0)
    h_mm = float(largo_mm or 0)
    if w_mm > 0 and h_mm > 0:
        largo_in = h_mm / 25.4
        ancho_in = w_mm / 25.4
        dims = f"{largo_in:.1f}x{ancho_in:.1f}"
        return f"RTZ{int(contador_rtz)}-{cal}-{dims}-{wo}"
    return f"RTZ{int(contador_rtz)}-{cal}-{wo}"


def nombre_rtzc_para_placa(
    contador_rtzc: int,
    calibre: str,
    wo_name: str,
    *,
    largo_mm: float | None = None,
    ancho_mm: float | None = None,
) -> str:
    """
    RTZ compensado (sobrante plasma): RTZC{n}-{esp}-{largo}x{ancho}-{wo}.
    """
    cal = str(calibre or "").strip() or "NA"
    wo = str(wo_name or "").strip() or "W.O."
    w_mm = float(ancho_mm or 0)
    h_mm = float(largo_mm or 0)
    if w_mm > 0 and h_mm > 0:
        largo_in = h_mm / 25.4
        ancho_in = w_mm / 25.4
        dims = f"{largo_in:.1f}x{ancho_in:.1f}"
        return f"RTZC{int(contador_rtzc)}-{cal}-{dims}-{wo}"
    return f"RTZC{int(contador_rtzc)}-{cal}-{wo}"


def nombre_rtzc_para_sobrante_db(
    contador_rtzc: int,
    calibre: str,
    wo_name: str,
    *,
    hoja=None,
) -> str:
    largo_mm = ancho_mm = None
    if isinstance(hoja, dict):
        largo_mm = float(hoja.get("placa_h") or hoja.get("h") or 0) or None
        ancho_mm = float(hoja.get("placa_w") or hoja.get("w") or 0) or None
    return nombre_rtzc_para_placa(
        contador_rtzc,
        calibre,
        wo_name,
        largo_mm=largo_mm,
        ancho_mm=ancho_mm,
    )


def allocar_nombre_rtzc_sobrante_db(
    contador_rtzc: dict,
    calibre: str,
    wo_name: str,
    *,
    hoja=None,
) -> str:
    n = int(contador_rtzc.get("n", 1))
    nombre = nombre_rtzc_para_sobrante_db(n, calibre, wo_name, hoja=hoja)
    contador_rtzc["n"] = n + 1
    return nombre


def nombre_rtz_para_sobrante_db(
    contador_rtz: int,
    calibre: str,
    wo_name: str,
    *,
    hoja=None,
) -> str:
    """Misma nomenclatura que los RTZ del motor (incluye largo×ancho si hay hoja)."""
    largo_mm = ancho_mm = None
    if isinstance(hoja, dict):
        largo_mm = float(hoja.get("placa_h") or hoja.get("h") or 0) or None
        ancho_mm = float(hoja.get("placa_w") or hoja.get("w") or 0) or None
    return nombre_rtz_para_placa(
        contador_rtz,
        calibre,
        wo_name,
        largo_mm=largo_mm,
        ancho_mm=ancho_mm,
    )


def allocar_nombre_rtz_sobrante_db(
    contador_rtz: dict,
    calibre: str,
    wo_name: str,
    *,
    hoja=None,
) -> str:
    n = int(contador_rtz.get("n", 1))
    nombre = nombre_rtz_para_sobrante_db(n, calibre, wo_name, hoja=hoja)
    contador_rtz["n"] = n + 1
    return nombre


def es_placa_madre_sobrante_rtz(hoja) -> bool:
    """Placa madre sobrante renombrada RTZ (láser), no RTZC plasma."""
    if hoja_excluida_de_rtz_sobrante(hoja):
        return False
    if not hoja_es_sobrante_sin_compra(hoja) or hoja_es_sobrante_plasma_compensado(hoja):
        return False
    pid = str(hoja.get("placa_id") or hoja.get("sheet_display_name") or "").strip().upper()
    return pid.startswith("RTZ") and not pid.startswith("RTZC")


def es_placa_madre_rtzc(hoja) -> bool:
    """Placa madre sobrante compensada plasma (solo Robot Plasma)."""
    if hoja_excluida_de_rtz_sobrante(hoja):
        return False
    if not hoja_es_sobrante_plasma_compensado(hoja):
        return False
    pid = str(hoja.get("placa_id") or hoja.get("sheet_display_name") or "").strip().upper()
    return pid.startswith("RTZC") or bool(hoja.get("is_rtz_plasma_sobrante"))


def sincronizar_hoja_sobrante_rtz(
    hoja: dict,
    *,
    ignorar: bool,
    contador_rtz: dict,
    calibre: str,
    wo_name: str,
    contador_rtzc: dict | None = None,
) -> None:
    """
    Placa madre con switch Sobrante: conserva geometría de placa madre pero
    adopta nomenclatura y flags RTZ (mismo flujo VSM/export que un retazo).
    """
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return

    if hoja_excluida_de_rtz_sobrante(hoja):
        _limpiar_nomenclatura_rtz_en_hoja(hoja)
        return

    if ignorar:
        original = str(hoja.get("_placa_id_antes_sobrante") or hoja.get("placa_id") or "").strip()
        if original and not original.upper().startswith("RTZ"):
            hoja["_placa_id_antes_sobrante"] = original

        es_plasma_sobrante = bool(hoja.get("plasma_compensado_manual"))

        if es_plasma_sobrante:
            contador_rtzc = contador_rtzc or {"n": 1}
            nombre_prev = str(hoja.get("_rtzc_nombre_sobrante") or "").strip()
            if nombre_prev.upper().startswith("RTZC"):
                nombre = nombre_prev
            else:
                nombre = allocar_nombre_rtzc_sobrante_db(
                    contador_rtzc, calibre, wo_name, hoja=hoja
                )
                hoja["_rtzc_nombre_sobrante"] = nombre
            hoja["is_rtz"] = True
            hoja["is_rtz_plasma_sobrante"] = True
            hoja["rtz_tipo"] = "COMPENSADO"
        else:
            nombre_prev = str(hoja.get("_rtz_nombre_sobrante") or "").strip()
            if nombre_prev.upper().startswith("RTZ") and not nombre_prev.upper().startswith("RTZC"):
                nombre = nombre_prev
            else:
                nombre = allocar_nombre_rtz_sobrante_db(
                    contador_rtz, calibre, wo_name, hoja=hoja
                )
                hoja["_rtz_nombre_sobrante"] = nombre
            hoja["is_rtz"] = True
            hoja.pop("is_rtz_plasma_sobrante", None)
            hoja.pop("rtz_tipo", None)

        hoja["placa_id"] = nombre
        hoja["sheet_display_name"] = nombre
        return

    original = str(hoja.get("_placa_id_antes_sobrante") or "").strip()
    if original:
        hoja["placa_id"] = original
    hoja.pop("sheet_display_name", None)
    hoja["is_rtz"] = False
    hoja.pop("is_rtz_plasma_sobrante", None)
    hoja.pop("rtz_tipo", None)


def sincronizar_sobrantes_rtz_en_resultados(
    resultados: dict,
    *,
    wo_name: str,
) -> None:
    """Aplica nombres RTZ a todas las placas madre marcadas como sobrante."""
    if not isinstance(resultados, dict):
        return

    contador = inicializar_contador_rtz_sobrante(resultados)
    contador_rtzc = inicializar_contador_rtzc_sobrante(resultados)
    for clave, info in resultados.items():
        if not isinstance(info, dict) or "error" in info:
            continue
        calibre = str(clave).split("_", 1)[0].strip() or "NA"
        for hoja in info.get("hojas", []) or []:
            if hoja_es_sobrante_sin_compra(hoja):
                sincronizar_hoja_sobrante_rtz(
                    hoja,
                    ignorar=True,
                    contador_rtz=contador,
                    contador_rtzc=contador_rtzc,
                    calibre=calibre,
                    wo_name=wo_name,
                )


def _max_indice_rtzc_en_resultados(resultados) -> int:
    import re

    max_n = 0
    for info_mat in (resultados or {}).values():
        if not isinstance(info_mat, dict):
            continue
        for hoja in info_mat.get("hojas", []) or []:
            if not isinstance(hoja, dict):
                continue
            for campo in ("placa_id", "sheet_display_name"):
                texto = str(hoja.get(campo) or "").strip()
                m = re.match(r"RTZC(\d+)", texto, re.IGNORECASE)
                if m:
                    max_n = max(max_n, int(m.group(1)))
    return max_n


def inicializar_contador_rtzc_sobrante(resultados) -> dict:
    return {"n": _max_indice_rtzc_en_resultados(resultados) + 1}


def _max_indice_rtz_en_resultados(resultados) -> int:
    import re

    max_n = 0
    for info_mat in (resultados or {}).values():
        if not isinstance(info_mat, dict):
            continue
        for hoja in info_mat.get("hojas", []) or []:
            if not isinstance(hoja, dict):
                continue
            for campo in ("placa_id", "sheet_display_name"):
                texto = str(hoja.get(campo) or "").strip()
                m = re.match(r"RTZC(\d+)", texto, re.IGNORECASE)
                if m:
                    continue
                m = re.match(r"RTZ(\d+)", texto, re.IGNORECASE)
                if m:
                    max_n = max(max_n, int(m.group(1)))
    return max_n


def inicializar_contador_rtz_sobrante(resultados) -> dict:
    """Contador RTZ para placas sobrante, continuando tras RTZ ya nesteados."""
    return {"n": _max_indice_rtz_en_resultados(resultados) + 1}


def actualizar_eficiencias_resultados(resultados_nesting) -> None:
    if not isinstance(resultados_nesting, dict):
        return
    for _, grupo in resultados_nesting.items():
        if not isinstance(grupo, dict) or "error" in grupo:
            continue
        hojas = grupo.get("hojas")
        if not isinstance(hojas, list):
            continue
        grupo.update(calcular_eficiencias_grupo(hojas))
