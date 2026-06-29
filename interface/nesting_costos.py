"""Cálculo en vivo de costos del nesteo actual (independiente del historial WO)."""
from __future__ import annotations

from typing import Any

from modules.nesting_engine.efficiency_metrics import hoja_cuenta_para_deduccion
try:
    from utils_nesting import format_clave_calibre_display
except ImportError:
    from interface.utils_nesting import format_clave_calibre_display


def _precio_hoja(hoja: dict) -> float:
    return float(hoja.get("precio_placa") or hoja.get("precio") or 0.0)


def recalcular_costos_grupo(info: dict) -> None:
    """Actualiza costo_total / costo_empresa / costo_proveedor en un grupo."""
    if not isinstance(info, dict):
        return
    hojas = info.get("hojas") or []
    costo_total = costo_emp = costo_prov = 0.0
    for h in hojas:
        if not hoja_cuenta_para_deduccion(h):
            continue
        p = _precio_hoja(h)
        costo_total += p
        if str(h.get("origen_placa") or "EMPRESA").upper() == "PROVEEDOR":
            costo_prov += p
        else:
            costo_emp += p
    info["costo_total"] = costo_total
    info["costo_empresa"] = costo_emp
    info["costo_proveedor"] = costo_prov


def sincronizar_costos_resultados(resultados: dict | None) -> None:
    if not isinstance(resultados, dict):
        return
    for info in resultados.values():
        if isinstance(info, dict):
            recalcular_costos_grupo(info)


def _etiqueta_origen(costo_emp: float, costo_prov: float) -> str:
    if costo_emp > 0 and costo_prov == 0:
        return "EMP"
    if costo_prov > 0 and costo_emp == 0:
        return "PROV"
    if costo_emp > 0 and costo_prov > 0:
        return "MIX"
    return "RET"


def calcular_costos_largos_desde_app(
    app,
    lote_idx: int | None = None,
    *,
    tab=None,
) -> dict[str, Any]:
    """Costo MRL de largos del lote activo (respeta exclusiones del modal largos)."""
    if app is None:
        return {"total_mxn": 0.0, "barras_total": 0, "lineas": []}
    try:
        from catalogo_largos import (
            _cargar_placas_largos_desde_herinox,
            etiqueta_tipo_perfil_mrl,
        )
        from interface.largos_nesting_service import (
            obtener_exclusiones_mrl_unidades,
            previsualizar_pedido_mrl,
            resolver_plan_largos_para_tab,
            resumir_plan_largos,
        )
    except ImportError:
        return {"total_mxn": 0.0, "barras_total": 0, "lineas": []}

    plan = None
    idx = int(lote_idx if lote_idx is not None else 0)
    if tab is not None:
        plan = resolver_plan_largos_para_tab(app, tab)
        idx = int(getattr(tab, "lote_actual_idx", idx) or idx)
    if not plan:
        planes = getattr(app, "plan_largos_por_lote", None) or {}
        plan = planes.get(idx)
    if not isinstance(plan, dict) or not (plan.get("data") or {}):
        return {"total_mxn": 0.0, "barras_total": 0, "lineas": []}

    excl = obtener_exclusiones_mrl_unidades(app, idx)
    filas = previsualizar_pedido_mrl(plan, unidades_excluidas_mrl=excl)
    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)

    resumen = resumir_plan_largos(plan, unidades_excluidas_mrl=excl)
    barras = int(resumen.get("mrl_barras_comerciales") or 0)
    if barras <= 0:
        barras = sum(max(0, int(f.get("cantidad") or 0)) for f in filas)

    lineas: list[dict[str, Any]] = []
    total = 0.0
    for fila in filas:
        costo = float(fila.get("costo") or 0.0)
        cant = max(0, int(fila.get("cantidad") or 0))
        if cant <= 0 and costo <= 0:
            continue
        total += costo
        mat = str(fila.get("material") or "").strip()
        cod = str(fila.get("codigo") or "").strip()
        tipo = etiqueta_tipo_perfil_mrl(mat, cod, catalogo=catalogo)
        largo = float(fila.get("largo") or 0.0)
        clave_display = f"{tipo} · {cod} · {largo:.0f}\"" if cod else f"{tipo} · {largo:.0f}\""
        lineas.append(
            {
                "clave": mat or cod or clave_display,
                "clave_display": clave_display,
                "etiqueta": "LARGOS",
                "tipo_linea": "largos",
                "placas": cant,
                "barras": cant,
                "costo_mxn": costo,
                "costo_empresa_mxn": costo,
                "costo_proveedor_mxn": 0.0,
            }
        )
    lineas.sort(key=lambda r: (str(r.get("clave_display") or ""), str(r.get("clave") or "")))
    return {"total_mxn": total, "barras_total": barras, "lineas": lineas}


def calcular_reporte_costos(
    resultados: dict | None,
    *,
    app=None,
    lote_idx: int | None = None,
    tab=None,
) -> dict[str, Any]:
    """
    Reporte del estado ACTUAL de resultados_nesting (post-movimientos / renest)
    más largos del lote activo si hay plan y app disponible.
    """
    sincronizar_costos_resultados(resultados)
    lineas: list[dict[str, Any]] = []
    total_mxn = total_emp = 0.0
    placas_total = 0

    if isinstance(resultados, dict):
        for clave in sorted(resultados.keys()):
            info = resultados.get(clave)
            if not isinstance(info, dict):
                continue
            hojas = info.get("hojas") or []
            costo_emp = 0.0
            n_emp = 0
            for h in hojas:
                if not hoja_cuenta_para_deduccion(h):
                    continue
                p = _precio_hoja(h)
                if str(h.get("origen_placa") or "EMPRESA").upper() == "PROVEEDOR":
                    continue
                placas_total += 1
                costo_emp += p
                n_emp += 1
            total_emp += costo_emp
            total_mxn += costo_emp
            if costo_emp > 0 or n_emp > 0:
                lineas.append(
                    {
                        "clave": clave,
                        "clave_display": format_clave_calibre_display(clave),
                        "etiqueta": "EMP",
                        "tipo_linea": "placa",
                        "placas": n_emp,
                        "barras": 0,
                        "costo_mxn": costo_emp,
                        "costo_empresa_mxn": costo_emp,
                        "costo_proveedor_mxn": 0.0,
                    }
                )

    largos = calcular_costos_largos_desde_app(app, lote_idx, tab=tab)
    total_largos = float(largos.get("total_mxn") or 0.0)
    barras_largos = int(largos.get("barras_total") or 0)
    total_mxn += total_largos
    total_emp += total_largos
    lineas.extend(largos.get("lineas") or [])

    return {
        "total_mxn": total_mxn,
        "total_empresa_mxn": total_emp,
        "total_proveedor_mxn": 0.0,
        "total_placas_mxn": total_mxn - total_largos,
        "total_largos_mxn": total_largos,
        "placas_total": placas_total,
        "barras_largos_total": barras_largos,
        "lineas": lineas,
        "grupos": len(lineas),
    }


def aplicar_totales_a_tab(tab, reporte: dict[str, Any], tc: float) -> None:
    """Sincroniza costo_mxn_val / costo_usd_val del tab con el reporte en vivo."""
    total = float(reporte.get("total_mxn") or 0.0)
    tab.costo_mxn_val = total
    tab.costo_usd_val = (total / tc) if tc > 0 else 0.0
    tab.total_usd_empresa = float(reporte.get("total_empresa_mxn") or 0.0)
    tab.total_usd_proveedor = 0.0
