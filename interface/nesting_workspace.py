import copy
import json
import os
import gzip
from datetime import datetime

SCHEMA_WORKSPACE = "arga_nesting_workspace_v2"
COMPRESSED_WORKSPACE_EXTS = {".arganest", ".navanest"}


def _combo_text(combo):
    if combo is None:
        return "OPTIMIZAR LARGO Y ANCHO"
    if hasattr(combo, "currentText"):
        return combo.currentText()
    if hasattr(combo, "get"):
        return combo.get()
    return "OPTIMIZAR LARGO Y ANCHO"


def _combo_set_text(combo, value):
    if combo is None:
        return
    text = str(value or "")
    if hasattr(combo, "setCurrentText"):
        combo.setCurrentText(text)
    elif hasattr(combo, "set"):
        combo.set(text)


def _combo_values(combo):
    if combo is None:
        return []
    if hasattr(combo, "count"):
        return [combo.itemText(i) for i in range(combo.count())]
    if hasattr(combo, "cget"):
        return list(combo.cget("values") or [])
    return []


def _label_set_text(label, text):
    if label is None:
        return
    if hasattr(label, "setText"):
        label.setText(str(text))
    elif hasattr(label, "configure"):
        label.configure(text=str(text))


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


# No serializar cachés de geometría en vivo (Shapely → str rompe el visor al recargar).
_WORKSPACE_SKIP_KEYS = frozenset({"_poly_cache", "_bounds_cache"})


def _json_safe(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, dict):
        return {
            str(k): _json_safe(v)
            for k, v in obj.items()
            if k not in _WORKSPACE_SKIP_KEYS
        }

    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]

    if hasattr(obj, "tolist"):
        try:
            return _json_safe(obj.tolist())
        except Exception:
            pass

    if hasattr(obj, "as_posix"):
        try:
            return str(obj)
        except Exception:
            pass

    return str(obj)


def _deepcopy_jsonsafe(obj):
    try:
        cloned = copy.deepcopy(obj)
    except Exception:
        cloned = obj
    return _json_safe(cloned)


def _normalizar_fila_pieza(fila):
    """
    Convierte cualquier fila a la estructura estándar:
    (nombre, material, qty, calibre, estado, ruta_dxf)
    """
    if isinstance(fila, dict):
        nombre = fila.get("nombre") or fila.get("pieza") or fila.get("ref") or ""
        material = fila.get("material") or ""
        qty = fila.get("qty") or fila.get("cantidad") or "1"
        calibre = fila.get("calibre") or ""
        estado = fila.get("estado") or "LISTO"
        ruta = fila.get("ruta") or fila.get("path") or fila.get("dxf") or ""
        return (
            str(nombre),
            str(material),
            str(qty),
            str(calibre),
            str(estado),
            str(ruta),
        )

    if isinstance(fila, (list, tuple)):
        vals = list(fila[:6])
        while len(vals) < 6:
            vals.append("")
        return (
            str(vals[0]),
            str(vals[1]),
            str(vals[2]),
            str(vals[3]),
            str(vals[4]),
            str(vals[5]),
        )

    return None


def _clonar_datos_partes(datos):
    salida = []
    for fila in (datos or []):
        fila_ok = _normalizar_fila_pieza(fila)
        if fila_ok:
            salida.append(fila_ok)
    return salida


def _get_resultados_multilote(tab):
    multilote = getattr(tab.app, "resultados_multilote", None)
    if multilote:
        return multilote

    simple = getattr(tab.app, "resultados_nesting", None)
    if simple:
        return [{
            "lote_k": getattr(tab, "cantidad_tanques", "N/A"),
            "data": simple
        }]

    return []


def _get_placa_id_actual(tab):
    hoja = getattr(tab, "hoja_actual_data", None)
    if isinstance(hoja, dict):
        return hoja.get("placa_id")
    return None


