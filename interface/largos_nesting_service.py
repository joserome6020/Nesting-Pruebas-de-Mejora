"""Carga plan de lista de largos y sincroniza pedido material_requerido_ldg."""
from __future__ import annotations

from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

import config
from lista_largos_material_requerido import (
    asegurar_tabla_material_requerido_ldg,
    reconstruir_pedido_desde_plan,
)


def _db_config() -> dict:
    return {
        "host": getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
        "database": getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
        "user": getattr(config, "NESTING_DB_USER", "postgres"),
        "password": getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
        "port": getattr(config, "NESTING_DB_PORT", "5433"),
    }


def _factor_lote(tab) -> int:
    try:
        if tab is not None and hasattr(tab, "_multiplicador_lote_activo"):
            return max(1, int(tab._multiplicador_lote_activo()))
    except Exception:
        pass
    try:
        return max(1, int(getattr(tab.app if tab else None, "multiplicador_tanques", 1) or 1))
    except Exception:
        return 1


def resolver_orden_largos(app, tab) -> tuple[str, str] | None:
    """WO/SWO real en BD (tras exportar o job SWO)."""
    job = str(getattr(app, "job_activo", "") or "").strip().upper()
    if job.startswith("SWO"):
        return job, "SWO"

    wo_map = getattr(app, "wo_reales_por_lote", None) or {}
    idx = int(getattr(tab, "lote_actual_idx", 0) or 0)
    wo = str(wo_map.get(idx) or "").strip()
    if wo:
        return wo, "WO"
    return None


def resolver_contexto_largos(app, tab) -> dict[str, Any]:
    """Contexto para nesteo de largos: orden real o vista previa por job/lote."""
    job = str(getattr(app, "job_activo", "") or "").strip()
    factor = _factor_lote(tab)
    ctx_orden = resolver_orden_largos(app, tab)
    if ctx_orden:
        orden_id, tipo_orden = ctx_orden
        return {
            "modo": "orden",
            "job": job,
            "factor_lote": factor,
            "orden_id": orden_id,
            "tipo_orden": tipo_orden,
            "puede_enviar_pedido": True,
            "etiqueta": f"ORDEN: {orden_id} · {tipo_orden}",
        }
    return {
        "modo": "preview",
        "job": job,
        "factor_lote": factor,
        "orden_id": "",
        "tipo_orden": "WO",
        "puede_enviar_pedido": False,
        "etiqueta": f"VISTA PREVIA · JOB: {job or '—'} · LOTE X{factor}",
    }


def bar_key(material: str, bar_idx: int) -> str:
    return f"{material}::{int(bar_idx)}"


KERF_LARGOS_IN = 0.25
RECORTE_EXTREMO_LARGOS_IN = 0.5


def _etiquetar_cortes_individuales(cortes: list[dict]) -> list[dict]:
    """Cada pieza es un tramo propio; si hay repetidas (mismo nombre/largo), numerar #1 #2…"""
    import copy
    from collections import Counter

    expandidos: list[dict] = []
    for corte in cortes or []:
        base = copy.deepcopy(corte)
        qty = max(1, int(base.pop("cantidad", 1) or 1))
        for _ in range(qty):
            expandidos.append(copy.deepcopy(base))

    claves = [
        (str(c.get("nombre") or "").strip(), round(float(c.get("largo") or 0), 3))
        for c in expandidos
    ]
    totales = Counter(claves)
    serial: dict[tuple[str, float], int] = {}

    for corte, clave in zip(expandidos, claves):
        nom = str(corte.get("nombre") or "Pieza").strip()
        if totales[clave] > 1:
            serial[clave] = serial.get(clave, 0) + 1
            corte["_etiqueta_pieza"] = f"{nom}  #{serial[clave]}"
        else:
            corte["_etiqueta_pieza"] = nom
        corte["_corte_uid"] = id(corte)

    return expandidos


