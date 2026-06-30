"""Carga plan de lista de largos y sincroniza pedido material_requerido_ldg."""
from __future__ import annotations

from collections import defaultdict
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


def nombre_pieza_largo_display(nombre: str) -> str:
    """Quita extensión Inventor (.ipt) y sufijos de exportación para mostrar al usuario."""
    s = str(nombre or "").strip()
    if not s:
        return s
    for ext in (".ipt", ".IPT", ".iam", ".IAM", ".idw", ".IDW"):
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    for marker in (
        "_Default_As Machined_",
        "_Default_As_Machined_",
        "_Default As Machined",
        "_Default_As Machined",
        "_Predeterminado",
        "_Predeterminado_",
        "_PREDETERMINADO",
    ):
        idx = s.find(marker)
        if idx >= 0:
            s = s[:idx]
            break
    s = s.rstrip("_ .")
    return s or str(nombre or "").strip()


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
        nom = nombre_pieza_largo_display(str(corte.get("nombre") or "Pieza"))
        corte["nombre"] = nom
        if totales[clave] > 1:
            serial[clave] = serial.get(clave, 0) + 1
            corte["_etiqueta_pieza"] = f"{nom}  #{serial[clave]}"
        else:
            corte["_etiqueta_pieza"] = nom
        corte["_corte_uid"] = id(corte)

    return expandidos


def _util_comercial_in(largo_com: float) -> float:
    return max(0.0, float(largo_com or 0) - 2 * RECORTE_EXTREMO_LARGOS_IN)


def _cap_cortes_a_util(cortes: list[dict], largo_com: float) -> list[dict]:
    """Nunca mostrar más piezas de las que caben en una barra comercial (util − kerf)."""
    import copy

    util = _util_comercial_in(largo_com)
    if util <= 0:
        return []
    out: list[dict] = []
    used = 0.0
    for corte in cortes or []:
        largo = float(corte.get("largo") or 0)
        if largo <= 0:
            continue
        kerf = KERF_LARGOS_IN if out else 0.0
        if used + kerf + largo > util + 0.02:
            break
        out.append(copy.deepcopy(corte))
        used += kerf + largo
    return out


def _split_cortes_en_unidades_comerciales(
    cortes: list[dict],
    largo_comercial: float,
    n_unidades: int,
) -> list[list[dict]]:
    """Reparte piezas (una a una) llenando cada barra comercial antes de la siguiente."""
    import copy

    piezas = _etiquetar_cortes_individuales(cortes)
    if n_unidades <= 1:
        return [_cap_cortes_a_util(piezas, largo_comercial)]

    util = _util_comercial_in(largo_comercial)
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
        if used[bucket_idx] + kerf + largo > util + 0.02:
            # No cabe en ningún slot comercial de esta tira física — omitir en vista.
            continue
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


def _slots_comerciales_en_tira(largo_stock: float, largo_com: float) -> int:
    """Cuántas barras comerciales (ej. 240\") cubre una tira física del nesteo (ej. 480\")."""
    import math

    largo_com = float(largo_com or 0)
    if largo_com <= 0:
        return 1
    stock = max(0.0, float(largo_stock or 0))
    if stock <= 0:
        return 1
    return max(1, math.ceil(stock / largo_com))