def _get_sheet_uid_actual(tab):
    hoja = getattr(tab, "hoja_actual_data", None)
    if isinstance(hoja, dict):
        uid = str(hoja.get("sheet_uid") or "").strip()
        if uid:
            return uid
        idx = hoja.get("_nest_list_idx")
        if idx is not None:
            return f"idx:{int(idx)}"
    return None


def _buscar_hoja_en_resultados(
    resultados_nesting,
    clave_objetivo=None,
    placa_id_objetivo=None,
    sheet_uid_objetivo=None,
    nest_list_idx=None,
):
    if not isinstance(resultados_nesting, dict):
        return None, None

    uid_obj = str(sheet_uid_objetivo or "").strip()
    idx_obj = nest_list_idx if nest_list_idx is not None else None

    def _match_hoja(hoja):
        if not isinstance(hoja, dict):
            return False
        if uid_obj:
            if str(hoja.get("sheet_uid") or "").strip() == uid_obj:
                return True
            if uid_obj.startswith("idx:"):
                try:
                    return int(hoja.get("_nest_list_idx")) == int(uid_obj[4:])
                except Exception:
                    return False
        if idx_obj is not None:
            try:
                return int(hoja.get("_nest_list_idx")) == int(idx_obj)
            except Exception:
                return False
        if placa_id_objetivo:
            return hoja.get("placa_id") == placa_id_objetivo
        return False

    if clave_objetivo and clave_objetivo in resultados_nesting:
        hojas = resultados_nesting.get(clave_objetivo, {}).get("hojas", [])
        if uid_obj or idx_obj is not None or placa_id_objetivo:
            for hoja in hojas:
                if _match_hoja(hoja):
                    return hoja, clave_objetivo
        if hojas:
            return hojas[0], clave_objetivo

    if uid_obj or idx_obj is not None or placa_id_objetivo:
        for clave, info in resultados_nesting.items():
            for hoja in info.get("hojas", []):
                if _match_hoja(hoja):
                    return hoja, clave

    for clave, info in resultados_nesting.items():
        hojas = info.get("hojas", [])
        if hojas:
            return hojas[0], clave

    return None, None


def _extraer_rutas_dxf(datos_partes):
    rutas = []
    for fila in (datos_partes or []):
        fila_ok = _normalizar_fila_pieza(fila)
        if fila_ok and fila_ok[5]:
            rutas.append(str(fila_ok[5]))
    return rutas


def _extraer_rutas_por_lote(editable_inputs_by_lote):
    salida = []
    for lote in (editable_inputs_by_lote or []):
        salida.append(_extraer_rutas_dxf(lote))
    return salida


def _inferir_multiplicador_desde_multilote(resultados_multilote, lote_idx=0):
    try:
        idx = int(lote_idx or 0)
        if isinstance(resultados_multilote, list) and 0 <= idx < len(resultados_multilote):
            k = resultados_multilote[idx].get("lote_k", 1)
            return max(1, int(k))
    except Exception:
        pass
    return 1


def _refrescar_tab_partes(app, datos_partes, *, thumbnails_async: bool = False, omitir_ui: bool = False):
    """
    Mantiene el comportamiento actual de PARTS.
    omitir_ui=True: solo deja datos en memoria (carga rápida de .arganest).
    """
    if omitir_ui:
        app.datos_partes_actuales = datos_partes
        app._parts_ui_pendiente = list(datos_partes or [])
        return

    try:
        if hasattr(app, "cargar_datos_parts"):
            if hasattr(app, "vista_parts") and hasattr(app.vista_parts, "refrescar_tabla"):
                app.datos_partes_actuales = datos_partes
                app.vista_parts.refrescar_tabla(datos_partes, thumbnails_async=thumbnails_async)
                return
            app.cargar_datos_parts(datos_partes)
            return
    except Exception:
        pass

    app.datos_partes_actuales = datos_partes

    try:
        if hasattr(app, "vista_parts") and hasattr(app.vista_parts, "refrescar_tabla"):
            app.vista_parts.refrescar_tabla(datos_partes, thumbnails_async=thumbnails_async)
    except Exception:
        pass