def _split_cortes_en_unidades_comerciales(
    cortes: list[dict],
    largo_comercial: float,
    n_unidades: int,
) -> list[list[dict]]:
    """Reparte piezas (una a una) llenando cada barra comercial antes de la siguiente."""
    import copy

    piezas = _etiquetar_cortes_individuales(cortes)
    if n_unidades <= 1:
        return [piezas]

    util = max(0.0, float(largo_comercial) - 2 * RECORTE_EXTREMO_LARGOS_IN)
    buckets: list[list[dict]] = [[] for _ in range(n_unidades)]
    used = [0.0] * n_unidades
    bucket_idx = 0

    for corte in piezas:
        largo = float(corte.get("largo") or 0)
        if largo <= 0:
            continue
        while bucket_idx < n_unidades - 1:
            kerf = KERF_LARGOS_IN if buckets[bucket_idx] else 0.0
            if not buckets[bucket_idx] or used[bucket_idx] + kerf + largo <= util + 0.02:
                break
            bucket_idx += 1

        kerf = KERF_LARGOS_IN if buckets[bucket_idx] else 0.0
        buckets[bucket_idx].append(copy.deepcopy(corte))
        used[bucket_idx] += kerf + largo
        if bucket_idx < n_unidades - 1 and used[bucket_idx] >= util - 0.02:
            bucket_idx += 1

    return buckets


def _remanente_barra_vista(largo_stock: float, cortes: list[dict]) -> float:
    used = RECORTE_EXTREMO_LARGOS_IN
    for i, corte in enumerate(cortes or []):
        if i > 0:
            used += KERF_LARGOS_IN
        used += float(corte.get("largo") or 0)
    used += RECORTE_EXTREMO_LARGOS_IN
    return max(0.0, float(largo_stock) - used)


def vista_barra_para_unidad_mrl(
    barra: dict,
    largo_comercial: float,
    unit_idx: int,
    *,
    reparto_greedy: bool = False,
    n_unidades_tira: int = 1,
    cant_grupo: int = 1,
) -> dict:
    """
    Vista de consumo para una barra comercial MRL (siempre largo comercial, ej. 240\").
    Si el nesteo usó una tira más larga (ej. 480\"), reparte piezas entre unidades
    llenando primero la barra #1 sin fusionar piezas iguales.
    """
    import copy

    stock_origen = float(barra.get("largo_stock") or 0)
    largo_com = float(largo_comercial or 0)
    if largo_com <= 0:
        return dict(barra)

    cortes_origen = list(barra.get("cortes") or [])
    n_comercial = max(1, int(cant_grupo or 1))

    if stock_origen > largo_com + 0.5:
        n_slices = max(n_comercial, int(round(stock_origen / largo_com)))
        splits = _split_cortes_en_unidades_comerciales(cortes_origen, largo_com, n_slices)
        idx = min(max(0, int(unit_idx) - 1), len(splits) - 1)
        cortes_vista = splits[idx]
        largo_vista = largo_com
        nota_nesteo = (
            f"Piezas en tira nesteo de {stock_origen:.0f}\" "
            f"→ barra comercial {int(unit_idx)}/{n_comercial} de {largo_com:.0f}\""
        )
    elif reparto_greedy and int(n_unidades_tira) > 1:
        splits = _split_cortes_en_unidades_comerciales(
            cortes_origen, largo_com or stock_origen, int(n_unidades_tira)
        )
        idx = min(max(0, int(unit_idx) - 1), len(splits) - 1)
        cortes_vista = splits[idx]
        largo_vista = largo_com if largo_com > 0 else stock_origen
        nota_nesteo = ""
    else:
        cortes_vista = _etiquetar_cortes_individuales(cortes_origen)
        # Vista MRL = barra comercial a comprar, no la tira física del nesteo.
        largo_vista = largo_com if largo_com > 0 else stock_origen
        nota_nesteo = ""
        if (
            stock_origen > 0
            and largo_com > 0
            and abs(stock_origen - largo_com) > 0.5
            and stock_origen < largo_com
        ):
            nota_nesteo = (
                f"Nesteo sobre tira de {stock_origen:.0f}\" "
                f"→ barra comercial {int(unit_idx)}/{int(cant_grupo or 1)} "
                f"de {largo_com:.0f}\""
            )

    rem = _remanente_barra_vista(largo_vista, cortes_vista)
    vista = copy.deepcopy(barra)
    vista["largo_stock"] = largo_vista
    vista["cortes"] = cortes_vista
    vista["remanente_show"] = rem
    vista["remanente_calc"] = rem
    vista["_vista_comercial_unidad"] = int(unit_idx)
    vista["_vista_cant_grupo"] = int(cant_grupo or 1)
    vista["_vista_largo_comercial"] = largo_com
    if nota_nesteo:
        vista["_vista_nota_nesteo"] = nota_nesteo
    return vista