def vista_barra_para_unidad_mrl(
    barra: dict,
    largo_comercial: float,
    unit_idx: int,
    *,
    reparto_greedy: bool = False,
    n_unidades_tira: int = 1,
    cant_grupo: int = 1,
    unit_idx_en_tira: int | None = None,
    n_slots_tira: int | None = None,
    cortes_slot: list[dict] | None = None,
) -> dict:
    """
    Vista MRL: siempre largo comercial (ej. 240\").
    Si la tira física es más larga (480\"), reparte con tope estricto de util por slot.
    """
    import copy

    stock_origen = float(barra.get("largo_stock") or 0)
    largo_com = float(largo_comercial or 0)
    if largo_com <= 0:
        return dict(barra)

    cortes_origen = list(barra.get("cortes") or [])
    idx_local = int(unit_idx_en_tira if unit_idx_en_tira is not None else unit_idx)
    n_slices = _slots_comerciales_en_tira(stock_origen, largo_com)

    if cortes_slot is not None:
        cortes_vista = _cap_cortes_a_util(cortes_slot, largo_com)
        largo_vista = largo_com
        nota_nesteo = (
            f"Piezas en tira nesteo de {stock_origen:.0f}\" "
            f"→ barra comercial {idx_local}/{n_slices} de {largo_com:.0f}\""
        ) if n_slices > 1 else ""
    elif n_slices > 1:
        splits = _split_cortes_en_unidades_comerciales(cortes_origen, largo_com, n_slices)
        idx = min(max(0, idx_local - 1), len(splits) - 1)
        cortes_vista = splits[idx]
        largo_vista = largo_com
        nota_nesteo = (
            f"Piezas en tira nesteo de {stock_origen:.0f}\" "
            f"→ barra comercial {idx_local}/{n_slices} de {largo_com:.0f}\""
        )
    else:
        cortes_vista = _cap_cortes_a_util(cortes_origen, largo_com or stock_origen)
        largo_vista = largo_com if largo_com > 0 else stock_origen
        nota_nesteo = ""

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
                    "proceso": str(row.get("proceso") or "").strip(),
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


def resolver_plan_largos_para_tab(app, tab) -> dict[str, Any] | None:
    """
    Misma fuente que el modal Nesteo de largos: memoria post-nesting → BD si hace falta.
    Cachea el plan en app.plan_largos_por_lote para costos y export.
    """
    plan = _plan_desde_nesting(app, tab)
    if isinstance(plan, dict) and (plan.get("data") or {}):
        return plan
    try:
        plan_ctx, _, err = cargar_plan_largos_contexto(app, tab)
        if err or not isinstance(plan_ctx, dict) or not (plan_ctx.get("data") or {}):
            return None
        idx = _lote_idx(tab)
        if not hasattr(app, "plan_largos_por_lote") or app.plan_largos_por_lote is None:
            app.plan_largos_por_lote = {}
        app.plan_largos_por_lote[idx] = plan_ctx
        app.plan_largos_job = str(getattr(app, "job_activo", "") or "").strip()
        return plan_ctx
    except Exception:
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
        if not (barra.get("cortes") or []):
            continue
        mat = str(material or "").strip()
        stock_por_material.setdefault(mat, []).append((bar_key(material, bar_idx), barra))

    por_material: dict[str, list[dict]] = {}
    for u in unidades:
        mat = str(u.get("material") or "").strip()
        por_material.setdefault(mat, []).append(u)

    for mat, units in por_material.items():
        tiras = stock_por_material.get(mat, [])
        if not tiras:
            continue

        largo_com = float(units[0].get("largo") or 0) if units else 0.0
        slots: list[dict] = []
        for nest_key, barra in tiras:
            stock_t = float(barra.get("largo_stock") or 0)
            n_slots = _slots_comerciales_en_tira(stock_t, largo_com)
            splits = _split_cortes_en_unidades_comerciales(
                list(barra.get("cortes") or []), largo_com, n_slots
            )
            for local_i in range(n_slots):
                slots.append(
                    {
                        "nesting_key": nest_key,
                        "unit_idx_en_tira": local_i + 1,
                        "n_slots_tira": n_slots,
                        "cortes_slot": splits[local_i] if local_i < len(splits) else [],
                    }
                )

        for u, slot in zip(units, slots):
            u["nesting_key"] = slot["nesting_key"]
            u["unit_idx_en_tira"] = slot["unit_idx_en_tira"]
            u["n_slots_tira"] = slot["n_slots_tira"]
            u["cortes_slot"] = slot["cortes_slot"]
            u["reparto_greedy"] = int(slot["n_slots_tira"]) > 1
            u["n_unidades_tira"] = int(slot["n_slots_tira"])

        # Unidades MRL de más (desfase catálogo): sin mapa de corte — no reutilizar último slot.
        for u in units[len(slots) :]:
            u.pop("nesting_key", None)
            u.pop("cortes_slot", None)
            u.pop("unit_idx_en_tira", None)
            u.pop("n_slots_tira", None)
            u["reparto_greedy"] = False
            u["n_unidades_tira"] = 1

    return unidades