def _reconstruir_editable_inputs_by_lote(payload, resultados_multilote, datos_base):
    """
    Reconstruye editable_inputs_by_lote.
    Si el workspace es viejo y no trae esa info,
    usa datos_base como fallback para todos los lotes.
    """
    raw = payload.get("editable_inputs_by_lote", None)
    cantidad_lotes = len(resultados_multilote or [])
    base = _clonar_datos_partes(datos_base)

    salida = []

    if isinstance(raw, list) and raw:
        for lote_raw in raw:
            lote_ok = _clonar_datos_partes(lote_raw)
            salida.append(lote_ok)

    # Compatibilidad hacia atrás:
    # si no hay editable_inputs_by_lote, crear uno por cada lote
    if not salida:
        if cantidad_lotes <= 0:
            return [base] if base else []
        for _ in range(cantidad_lotes):
            salida.append(_clonar_datos_partes(base))

    # Ajustar longitud si no coincide con resultados_multilote
    if cantidad_lotes > 0:
        while len(salida) < cantidad_lotes:
            salida.append(_clonar_datos_partes(base))
        if len(salida) > cantidad_lotes:
            salida = salida[:cantidad_lotes]

    return salida


def construir_payload_workspace(tab):
    multilote = _get_resultados_multilote(tab)
    if not multilote:
        raise ValueError("No hay resultados de nesting para guardar.")

    datos_partes = _clonar_datos_partes(getattr(tab.app, "datos_partes_actuales", []))
    editable_inputs_by_lote = getattr(tab.app, "editable_inputs_by_lote", []) or []
    editable_inputs_actuales = getattr(tab.app, "editable_inputs_actuales", []) or []

    # Compatibilidad si aún no se ha llenado editable_inputs_by_lote
    if not editable_inputs_by_lote:
        lote_idx = int(getattr(tab, "lote_actual_idx", 0) or 0)
        total_lotes = len(multilote)
        if total_lotes > 0:
            editable_inputs_by_lote = []
            for i in range(total_lotes):
                if i == lote_idx and editable_inputs_actuales:
                    editable_inputs_by_lote.append(_clonar_datos_partes(editable_inputs_actuales))
                else:
                    editable_inputs_by_lote.append(_clonar_datos_partes(datos_partes))

    payload = {
        "schema": SCHEMA_WORKSPACE,
        "saved_at": _now_iso(),
        "workspace_type": "nesting_workspace",
        "job_activo": getattr(tab.app, "job_activo", "NESTING"),
        "lote_actual_idx": int(getattr(tab, "lote_actual_idx", 0) or 0),
        "resultados_multilote": multilote,

        # Lista general de partes que la app ya usa para PARTS
        "datos_partes_actuales": datos_partes,

        # ===== NUEVO =====
        # Entradas editables por lote, base del futuro re-nesteo por lote
        "editable_inputs_by_lote": editable_inputs_by_lote,
        "editable_inputs_actuales": _clonar_datos_partes(editable_inputs_actuales),
        "lote_editado_dirty": bool(getattr(tab.app, "lote_editado_dirty", False)),

        # Rutas DXF generales y por lote
        "source_dxf_paths": _extraer_rutas_dxf(datos_partes),
        "source_dxf_paths_by_lote": _extraer_rutas_por_lote(editable_inputs_by_lote),

        "meta_pdf_por_ruta": getattr(tab.app, "meta_pdf_por_ruta", {}),
        "wo_reales_por_lote": getattr(tab.app, "wo_reales_por_lote", {}) or {},
        "ultimos_escenarios": getattr(tab.app, "ultimos_escenarios", []),
        "ui_state": {
            "cantidad_tanques": getattr(tab, "cantidad_tanques", "N/A"),
            "multiplicador_tanques": int(getattr(tab.app, "multiplicador_tanques", 1) or 1),
            "lote_k_activo": int(_inferir_multiplicador_desde_multilote(multilote, getattr(tab, "lote_actual_idx", 0))),
            "global_margin_val": getattr(tab, "global_margin_val", 0.15),
            "global_kerf_val": getattr(tab, "global_kerf_val", 0.3),
            "global_corner_val": getattr(tab, "global_corner_val", "INFERIOR IZQUIERDA"),
            "costo_usd_val": getattr(tab, "costo_usd_val", 0.0),
            "costo_mxn_val": getattr(tab, "costo_mxn_val", 0.0),
            "cmb_opt_val": _combo_text(getattr(tab, "cmb_opt", None)),
            "ajuste_desplegado": bool(getattr(tab, "ajuste_desplegado", False)),
        },
        "vista_actual": {
            "clave_actual": getattr(tab, "clave_actual", "") or "",
            "placa_id": _get_placa_id_actual(tab),
            "sheet_uid": _get_sheet_uid_actual(tab),
            "nest_list_idx": (
                tab.hoja_actual_data.get("_nest_list_idx")
                if isinstance(getattr(tab, "hoja_actual_data", None), dict)
                else None
            ),
        },
    }

    return _deepcopy_jsonsafe(payload)