def preparar_barra_para_canvas(barra: dict | None, codigo: str = "") -> dict | None:
    """Etiqueta cada pieza para el visor sin alterar el plan original."""
    import copy

    if not barra:
        return None
    vista = copy.deepcopy(barra)
    vista["cortes"] = _etiquetar_cortes_individuales(vista.get("cortes") or [])
    if codigo:
        vista["_vista_codigo"] = str(codigo).strip()
    return vista


def _filas_desde_csv_para_job(app, job: str, factor_lote: int) -> list[dict]:
    """
    Misma demanda que la pestaña DEMANDA DE LARGOS (CSV del AutoDXF del job activo).
    Estructura idéntica a _expandir_lista_para_wo.
    """
    from api_server import _extraer_factor_wo

    parts = getattr(app, "vista_parts", None)
    if parts is None or not hasattr(parts, "_cargar_listas_largos_desde_rutas"):
        return []

    job = str(job or getattr(app, "job_activo", "") or "").strip()
    job_norm = _norm_job(job)
    wo_label = f"LOTE X{max(1, int(factor_lote))}"
    factor_wo = _extraer_factor_wo(wo_label)
    filas: list[dict] = []

    for grupo in parts._cargar_listas_largos_desde_rutas():
        if grupo.get("status") != "ok":
            continue
        gjob = str(grupo.get("job") or "").strip()
        if job_norm and _norm_job(gjob) != job_norm:
            continue
        for row in grupo.get("rows") or []:
            try:
                cantidad_base = int(float(row.get("cantidad_base", row.get("cantidad")) or 0))
            except Exception:
                cantidad_base = 0
            if cantidad_base <= 0:
                continue
            try:
                largo_in = float(row.get("largo_in") or 0)
            except Exception:
                largo_in = 0.0
            if largo_in <= 0:
                continue
            cantidad_wo = cantidad_base * max(1, int(factor_wo))
            filas.append(
                {
                    "job": gjob or job,
                    "work_order": wo_label,
                    "factor_wo": factor_wo,
                    "nombre": str(row.get("nombre") or "").strip(),
                    "clasificacion": str(row.get("clasificacion") or "").strip(),
                    "largo_in": largo_in,
                    "cantidad": cantidad_wo,
                    "cantidad_base": cantidad_base,
                    "cantidad_wo": cantidad_wo,
                }
            )
    return filas


def _filas_desde_csv_app(app, factor_lote: int) -> list[dict]:
    job = str(getattr(app, "job_activo", "") or "").strip()
    return _filas_desde_csv_para_job(app, job, factor_lote)


def _filas_desde_job_bd(cursor, job: str, factor_lote: int) -> list[dict]:
    from api_server import _expandir_lista_para_wo, _obtener_lista_base_por_job

    job = str(job or "").strip()
    if not job:
        return []

    base = _obtener_lista_base_por_job(cursor, job)
    if not base:
        return []

    wo_sintetica = f"LOTE X{max(1, int(factor_lote))}"
    return _expandir_lista_para_wo(cursor, job, wo_sintetica)


def _obtener_filas_demanda_lote(app, cursor, job: str, factor_lote: int) -> tuple[list[dict], str]:
    """
    Fuente única de demanda: CSV del job (como DEMANDA DE LARGOS), luego lista_largos_job en BD.
    Retorna (filas, origen).
    """
    job = str(job or "").strip()
    rows = _filas_desde_csv_para_job(app, job, factor_lote)
    if rows:
        return rows, "csv"
    if cursor is not None and job:
        rows = _filas_desde_job_bd(cursor, job, factor_lote)
        if rows:
            return rows, "bd"
    return [], ""