def auditar_consumo_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """
    Verifica coherencia MRL ↔ slots comerciales ↔ piezas mapeadas.
    Devuelve ok=True si cantidades cuadran y ningún slot excede el útil.
    """
    from lista_largos_material_requerido import agregar_filas_desde_plan

    filas_mrl = agregar_filas_desde_plan(plan)
    unidades = listar_unidades_mrl_plan(plan)
    total_mrl = sum(int(f.get("cantidad") or 0) for f in filas_mrl)

    piezas_plan = 0
    piezas_slot = 0
    slots_calc = 0
    overflow = 0
    sin_mapa = 0
    detalle: list[dict[str, Any]] = []

    units_por_mat: dict[str, list[dict]] = defaultdict(list)
    for u in unidades:
        units_por_mat[str(u.get("material") or "").strip()].append(u)

    mrl_por_mat = {str(f["material"]).strip(): int(f.get("cantidad") or 0) for f in filas_mrl}

    for material, _idx, barra in iter_barras_plan(plan):
        if str(barra.get("source") or "STOCK").upper() == "REMANENTE":
            continue
        if not (barra.get("cortes") or []):
            continue
        piezas_plan += len(barra.get("cortes") or [])

    for mat, units in units_por_mat.items():
        mrl_qty = mrl_por_mat.get(mat, 0)
        n_units = len(units)
        n_slots_mat = sum(
            _slots_comerciales_en_tira(
                float(b.get("largo_stock") or 0),
                float(units[0].get("largo") or 0) if units else 0.0,
            )
            for _mk, b in [
                (bar_key(material, bar_idx), barra)
                for material, bar_idx, barra in iter_barras_plan(plan)
                if str(material or "").strip() == mat
                and str(barra.get("source") or "STOCK").upper() == "STOCK"
                and (barra.get("cortes") or [])
            ]
        )
        slots_calc += n_slots_mat
        p_slot = sum(len(u.get("cortes_slot") or []) for u in units)
        piezas_slot += p_slot
        sin_mapa += sum(1 for u in units if not u.get("nesting_key"))
        for u in units:
            cs = u.get("cortes_slot") or []
            if cs and _slot_overflow(cs, float(u.get("largo") or 0)):
                overflow += 1
        if mrl_qty != n_units or mrl_qty != n_slots_mat or p_slot < sum(
            len(b.get("cortes") or [])
            for _m, _i, b in iter_barras_plan(plan)
            if str(_m or "").strip() == mat
            and str(b.get("source") or "STOCK").upper() == "STOCK"
        ):
            detalle.append(
                {
                    "material": mat,
                    "mrl": mrl_qty,
                    "unidades": n_units,
                    "slots": n_slots_mat,
                    "piezas_slot": p_slot,
                }
            )

    ok = (
        total_mrl == len(unidades) == slots_calc
        and piezas_plan == piezas_slot
        and overflow == 0
        and sin_mapa == 0
    )
    return {
        "ok": ok,
        "mrl_barras": total_mrl,
        "unidades": len(unidades),
        "slots": slots_calc,
        "piezas_plan": piezas_plan,
        "piezas_mapeadas": piezas_slot,
        "overflow_slots": overflow,
        "sin_mapa": sin_mapa,
        "detalle": detalle,
    }


def _slot_overflow(cortes: list[dict], largo_com: float) -> bool:
    util = _util_comercial_in(largo_com)
    used = 0.0
    for i, c in enumerate(cortes or []):
        if i > 0:
            used += KERF_LARGOS_IN
        used += float(c.get("largo") or 0)
    return used > util + 0.02


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
    asegurar_tabla_material_requerido_ldg(_db_config())
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