def guardar_workspace(tab, ruta_archivo):
    payload = construir_payload_workspace(tab)

    carpeta = os.path.dirname(os.path.abspath(ruta_archivo))
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)

    ext = os.path.splitext(str(ruta_archivo))[1].lower()
    json_text = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # .arganest/.navanest: formato compacto + compresión gzip (sin pérdida).
    # .json: se mantiene en texto plano para inspección manual.
    if ext in COMPRESSED_WORKSPACE_EXTS:
        with gzip.open(ruta_archivo, "wb", compresslevel=6) as f:
            f.write(json_text.encode("utf-8"))
    else:
        with open(ruta_archivo, "w", encoding="utf-8") as f:
            f.write(json_text)

    return payload


def cargar_workspace_desde_archivo(ruta_archivo):
    with open(ruta_archivo, "rb") as f:
        raw = f.read()

    if not raw:
        raise ValueError("El archivo está vacío.")

    # Compatibilidad automática:
    # - Workspaces nuevos .arganest/.navanest comprimidos con gzip.
    # - Workspaces viejos en JSON plano.
    if raw[:2] == b"\x1f\x8b":
        text = gzip.decompress(raw).decode("utf-8")
    else:
        text = raw.decode("utf-8")

    payload = json.loads(text)

    if not isinstance(payload, dict):
        raise ValueError("El archivo no contiene un workspace válido.")

    schema = payload.get("schema")

    # Compatibilidad con workspaces anteriores
    schemas_compatibles = {
        "arga_nesting_workspace_v1",
        "arga_nesting_workspace_v2",
    }

    if schema not in schemas_compatibles:
        raise ValueError(
            f"Schema no compatible. Encontrado: {schema!r}"
        )

    return payload


def _relink_rutas_dxf_en_multilote(app, datos_partes) -> int:
    """Tras cargar .arganest: enlaza ruta DXF desde PARTS a piezas del nest guardado."""
    if not datos_partes:
        return 0
    try:
        from modules.nesting_engine.manager import enriquecer_hoja_export_desde_partes
    except Exception:
        return 0

    total = 0
    multilote = getattr(app, "resultados_multilote", None) or []
    for lote in multilote:
        if not isinstance(lote, dict):
            continue
        data = lote.get("data")
        if not isinstance(data, dict):
            continue
        for clave, info in data.items():
            if not isinstance(info, dict):
                continue
            for hoja in info.get("hojas") or []:
                if isinstance(hoja, dict):
                    total += enriquecer_hoja_export_desde_partes(hoja, clave, datos_partes)

    res = getattr(app, "resultados_nesting", None)
    if isinstance(res, dict):
        for clave, info in res.items():
            if not isinstance(info, dict):
                continue
            for hoja in info.get("hojas") or []:
                if isinstance(hoja, dict):
                    total += enriquecer_hoja_export_desde_partes(hoja, clave, datos_partes)

    try:
        from modules.nesting_engine.display_geometry import refrescar_poligonos_display_multilote

        refrescar_poligonos_display_multilote(multilote)
    except Exception:
        pass

    return total