def resumir_plan_largos(plan: dict[str, Any], unidades_excluidas_mrl: set[str] | None = None) -> dict[str, int]:
    """Desglose de tiras nesteo vs barras comerciales MRL."""
    stock = rem = piezas = 0
    for _mat, _idx, barra in iter_barras_plan(plan):
        src = str(barra.get("source") or "STOCK").upper()
        n = len(barra.get("cortes") or [])
        piezas += n
        if src == "REMANENTE":
            rem += 1
        else:
            stock += 1
    excl = unidades_excluidas_mrl if unidades_excluidas_mrl is not None else set()
    unidades = listar_unidades_mrl_plan(plan)
    mrl_activas = [u for u in unidades if u["key"] not in excl]
    mrl_barras = len(mrl_activas)
    filas = previsualizar_pedido_mrl(plan, unidades_excluidas_mrl=excl)
    return {
        "tiras_total": stock + rem,
        "tiras_stock": stock,
        "tiras_remanente": rem,
        "piezas": piezas,
        "mrl_filas": len(filas),
        "mrl_barras_comerciales": mrl_barras,
        "mrl_barras_total": len(unidades),
    }


def _norm_job(job: str) -> str:
    return str(job or "").strip().upper()


def _generar_plan_desde_filas(
    cursor,
    orden_id: str,
    tipo_orden: str,
    job: str,
    factor_lote: int,
    rows: list[dict],
    *,
    usar_remanentes: bool = False,
) -> dict[str, Any]:
    from api_server import _extraer_factor_wo, _ll_generar_plan_desde_payload

    if not rows:
        return {
            "orden_id": orden_id,
            "tipo_orden": tipo_orden,
            "data": {},
            "total_piezas": 0,
            "total_barras": 0,
        }

    wo_label = f"LOTE X{max(1, int(factor_lote))}"
    payload = {
        "tipo": "wo",
        "identificador": str(orden_id or "").strip(),
        "work_order": wo_label,
        "jobs": [job] if job else [],
        "factor_wo": _extraer_factor_wo(wo_label),
        "rows": rows,
    }
    plan_json, _rem = _ll_generar_plan_desde_payload(
        cursor, orden_id, tipo_orden, payload, usar_remanentes=usar_remanentes
    )
    return dict(plan_json or {})


# Referencia auditada job 62174 · LOTE X1 (mismo CSV estándar tanque OTC)
REFERENCIA_JOB_62174 = {
    "job": "62174",
    "piezas": 65,
    "tiras_stock": 11,
    "mrl_barras": 15,
    "filas_csv": 24,
}


def verificar_empate_referencia_62174(plan: dict[str, Any], filas_demanda: list[dict]) -> dict[str, Any]:
    """Compara plan actual contra job 62174 exportado (mismo algoritmo auditado)."""
    ref = REFERENCIA_JOB_62174
    res = resumir_plan_largos(plan)
    piezas_dem = sum(int(r.get("cantidad") or 0) for r in (filas_demanda or []))
    empate = (
        piezas_dem == ref["piezas"]
        and int(res.get("piezas") or 0) == ref["piezas"]
        and int(res.get("tiras_stock") or 0) == ref["tiras_stock"]
        and int(res.get("tiras_remanente") or 0) == 0
        and int(res.get("mrl_barras_comerciales") or 0) == ref["mrl_barras"]
        and len(filas_demanda or []) == ref["filas_csv"]
    )
    return {
        "empate": empate,
        "referencia": ref,
        "actual": res,
        "piezas_demanda": piezas_dem,
        "filas_demanda": len(filas_demanda or []),
    }


def cargar_plan_largos(orden_id: str, tipo_orden: str) -> dict[str, Any]:
    from api_server import _ll_obtener_o_generar_plan

    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**_db_config())
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        plan_json, _row = _ll_obtener_o_generar_plan(
            cursor, str(orden_id).strip(), str(tipo_orden).strip().upper(), reservar=False
        )
        conexion.commit()
        return dict(plan_json or {})
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def _lote_idx(tab) -> int:
    try:
        return int(getattr(tab, "lote_actual_idx", 0) or 0)
    except Exception:
        return 0