def obtener_filas_demanda_contexto(app, tab) -> list[dict]:
    """Demanda de largos del CSV (o BD) para el job/lote activo — cantidad_base por unidad."""
    contexto = resolver_contexto_largos(app, tab)
    job = str(contexto.get("job") or "").strip()
    factor = int(contexto.get("factor_lote") or 1)
    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**_db_config())
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        rows, _origen = _obtener_filas_demanda_lote(app, cursor, job, factor)
        return list(rows or [])
    except Exception:
        return _filas_desde_csv_para_job(app, job, factor)
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def construir_snapshot_pdf_piso(
    plan: dict[str, Any],
    contexto: dict[str, Any],
    unidades_excluidas: set[str],
    app,
    tab,
) -> dict[str, Any]:
    """
    Snapshot para PDF piso: tabla accesorios (CSV, qty/unidad) + barras MRL activas.
    """
    from catalogo_largos import _cargar_placas_largos_desde_herinox, descripcion_herinox_mrl, etiqueta_tipo_perfil_mrl
    from reporte_pdf_nesteo_largos_piso import filas_csv_a_accesorios

    filas_demanda = obtener_filas_demanda_contexto(app, tab)
    job = str(contexto.get("job") or getattr(app, "job_activo", "") or "").strip()
    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)

    barras_piso: list[dict[str, Any]] = []
    barra_lookup: dict[str, tuple[str, dict]] = {}
    for material, bar_idx, barra in iter_barras_plan(plan):
        barra_lookup[bar_key(material, bar_idx)] = (material, barra)

    for u in listar_unidades_mrl_plan(plan):
        key = str(u.get("key") or "")
        if key in unidades_excluidas:
            continue
        nesting_key = str(u.get("nesting_key") or "")
        if not nesting_key or nesting_key not in barra_lookup:
            continue
        material, barra = barra_lookup[nesting_key]

        cod = str(u.get("codigo") or "")
        mat_raw = str(u.get("material") or "")
        tipo_lbl = etiqueta_tipo_perfil_mrl(mat_raw, cod, catalogo=catalogo)
        try:
            largo_com = float(u.get("largo") or 0)
        except Exception:
            largo_com = 0.0
        unit_idx = int(u.get("unit_idx") or 1)
        cant_grupo = int(u.get("cant_grupo") or 1)
        desc = descripcion_herinox_mrl(cod, mat_raw, catalogo=catalogo)
        etiqueta = f"#{unit_idx}/{cant_grupo} · {tipo_lbl} · {cod} · {largo_com:.0f}\" comercial"
        if desc:
            etiqueta += f" · {desc}"

        vista = vista_barra_para_unidad_mrl(
            barra,
            largo_com,
            unit_idx,
            reparto_greedy=bool(u.get("reparto_greedy")),
            n_unidades_tira=int(u.get("n_unidades_tira") or 1),
            cant_grupo=cant_grupo,
            unit_idx_en_tira=u.get("unit_idx_en_tira"),
            n_slots_tira=u.get("n_slots_tira"),
            cortes_slot=u.get("cortes_slot"),
        )
        if cod:
            vista["_vista_codigo"] = cod

        barras_piso.append(
            {
                "etiqueta": etiqueta,
                "material": material,
                "codigo": cod,
                "barra": preparar_barra_para_canvas(vista, codigo=cod),
            }
        )

    titulo = f"ACCESORIOS LARGOS TANK ({job})" if job else "ACCESORIOS LARGOS"
    return {
        "job": job,
        "titulo": titulo,
        "titulo_job": job,
        "etiqueta_orden": str(contexto.get("etiqueta") or ""),
        "filas_demanda": filas_demanda,
        "filas_accesorios": filas_csv_a_accesorios(filas_demanda),
        "barras_piso": barras_piso,
    }

