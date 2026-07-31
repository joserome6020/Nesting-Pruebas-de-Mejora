"""Poka-yoke de integridad de nest (inventario, solapes, kerf/margin, pack faults).

Helpers compartidos para no dejar pasar nests incompletos, solapados o con
configuración inválida hacia UI/export.
"""
from __future__ import annotations

import os
from typing import Any, Optional


class PackEngineFault(RuntimeError):
    """El motor nativo falló; no confundir con 'no cabe nada'."""


def allow_incomplete_nest() -> bool:
    """Escape shop: ARGA_ALLOW_INCOMPLETE_NEST=1 permite aviso en vez de error."""
    return str(os.environ.get("ARGA_ALLOW_INCOMPLETE_NEST", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def validar_kerf_in(kerf: float, *, default: float = 0.15) -> tuple[float, Optional[str]]:
    """
    Returns (kerf_ok, error_msg).
    No coerce silencioso: si es inválido, error_msg != None.
    """
    try:
        k = float(kerf)
    except Exception:
        return float(default), f"Kerf no numérico ({kerf!r}). Use pulgadas > 0 (ej. 0.15)."
    if k <= 0:
        return float(default), f"Kerf inválido ({k}). Debe ser > 0 in."
    if k > 2.0:
        return float(default), f"Kerf fuera de rango ({k} in). Máximo razonable: 2.0 in."
    return k, None


def validar_margin_in(margin: float) -> tuple[float, Optional[str]]:
    try:
        m = float(margin)
    except Exception:
        return 0.0, f"Margin no numérico ({margin!r})."
    if m < 0:
        return 0.0, f"Margin inválido ({m}). No puede ser negativo."
    if m > 5.0:
        return 0.0, f"Margin fuera de rango ({m} in). Máximo razonable: 5.0 in."
    return m, None


def marcar_pack_fault(hoja: dict, motivo: str) -> dict:
    out = dict(hoja or {})
    out["_pack_engine_fault"] = str(motivo or "unknown")
    out.setdefault("piezas", [])
    out.setdefault("area_usada", 0.0)
    out.setdefault("eficiencia", 0.0)
    return out


def es_pack_fault(hoja: Any) -> bool:
    return isinstance(hoja, dict) and bool(hoja.get("_pack_engine_fault"))


def motivo_pack_fault(hoja: Any) -> str:
    if not isinstance(hoja, dict):
        return ""
    return str(hoja.get("_pack_engine_fault") or "")


def validar_solapes_hojas_fail_closed(
    hojas,
    *,
    incluir_retazos: bool = True,
) -> tuple[bool, str]:
    """
    True, "" si OK.
    False, msg si hay solape O si el validador no pudo correr (fail-closed).

    Por defecto incluye hojas RTZ reales (es_retazo). Solo omite overlays
    virtuales (cu_rtz_virtual) que no son metal de corte.
    """
    from .sheet_integrity import hoja_tiene_solapes_metal

    for hx in hojas or []:
        if not isinstance(hx, dict):
            continue
        if hx.get("cu_rtz_virtual"):
            continue
        if hx.get("es_retazo") and not incluir_retazos:
            continue
        try:
            solapa, detalle = hoja_tiene_solapes_metal(hx)
        except Exception as exc:
            return False, f"No se pudo validar solapes: {exc}"
        det = str(detalle or "")
        if det.startswith("validacion_solape_no_disponible"):
            return False, (
                "Validación de solapes no disponible (fail-closed). "
                f"Detalle: {det}"
            )
        if solapa:
            tipo = "RTZ" if hx.get("es_retazo") else "placa"
            return False, (
                f"Piezas solapadas en {tipo} {hx.get('placa_id')}: {det}"
            )
    return True, ""


def es_resultado_grupo_fallido(resultado) -> bool:
    """True si el dict de grupo (o mapa de grupos) tiene fallo duro de integridad."""
    if not isinstance(resultado, dict):
        return True
    if resultado.get("error") or resultado.get("inventario_incompleto"):
        return True
    # Mapa multi-calibre (sin hojas propias): revisar hijos.
    if "hojas" not in resultado:
        for k, v in resultado.items():
            if str(k).startswith("_"):
                continue
            if isinstance(v, dict) and (
                v.get("error") or v.get("inventario_incompleto")
            ):
                return True
    return False


def aplicar_resultado_inventario(
    grupo: dict,
    *,
    ok_inv: bool,
    msg_inv: str,
) -> None:
    """
    Poka-yoke: inventario incompleto → error duro (salvo escape env).
    """
    if not isinstance(grupo, dict):
        return
    if ok_inv:
        grupo.pop("advertencia", None)
        grupo.pop("inventario_incompleto", None)
        return
    grupo["inventario_incompleto"] = True
    if allow_incomplete_nest():
        grupo["advertencia"] = msg_inv
        grupo.pop("error", None)
    else:
        grupo["error"] = (
            f"{msg_inv} "
            "(Poka-yoke: nest incompleto rechazado. "
            "Para forzar aviso: ARGA_ALLOW_INCOMPLETE_NEST=1)"
        )
        grupo["advertencia"] = msg_inv


def edad_cache_placas_horas() -> tuple[float | None, str]:
    """
    Edad del snapshot Herinox de placas en horas.
    Returns (horas, fuente). horas=None si no hay meta usable.
    """
    try:
        from modules.herinox_catalog_cache import cargar_snapshot_placas

        _emp, _prov, meta = cargar_snapshot_placas()
    except Exception as exc:
        return None, f"sin_cache:{exc}"
    meta = meta or {}
    raw = meta.get("updated_at")
    if not raw:
        return None, str(meta.get("source") or "sin_updated_at")
    try:
        from datetime import datetime, timezone

        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        return max(0.0, float(age_h)), str(meta.get("source") or "cache")
    except Exception as exc:
        return None, f"parse_error:{exc}"


def validar_stock_antes_nest(
    datos_placas,
    *,
    max_age_hours: float = 24.0,
) -> tuple[bool, str, str]:
    """
    Poka-yoke stock: sin placas → bloquea; cache vieja → aviso (ok=True, nivel=warn).
    Returns (puede_continuar, mensaje, nivel) donde nivel in {"ok","warn","block"}.
    """
    if not datos_placas:
        return (
            False,
            "No hay placas DISPONIBLE en inventario. "
            "Sincronice Herinox / revise stock antes de nestear.",
            "block",
        )
    age_h, fuente = edad_cache_placas_horas()
    if age_h is None:
        return (
            True,
            f"No se pudo verificar frescura del cache de placas ({fuente}). "
            "Confirme sync Herinox si el stock puede estar desactualizado.",
            "warn",
        )
    if age_h > float(max_age_hours):
        return (
            True,
            f"Cache de placas Herinox tiene ~{age_h:.1f} h ({fuente}). "
            f"Umbral recomendado: {max_age_hours:.0f} h. "
            "Sincronice stock antes de nestear si hubo movimientos recientes.",
            "warn",
        )
    return True, "", "ok"


def _extraer_numero_placa(valor) -> float:
    try:
        if isinstance(valor, (int, float)):
            return float(valor)
        limpio = str(valor).replace("$", "").replace(",", ".").strip()
        import re

        nums = re.findall(r"[-+]?\d*\.\d+|\d+", limpio)
        return float(nums[0]) if nums else 0.0
    except Exception:
        return 0.0


def _agrupar_partes_por_clave(lista_partes) -> dict[str, list]:
    grupos: dict[str, list] = {}
    for item in lista_partes or []:
        try:
            pieza, mat, qty, cal, _st, ruta = item
        except Exception:
            continue
        clave = f"{str(cal).strip().upper()}_{str(mat).strip().upper()}"
        try:
            q = max(0, int(qty or 0))
        except Exception:
            q = 0
        if q <= 0:
            continue
        grupos.setdefault(clave, []).append(
            {
                "nombre": str(pieza or "").strip(),
                "material": str(mat or "").strip(),
                "calibre": str(cal or "").strip(),
                "qty": q,
                "ruta": str(ruta or "").strip(),
            }
        )
    return grupos


def _placas_matching_grupo(
    datos_placas,
    req_cal: str,
    req_mat: str,
    *,
    coinciden,
) -> list[dict]:
    """Misma regla que el motor: SOLO calibre exacto (sin tolerancia %)."""
    try:
        from .manager import MotorNesting

        motor = MotorNesting.__new__(MotorNesting)
        placas, _mode = motor._clasificar_placas_por_calibre(
            req_cal, req_mat, datos_placas
        )
        return [
            {
                "id": p.get("id", ""),
                "w": p["w"],
                "h": p["h"],
                "origen": p.get("origen", "EMPRESA"),
                "w_in": float(p["w"]) / 25.4,
                "h_in": float(p["h"]) / 25.4,
            }
            for p in placas
        ]
    except Exception:
        pass

    # Fallback legacy (exacto vía coinciden)
    placas_empresa: list[dict] = []
    placas_proveedor: list[dict] = []
    for placa in datos_placas or []:
        try:
            p_cal = placa[0] if len(placa) > 0 else ""
            p_mat = placa[1] if len(placa) > 1 else ""
        except Exception:
            continue
        if not coinciden(req_cal, p_cal) or not coinciden(req_mat, p_mat):
            continue
        w_in = _extraer_numero_placa(placa[3] if len(placa) > 3 else 0)
        h_in = _extraer_numero_placa(placa[4] if len(placa) > 4 else 0)
        if w_in <= 0 or h_in <= 0:
            continue
        origen = str(placa[9]).upper() if len(placa) > 9 else "EMPRESA"
        row = {
            "id": str(placa[2]) if len(placa) > 2 else "",
            "w": w_in * 25.4,
            "h": h_in * 25.4,
            "origen": origen,
            "w_in": w_in,
            "h_in": h_in,
        }
        if "EMPRESA" in origen or origen.strip() == "":
            placas_empresa.append(row)
        else:
            placas_proveedor.append(row)
    return placas_empresa if placas_empresa else placas_proveedor


def validar_stock_por_grupos_antes_nest(
    lista_partes,
    datos_placas,
    *,
    coinciden,
    solo_claves: set[str] | None = None,
) -> tuple[bool, str]:
    """
    Poka-yoke INPUT por calibre/material:
    - cada grupo de PARTS debe tener inventario matching
    - cobre: barras largos 144\"×1.75–6\"
    """
    if not lista_partes:
        return False, "No hay piezas en PARTS para nestear."
    if not datos_placas:
        return False, "No hay placas DISPONIBLE en inventario."

    try:
        from .cu_inventory import inventario_barras_largos_cu
    except Exception:
        inventario_barras_largos_cu = None  # type: ignore

    try:
        from interface.utils_nesting import es_material_cobre
    except Exception:
        def es_material_cobre(m):  # type: ignore
            mu = str(m or "").strip().upper()
            return mu in ("CU", "COBRE", "COPPER") or "COBRE" in mu or "COPPER" in mu

    grupos = _agrupar_partes_por_clave(lista_partes)
    if solo_claves:
        grupos = {k: v for k, v in grupos.items() if k in solo_claves}
    if not grupos:
        return False, "No hay grupos calibre/material válidos en PARTS."

    fallas: list[str] = []
    for clave, filas in sorted(grupos.items()):
        partes = str(clave).split("_", 1)
        req_cal = partes[0]
        req_mat = partes[1] if len(partes) > 1 else ""
        n_pz = sum(int(f.get("qty") or 0) for f in filas)
        placas = _placas_matching_grupo(
            datos_placas, req_cal, req_mat, coinciden=coinciden
        )
        if not placas:
            fallas.append(
                f"{clave}: sin inventario de placas para {req_cal} / {req_mat} "
                f"({n_pz} pieza(s) en PARTS)."
            )
            continue
        mat_u = str(req_mat).strip().upper()
        if mat_u == "CU" or es_material_cobre(req_mat):
            if inventario_barras_largos_cu is None:
                continue
            barras = inventario_barras_largos_cu(placas)
            if not barras:
                fallas.append(
                    f"{clave}: hay stock CU pero ninguna barra 144\"×1.75–6\" "
                    f"para largos ({n_pz} pieza(s))."
                )

    if fallas:
        texto = "\n".join(f"• {f}" for f in fallas[:10])
        if len(fallas) > 10:
            texto += f"\n(+{len(fallas) - 10} grupos más)"
        return (
            False,
            "Poka-yoke: hay calibres/materiales sin stock usable.\n"
            "Corrija inventario Herinox o quite esas piezas de PARTS "
            "antes de nestear/renestear.\n\n"
            f"{texto}",
        )
    return True, ""


def listar_fallas_resultados_nest(resultados) -> list[str]:
    """Errores / inventario incompleto / solapes en un dict de grupos."""
    fallas: list[str] = []
    if not isinstance(resultados, dict):
        return ["Resultado de nesting inválido."]
    # Payload top-level de error (p. ej. aborto DXF).
    top_err = resultados.get("error")
    if isinstance(top_err, str) and top_err.strip() and "hojas" not in resultados:
        fallas.append(top_err.strip())
    for clave, info in resultados.items():
        if str(clave).startswith("_"):
            continue
        if not isinstance(info, dict):
            continue
        if info.get("error"):
            fallas.append(f"{clave}: {info.get('error')}")
            continue
        if info.get("inventario_incompleto"):
            fallas.append(
                f"{clave}: {info.get('advertencia') or 'inventario incompleto'}"
            )
            continue
        if "hojas" not in info:
            continue
        ok_s, msg_s = validar_solapes_hojas_fail_closed(info.get("hojas") or [])
        if not ok_s:
            fallas.append(f"{clave}: {msg_s}")
            continue
        ok_b, msg_b = validar_piezas_dentro_placas(info.get("hojas") or [])
        if not ok_b:
            fallas.append(f"{clave}: {msg_b}")
    return fallas


def validar_piezas_dentro_placa(hoja, *, tol_mm: float = 1.5) -> tuple[bool, str]:
    """True si todas las piezas reales caben en el bbox de la placa."""
    if not isinstance(hoja, dict) or hoja.get("cu_rtz_virtual"):
        return True, ""
    try:
        w = float(hoja.get("placa_w") or 0.0)
        h = float(hoja.get("placa_h") or 0.0)
    except Exception:
        return True, ""
    if w <= 0 or h <= 0:
        return True, ""

    from .geometry_parser import reconstruir_poly_seguro
    from .sheet_integrity import piezas_reales_en_hoja

    tol = float(tol_mm)
    for p in piezas_reales_en_hoja(hoja):
        try:
            poly = reconstruir_poly_seguro(p.get("poligonos") or [])
            if poly is None or poly.is_empty:
                continue
            minx, miny, maxx, maxy = poly.bounds
        except Exception as exc:
            return False, (
                f"validacion_bbox_no_disponible:{p.get('nombre')}:{exc}"
            )
        if (
            minx < -tol
            or miny < -tol
            or maxx > w + tol
            or maxy > h + tol
        ):
            return False, (
                f"Pieza fuera de placa {hoja.get('placa_id')}: "
                f"{p.get('nombre')} "
                f"bounds=({minx:.1f},{miny:.1f})-({maxx:.1f},{maxy:.1f}) "
                f"placa={w:.1f}x{h:.1f} mm"
            )
    return True, ""


def validar_piezas_dentro_placas(hojas, *, tol_mm: float = 1.5) -> tuple[bool, str]:
    for hx in hojas or []:
        ok, msg = validar_piezas_dentro_placa(hx, tol_mm=tol_mm)
        if not ok:
            if str(msg).startswith("validacion_bbox_no_disponible"):
                return False, f"Validación bbox no disponible (fail-closed): {msg}"
            return False, msg
    return True, ""


def validar_integridad_bloque_hojas(hojas) -> tuple[bool, str]:
    """Solapes (incl. RTZ) + piezas dentro de placa. Fail-closed."""
    ok_s, msg_s = validar_solapes_hojas_fail_closed(hojas)
    if not ok_s:
        return False, msg_s
    return validar_piezas_dentro_placas(hojas)


def validar_auditoria_dxf_antes_nest(
    audit: dict | None,
    *,
    pending: bool = False,
) -> tuple[bool, str]:
    """
    Poka-yoke input: no arrancar nest caro si la auditoría PARTS aún corre
    o hay DXF omitidos (ruta faltante / ilegible / sin geometría).
    """
    if pending:
        return (
            False,
            "La auditoría de DXF aún está en curso (DXF NESTEO: validando…).\n"
            "Espere a que termine antes de nestear para no desperdiciar tiempo "
            "re-parseando piezas inválidas.",
        )
    if not isinstance(audit, dict):
        return (
            False,
            "No hay auditoría de DXF disponible.\n"
            "Vuelva a PARTS / reimporte el job para validar geometría antes de nestear.",
        )
    total = int(audit.get("total") or 0)
    omitidos = list(audit.get("omitidos") or [])
    if total <= 0:
        return False, "No hay piezas/DXF auditados en PARTS."
    if omitidos:
        lineas = []
        for item in omitidos[:8]:
            if not isinstance(item, dict):
                lineas.append(str(item))
                continue
            pieza = item.get("pieza") or item.get("archivo") or "?"
            err = item.get("error") or "sin detalle"
            lineas.append(f"• {pieza}: {err}")
        extra = ""
        if len(omitidos) > 8:
            extra = f"\n(+{len(omitidos) - 8} más — use VER OMITIDOS en PARTS)"
        return (
            False,
            f"{len(omitidos)} DXF no apto(s) para nesting (de {total}).\n"
            "Repare o cambie esos DXF en PARTS (VER OMITIDOS / REEMPLAZAR DXF)\n"
            "antes de nestear o renestear.\n\n"
            + "\n".join(lineas)
            + extra,
        )
    ok = int(audit.get("ok") or 0)
    if ok <= 0:
        return False, "Ningún DXF pasó la auditoría de geometría."
    if ok < total:
        return (
            False,
            f"Auditoría inconsistente: ok={ok} total={total} sin lista de omitidos.",
        )
    return True, ""


def validar_step_cama_ab_pares(
    step_dir_a: str,
    step_dir_b: str,
    *,
    etiqueta: str = "ROBOT",
    motor_label: str = "STEP",
) -> tuple[bool, str]:
    """
    Poka-yoke: Cama A y Cama B deben tener la misma cantidad de .step
    (mismo set de DXF robot exportados a ambos destinos).
    """
    import glob

    def _count(folder: str) -> int:
        folder = os.path.normpath(str(folder or "").strip())
        if not folder or not os.path.isdir(folder):
            return 0
        files = glob.glob(os.path.join(folder, "*.step"))
        files.extend(glob.glob(os.path.join(folder, "*.STEP")))
        vistos = set()
        n = 0
        for p in files:
            key = os.path.normcase(p)
            if key in vistos:
                continue
            vistos.add(key)
            try:
                if os.path.getsize(p) < 64:
                    continue
            except OSError:
                continue
            n += 1
        return n

    na = _count(step_dir_a)
    nb = _count(step_dir_b)
    if na == 0 and nb == 0:
        return True, ""
    if na != nb:
        return (
            False,
            f"{motor_label} poka-yoke [{etiqueta}]: Cama A tiene {na} STEP y "
            f"Cama B tiene {nb}. Deben coincidir (mismo DXF → A y B).",
        )
    return True, ""