def _plan_desde_nesting(app, tab) -> dict[str, Any] | None:
    """Plan ya calculado al ejecutar nesting (por lote activo)."""
    job_actual = _norm_job(getattr(app, "job_activo", ""))
    if job_actual != _norm_job(getattr(app, "plan_largos_job", "")):
        return None
    planes = getattr(app, "plan_largos_por_lote", None) or {}
    idx = _lote_idx(tab)
    plan = planes.get(idx)
    if isinstance(plan, dict) and (plan.get("data") or {}):
        return plan
    return None


def previsualizar_pedido_mrl(
    plan: dict[str, Any],
    barras_excluidas_pedido: set[str] | None = None,
    unidades_excluidas_mrl: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Filas agregadas para material_requerido_ldg (vista resumen).
    Preferir unidades_excluidas_mrl para decisiones unitarias comerciales.
    """
    from lista_largos_material_requerido import (
        agregar_filas_desde_plan,
        agregar_filas_desde_unidades_mrl,
        previsualizar_pedido_mrl_unidades,
    )

    if unidades_excluidas_mrl is not None:
        unidades = previsualizar_pedido_mrl_unidades(plan, unidades_excluidas_mrl)
        return agregar_filas_desde_unidades_mrl(unidades)
    return agregar_filas_desde_plan(plan, barras_excluidas_pedido=barras_excluidas_pedido)


def listar_unidades_mrl_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Unidades comerciales (1 switch = 1 barra a comprar) con enlace al mapa de corte."""
    from lista_largos_material_requerido import expandir_pedido_mrl_unidades

    unidades = expandir_pedido_mrl_unidades(plan)
    stock_por_material: dict[str, list[tuple[str, dict]]] = {}
    for material, bar_idx, barra in iter_barras_plan(plan):
        if str(barra.get("source") or "STOCK").upper() != "STOCK":
            continue
        mat = str(material or "").strip()
        stock_por_material.setdefault(mat, []).append((bar_key(material, bar_idx), barra))

    por_material_cant: dict[str, int] = {}
    for u in unidades:
        mat = str(u.get("material") or "").strip()
        por_material_cant[mat] = por_material_cant.get(mat, 0) + 1

    for u in unidades:
        mat = str(u.get("material") or "").strip()
        largo_com = float(u.get("largo") or 0)
        unit_idx = int(u.get("unit_idx") or 1)
        cant_grupo = int(u.get("cant_grupo") or 1)
        tiras = stock_por_material.get(mat, [])
        if not tiras:
            continue

        if len(tiras) == cant_grupo:
            idx = min(max(0, unit_idx - 1), len(tiras) - 1)
            key, barra = tiras[idx]
            u["nesting_key"] = key
            u["reparto_greedy"] = False
            u["n_unidades_tira"] = 1
            stock = float(barra.get("largo_stock") or 0)
            if stock > largo_com + 0.5:
                u["reparto_greedy"] = True
                u["n_unidades_tira"] = max(1, int(round(stock / largo_com)))
        elif len(tiras) == 1:
            key, barra = tiras[0]
            u["nesting_key"] = key
            stock = float(barra.get("largo_stock") or 0)
            n_from_strip = max(1, int(round(stock / largo_com))) if largo_com > 0 else 1
            u["reparto_greedy"] = n_from_strip > 1 or cant_grupo > 1
            u["n_unidades_tira"] = max(cant_grupo, n_from_strip)
        else:
            idx = min(max(0, unit_idx - 1), len(tiras) - 1)
            key, barra = tiras[idx]
            u["nesting_key"] = key
            stock = float(barra.get("largo_stock") or 0)
            u["reparto_greedy"] = stock > largo_com + 0.5
            u["n_unidades_tira"] = max(1, int(round(stock / largo_com))) if largo_com > 0 else 1
    return unidades


def obtener_exclusiones_mrl_unidades(app, lote_idx: int) -> set[str]:
    raw = (getattr(app, "exclusiones_mrl_unidades_por_lote", None) or {}).get(int(lote_idx))
    if not raw:
        return set()
    return {str(k) for k in raw}


def guardar_exclusiones_mrl_unidades(app, lote_idx: int, unidades_excluidas: set[str]) -> None:
    if not hasattr(app, "exclusiones_mrl_unidades_por_lote") or app.exclusiones_mrl_unidades_por_lote is None:
        app.exclusiones_mrl_unidades_por_lote = {}
    app.exclusiones_mrl_unidades_por_lote[int(lote_idx)] = set(unidades_excluidas)


def piezas_por_material(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Piezas únicas por material: nombre, largo, cantidad."""
    out: dict[str, dict[tuple[str, float], dict[str, Any]]] = {}
    for material, _idx, barra in iter_barras_plan(plan):
        mat = str(material or "").strip()
        if mat not in out:
            out[mat] = {}
        for corte in barra.get("cortes") or []:
            nombre = str(corte.get("nombre") or "").strip() or "SIN_NOMBRE"
            try:
                largo = float(corte.get("largo") or 0)
            except Exception:
                largo = 0.0
            clave = (nombre, round(largo, 4))
            if clave not in out[mat]:
                out[mat][clave] = {"nombre": nombre, "largo": largo, "cantidad": 0}
            out[mat][clave]["cantidad"] += 1
    return {
        mat: sorted(vals.values(), key=lambda p: (-float(p["largo"]), p["nombre"]))
        for mat, vals in out.items()
    }


def obtener_exclusiones_largos(app, lote_idx: int) -> set[str]:
    raw = (getattr(app, "exclusiones_largos_pedido_por_lote", None) or {}).get(int(lote_idx))
    if not raw:
        return set()
    return {str(k) for k in raw}


def guardar_exclusiones_largos(app, lote_idx: int, barras_excluidas: set[str]) -> None:
    if not hasattr(app, "exclusiones_largos_pedido_por_lote") or app.exclusiones_largos_pedido_por_lote is None:
        app.exclusiones_largos_pedido_por_lote = {}
    app.exclusiones_largos_pedido_por_lote[int(lote_idx)] = set(barras_excluidas)


def calcular_planes_largos_nesting(app, resultados_list: list) -> dict[int, dict[str, Any]]:
    """
    Calcula el nesteo de largos para cada lote al terminar EJECUTAR NESTING
    (mismo momento que el nesteo de placas).
    """
    job = str(getattr(app, "job_activo", "") or "").strip()
    planes: dict[int, dict[str, Any]] = {}
    exclusiones: dict[int, set[str]] = {}

    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**_db_config())
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        for idx, orden_obj in enumerate(resultados_list or []):
            try:
                factor = max(1, int(orden_obj.get("lote_k", 1) or 1))
            except Exception:
                factor = 1

            wo_label = f"LOTE X{max(1, int(factor))}"
            rows, origen = _obtener_filas_demanda_lote(app, cursor, job, factor)
            if not rows:
                continue

            orden_id = f"NEST-{_norm_job(job)}-{wo_label}".replace(" ", "_")[:90]
            plan = _generar_plan_desde_filas(cursor, orden_id, "WO", job, factor, rows)
            if (plan.get("data") or {}) and int(plan.get("total_barras") or 0) > 0:
                plan["_demanda_origen"] = origen
                plan["_verificacion_62174"] = verificar_empate_referencia_62174(plan, rows)
                planes[int(idx)] = plan
                exclusiones[int(idx)] = set()
    except Exception:
        pass
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

    app.plan_largos_por_lote = planes
    app.exclusiones_largos_pedido_por_lote = exclusiones
    app.exclusiones_mrl_unidades_por_lote = {idx: set() for idx in planes}
    app.plan_largos_job = str(job or "").strip()
    return planes


def aplicar_pedido_largos_tras_export(
    app,
    lote_idx: int,
    orden_id: str,
    tipo_orden: str,
) -> tuple[bool, str]:
    """Aplica a material_requerido_ldg el plan del nesting y las exclusiones del usuario."""
    planes = getattr(app, "plan_largos_por_lote", None) or {}
    plan = planes.get(int(lote_idx))
    if not plan or not (plan.get("data") or {}):
        return False, "No hay plan de largos calculado para este lote."
    excl = obtener_exclusiones_mrl_unidades(app, int(lote_idx))
    return enviar_pedido_largos_filtrado(
        str(orden_id or "").strip(),
        str(tipo_orden or "WO").strip().upper(),
        plan,
        unidades_excluidas_mrl=excl,
    )


def cargar_plan_largos_contexto(app, tab) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """
    Devuelve (plan, contexto, error).
    Prioriza el plan calculado junto con el nesteo de placas.
  """
    contexto = resolver_contexto_largos(app, tab)
    plan_nesting = _plan_desde_nesting(app, tab)
    if plan_nesting is not None:
        contexto = dict(contexto)
        contexto["origen"] = "nesting"
        job = str(contexto.get("job") or "").strip()
        factor = int(contexto.get("factor_lote") or 1)
        contexto["etiqueta"] = f"NESTEO · JOB: {job or '—'} · LOTE X{factor}"
        contexto["puede_enviar_pedido"] = bool(resolver_orden_largos(app, tab))
        if contexto["puede_enviar_pedido"]:
            o, t = resolver_orden_largos(app, tab)
            contexto["orden_id"] = o
            contexto["tipo_orden"] = t
            contexto["modo"] = "orden"
            contexto["etiqueta"] = f"NESTEO · {o} · {t} · LOTE X{factor}"
        else:
            contexto["modo"] = "nesting"
        return plan_nesting, contexto, None

    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**_db_config())
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        if contexto["modo"] == "orden":
            plan = cargar_plan_largos(contexto["orden_id"], contexto["tipo_orden"])
            if (plan.get("data") or {}) and int(plan.get("total_barras") or 0) > 0:
                return plan, contexto, None

        job = str(contexto.get("job") or "").strip()
        factor = int(contexto.get("factor_lote") or 1)
        rows, origen = _obtener_filas_demanda_lote(app, cursor, job, factor)

        if not rows:
            return (
                {},
                contexto,
                "No hay demanda de largos para este job. Verifica el CSV en AutoDXF o importa la lista.",
            )

        orden_preview = f"PREVIEW-{_norm_job(job)}-LOTE-X{factor}".replace(" ", "_")[:90]
        plan = _generar_plan_desde_filas(
            cursor,
            orden_preview,
            "WO",
            job,
            factor,
            rows,
        )
        if not (plan.get("data") or {}):
            return {}, contexto, "No se pudo calcular el nesteo de largos con la demanda disponible."

        plan["_demanda_origen"] = origen
        plan["_verificacion_62174"] = verificar_empate_referencia_62174(plan, rows)

        contexto = dict(contexto)
        contexto["modo"] = "preview"
        contexto["orden_id"] = ""
        contexto["puede_enviar_pedido"] = bool(resolver_orden_largos(app, tab))
        if contexto["puede_enviar_pedido"]:
            o, t = resolver_orden_largos(app, tab)
            contexto["orden_id"] = o
            contexto["tipo_orden"] = t
            contexto["modo"] = "orden"
            contexto["etiqueta"] = f"ORDEN: {o} · {t}"

        return plan, contexto, None
    except Exception as e:
        return {}, contexto, str(e)
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def enviar_pedido_largos_filtrado(
    orden_id: str,
    tipo_orden: str,
    plan: dict[str, Any],
    barras_excluidas_pedido: set[str] | None = None,
    unidades_excluidas_mrl: set[str] | None = None,
) -> tuple[bool, str]:
    if not str(orden_id or "").strip():
        return False, "No hay WO/SWO asociada. Exporta el nesting antes de enviar el pedido."
    asegurar_tabla_material_requerido_ldg()
    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**_db_config())
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        ok, msg = reconstruir_pedido_desde_plan(
            cursor,
            str(orden_id).strip(),
            str(tipo_orden).strip().upper(),
            plan,
            barras_excluidas_pedido=barras_excluidas_pedido,
            unidades_excluidas_mrl=unidades_excluidas_mrl,
        )
        conexion.commit()
        return ok, msg
    except Exception as e:
        if conexion:
            conexion.rollback()
        return False, str(e)
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def iter_barras_plan(plan: dict[str, Any]):
    """(material, bar_idx, barra_dict) en orden estable."""
    data = plan.get("data") or {}
    for material in sorted(data.keys()):
        barras = list(data.get(material) or [])
        for bar_idx, barra in enumerate(barras):
            yield material, bar_idx, barra