def aplicar_workspace(tab, payload, *, carga_rapida: bool = False):
    if not isinstance(payload, dict):
        raise ValueError("Payload inválido para aplicar workspace.")

    def _avanzar(msg, pct):
        app = getattr(tab, "app", None)
        if app is not None and hasattr(app, "actualizar_progreso"):
            try:
                app.actualizar_progreso(msg, pct)
            except Exception:
                pass
        if app is not None and hasattr(app, "update_idletasks"):
            try:
                app.update_idletasks()
            except Exception:
                pass

    schema = payload.get("schema")
    if schema not in {"arga_nesting_workspace_v1", "arga_nesting_workspace_v2"}:
        raise ValueError("El workspace no corresponde a un schema compatible.")

    _avanzar("Restaurando piezas…", 0.62)
    # =========================================================
    # 1) Datos generales de PARTS
    # =========================================================
    datos_partes = _clonar_datos_partes(payload.get("datos_partes_actuales", []))

    # =========================================================
    # 2) Restaurar estado global app
    # =========================================================
    tab.app.resultados_multilote = payload.get("resultados_multilote", []) or []
    tab.app.datos_partes_actuales = datos_partes
    tab.app.meta_pdf_por_ruta = payload.get("meta_pdf_por_ruta", {}) or {}
    tab.app.job_activo = payload.get("job_activo", "NESTING")
    tab.app.ultimos_escenarios = payload.get("ultimos_escenarios", []) or []

    raw_wo_map = payload.get("wo_reales_por_lote", {}) or {}
    try:
        tab.app.wo_reales_por_lote = {int(k): str(v) for k, v in raw_wo_map.items()}
    except Exception:
        tab.app.wo_reales_por_lote = {}

    # =========================================================
    # 3) Restaurar datos editables por lote
    # =========================================================
    editable_inputs_by_lote = _reconstruir_editable_inputs_by_lote(
        payload,
        tab.app.resultados_multilote,
        datos_partes
    )

    lote_idx = int(payload.get("lote_actual_idx", 0) or 0)
    if lote_idx < 0:
        lote_idx = 0
    if editable_inputs_by_lote and lote_idx >= len(editable_inputs_by_lote):
        lote_idx = 0

    editable_inputs_actuales = []
    if editable_inputs_by_lote:
        editable_inputs_actuales = _clonar_datos_partes(editable_inputs_by_lote[lote_idx])

    tab.app.editable_inputs_by_lote = editable_inputs_by_lote
    tab.app.editable_inputs_actuales = editable_inputs_actuales
    tab.app.lote_editado_dirty = bool(payload.get("lote_editado_dirty", False))

    tab.app.source_dxf_paths_workspace = payload.get("source_dxf_paths", []) or _extraer_rutas_dxf(datos_partes)
    tab.app.source_dxf_paths_by_lote = payload.get("source_dxf_paths_by_lote", []) or _extraer_rutas_por_lote(editable_inputs_by_lote)

    _relink_rutas_dxf_en_multilote(tab.app, datos_partes)

    _avanzar("Actualizando PARTS…", 0.72)
    # =========================================================
    # 4) Mantener comportamiento actual de PARTS
    # =========================================================
    if carga_rapida:
        _refrescar_tab_partes(tab.app, datos_partes, omitir_ui=True)
    else:
        _refrescar_tab_partes(tab.app, datos_partes)

    _avanzar("Restaurando NESTING…", 0.82)
    # =========================================================
    # 5) Restaurar estado UI tab NESTING
    # =========================================================
    ui = payload.get("ui_state", {}) or {}
    mult_ui = ui.get("multiplicador_tanques", None)
    if mult_ui is None:
        mult_ui = ui.get("lote_k_activo", None)
    if mult_ui is None:
        mult_ui = _inferir_multiplicador_desde_multilote(tab.app.resultados_multilote, lote_idx)
    try:
        mult_ui = max(1, int(mult_ui))
    except Exception:
        mult_ui = 1
    tab.app.multiplicador_tanques = int(mult_ui)
    tab.cantidad_tanques = f"X{int(mult_ui)}"
    tab.global_margin_val = float(ui.get("global_margin_val", 0.15) or 0.15)
    try:
        gk = float(ui.get("global_kerf_val", 0.3) or 0.3)
    except Exception:
        gk = 0.3
    if gk <= 0:
        gk = 0.3
    tab.global_kerf_val = gk
    if hasattr(tab, "_sync_kerf_widget"):
        tab._sync_kerf_widget()
    tab.global_corner_val = str(ui.get("global_corner_val", "INFERIOR IZQUIERDA") or "INFERIOR IZQUIERDA")
    tab.costo_usd_val = float(ui.get("costo_usd_val", 0.0) or 0.0)
    tab.costo_mxn_val = float(ui.get("costo_mxn_val", 0.0) or 0.0)
    tab.lote_actual_idx = lote_idx

    _label_set_text(getattr(tab, "lbl_cantidad", None), f"CANTIDAD: {tab.cantidad_tanques}")
    try:
        _combo_set_text(getattr(tab, "cmb_opt", None), ui.get("cmb_opt_val", "OPTIMIZAR LARGO"))
    except Exception:
        pass

    # =========================================================
    # 6) Reconstruir dropdown y lote actual
    # =========================================================
    tab.actualizar_dropdown_lotes(activar_primero=not carga_rapida)

    valores = _combo_values(getattr(tab, "cmb_lotes", None))
    if valores and valores[0] != "SIN ÓRDENES":
        idx = tab.lote_actual_idx
        if idx < 0 or idx >= len(valores):
            idx = 0
            tab.lote_actual_idx = 0

        opcion = valores[idx]
        _combo_set_text(getattr(tab, "cmb_lotes", None), opcion)
        if carga_rapida:
            tab._workspace_lote_pendiente = opcion
        else:
            tab.on_lote_selected(opcion, sync_parts=True)

    try:
        from modules.nesting_engine.sheet_integrity import deduplicar_resultados_nesting

        kerf_g = float(getattr(tab, "global_kerf_val", 0.3) or 0.3)
        if kerf_g <= 0:
            kerf_g = 0.3
        res = getattr(tab.app, "resultados_nesting", None)
        if isinstance(res, dict) and res:
            deduplicar_resultados_nesting(res, kerf_global=kerf_g)
    except Exception:
        pass

    _avanzar("Dibujando vista…", 0.92)
    # =========================================================
    # 7) Restaurar vista exacta
    # =========================================================
    vista = payload.get("vista_actual", {}) or {}
    clave_guardada = vista.get("clave_actual")
    placa_guardada = vista.get("placa_id")
    sheet_uid_guardado = vista.get("sheet_uid")
    nest_idx_guardado = vista.get("nest_list_idx")

    hoja, clave = _buscar_hoja_en_resultados(
        getattr(tab.app, "resultados_nesting", {}),
        clave_guardada,
        placa_guardada,
        sheet_uid_objetivo=sheet_uid_guardado,
        nest_list_idx=nest_idx_guardado,
    )

    if hoja is not None and clave is not None:
        if carga_rapida:
            tab._workspace_vista_pendiente = {
                "hoja": hoja,
                "clave": clave,
                "ajuste_desplegado": bool(ui.get("ajuste_desplegado", False)),
            }
        else:
            tab.dibujar_hoja_full(hoja, clave)

    # =========================================================
    # 8) Reabrir panel de ajuste si estaba abierto
    # =========================================================
    ajuste_desplegado = bool(ui.get("ajuste_desplegado", False))
    if not carga_rapida and ajuste_desplegado and hasattr(tab, "ajuste_desplegado") and not tab.ajuste_desplegado:
        try:
            tab.toggle_ajuste_placa()
        except Exception:
            pass

    _avanzar("Listo", 1.0)
    return True