import copy
import json
import os
import gzip
import time
from datetime import datetime

SCHEMA_WORKSPACE = "arga_nesting_workspace_v2"
COMPRESSED_WORKSPACE_EXTS = {".arganest", ".navanest"}
WORKSPACE_OPEN_EXTS = COMPRESSED_WORKSPACE_EXTS | {".json"}

MATERIAL_KIND_STEEL = "steel"
MATERIAL_KIND_CU = "cu"
MATERIAL_KIND_MIX = "mix"

_ARGANEST_LOAD_T0: float | None = None
_ARGANEST_LOAD_FILE: str = ""


def reset_arganest_load_log(ruta_archivo: str = "") -> None:
    global _ARGANEST_LOAD_T0, _ARGANEST_LOAD_FILE
    _ARGANEST_LOAD_T0 = time.perf_counter()
    _ARGANEST_LOAD_FILE = os.path.basename(str(ruta_archivo or "workspace"))


def log_arganest_load(msg: str, *, phase: str = "") -> None:
    """Traza en consola el avance de carga .arganest (flush inmediato)."""
    global _ARGANEST_LOAD_T0
    t0 = _ARGANEST_LOAD_T0 if _ARGANEST_LOAD_T0 is not None else time.perf_counter()
    elapsed = time.perf_counter() - t0
    fname = _ARGANEST_LOAD_FILE or "workspace"
    tag = f"[ARGANEST-LOAD][{fname}][+{elapsed:6.1f}s]"
    if phase:
        tag += f" [{phase}]"
    print(f"{tag} {msg}", flush=True)


def _clave_es_cobre_workspace(clave) -> bool:
    """True si la clave de grupo parece cobre (misma heurística que el motor)."""
    try:
        from utils_nesting import es_material_cobre
    except Exception:
        def es_material_cobre(material):
            m = str(material or "").strip().upper()
            return m in ("CU", "COBRE", "COPPER") or "COBRE" in m or "COPPER" in m

    s = str(clave or "").strip().upper()
    if not s or s.startswith("_"):
        return False
    if s.endswith("_CU") or s.endswith("|CU") or "| CU" in s:
        return True
    if "_" in s:
        mat = s.split("_", 1)[1]
        return bool(es_material_cobre(mat))
    return bool(es_material_cobre(s))


def _iter_claves_resultados(payload_or_map) -> list[str]:
    """Recolecta claves de nest desde payload completo o mapa de resultados."""
    claves: list[str] = []
    if not isinstance(payload_or_map, dict):
        return claves

    multilote = payload_or_map.get("resultados_multilote")
    if isinstance(multilote, list):
        for slot in multilote:
            if not isinstance(slot, dict):
                continue
            data = slot.get("data") or {}
            if isinstance(data, dict):
                claves.extend(str(k) for k in data.keys())
        if claves:
            return claves

    for key in ("resultados_nesting", "resultados", "data"):
        data = payload_or_map.get(key)
        if isinstance(data, dict):
            return [str(k) for k in data.keys()]

    # Mapa plano clave→info (p.ej. resultados_nesting pasado directo)
    for k, v in payload_or_map.items():
        if str(k).startswith("_"):
            continue
        if isinstance(v, dict) and ("hojas" in v or "error" in v):
            claves.append(str(k))
    return claves


def clasificar_material_workspace(payload) -> str:
    """
    Clasifica el workspace: 'steel' | 'cu' | 'mix'.
    Escanea claves de resultados_multilote / resultados_nesting.
    """
    if isinstance(payload, dict):
        marked = str(payload.get("workspace_material_kind") or "").strip().lower()
        if marked in (MATERIAL_KIND_STEEL, MATERIAL_KIND_CU, MATERIAL_KIND_MIX):
            return marked

    claves = _iter_claves_resultados(payload if isinstance(payload, dict) else {})
    # Ignorar claves internas de metadatos del motor
    claves = [k for k in claves if k and not str(k).startswith("_")]
    if not claves:
        # Fallback: mirar materiales en datos_partes
        has_cu = False
        has_steel = False
        partes = []
        if isinstance(payload, dict):
            partes = payload.get("datos_partes_actuales") or []
            for lote_parts in payload.get("editable_inputs_by_lote") or []:
                if isinstance(lote_parts, list):
                    partes = list(partes) + list(lote_parts)
        try:
            from utils_nesting import es_material_cobre
        except Exception:
            def es_material_cobre(material):
                m = str(material or "").strip().upper()
                return m in ("CU", "COBRE", "COPPER") or "COBRE" in m or "COPPER" in m

        for fila in partes or []:
            if not isinstance(fila, dict):
                continue
            mat = fila.get("material") or ""
            if es_material_cobre(mat):
                has_cu = True
            elif str(mat).strip():
                has_steel = True
        if has_cu and has_steel:
            return MATERIAL_KIND_MIX
        if has_cu:
            return MATERIAL_KIND_CU
        return MATERIAL_KIND_STEEL

    has_cu = any(_clave_es_cobre_workspace(k) for k in claves)
    has_steel = any(not _clave_es_cobre_workspace(k) for k in claves)
    if has_cu and has_steel:
        return MATERIAL_KIND_MIX
    if has_cu:
        return MATERIAL_KIND_CU
    return MATERIAL_KIND_STEEL


def extension_para_material(kind: str) -> str:
    """Siempre .arganest: el icono se resuelve por contenido (Icon Handler)."""
    _ = kind
    return ".arganest"


def normalizar_ruta_workspace(ruta: str, kind: str | None = None) -> str:
    """Normaliza a .arganest/.navanest (sin cambiar a otras extensiones)."""
    ruta = str(ruta or "")
    if not ruta:
        return ruta
    root, ext = os.path.splitext(ruta)
    ext_l = ext.lower()
    if ext_l in COMPRESSED_WORKSPACE_EXTS:
        return ruta
    if not ext_l or ext_l == ".json":
        return root + ".arganest"
    return ruta


def filetypes_workspace_guardar():
    return [
        ("Arga Nest Workspace", "*.arganest"),
        ("Nava Nest Workspace", "*.navanest"),
        ("JSON", "*.json"),
        ("Todos los archivos", "*.*"),
    ]


def filetypes_workspace_abrir():
    return [
        ("Arga Nest Workspace", "*.arganest *.navanest"),
        ("Nava Nest Workspace", "*.navanest"),
        ("JSON", "*.json"),
        ("Todos los archivos", "*.*"),
    ]


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


def _construir_ui_state_workspace(tab, multilote, lote_idx=0):
    lote_idx = int(lote_idx or 0)
    cantidad = getattr(tab, "cantidad_tanques", "N/A")
    if isinstance(multilote, list) and 0 <= lote_idx < len(multilote):
        k = multilote[lote_idx].get("lote_k", cantidad)
        if k not in (None, "", "N/A"):
            cantidad = f"X{k}" if not str(k).upper().startswith("X") else str(k)
    return {
        "cantidad_tanques": cantidad,
        "multiplicador_tanques": int(_inferir_multiplicador_desde_multilote(multilote, lote_idx)),
        "lote_k_activo": int(_inferir_multiplicador_desde_multilote(multilote, lote_idx)),
        "global_margin_val": getattr(tab, "global_margin_val", 0.15),
        "global_kerf_val": getattr(tab, "global_kerf_val", 0.15),
        "global_corner_val": getattr(tab, "global_corner_val", "INFERIOR IZQUIERDA"),
        "costo_usd_val": getattr(tab, "costo_usd_val", 0.0),
        "costo_mxn_val": getattr(tab, "costo_mxn_val", 0.0),
        "cmb_opt_val": _combo_text(getattr(tab, "cmb_opt", None)),
        "ajuste_desplegado": bool(getattr(tab, "ajuste_desplegado", False)),
    }


def _vista_desde_resultados(mini_resultados):
    for clave, info in (mini_resultados or {}).items():
        hojas = info.get("hojas") or []
        if not hojas:
            continue
        hoja = hojas[0]
        uid = str(hoja.get("sheet_uid") or "").strip()
        if not uid and hoja.get("_nest_list_idx") is not None:
            uid = f"idx:{int(hoja.get('_nest_list_idx'))}"
        return {
            "clave_actual": str(clave),
            "placa_id": hoja.get("placa_id"),
            "sheet_uid": uid or None,
            "nest_list_idx": hoja.get("_nest_list_idx"),
        }
    return {
        "clave_actual": "",
        "placa_id": None,
        "sheet_uid": None,
        "nest_list_idx": None,
    }


def construir_payload_workspace_lote_export(
    tab,
    *,
    lote_idx,
    mini_resultados,
    n_wo,
    k_val,
):
    """Workspace .arganest de un solo lote/WO (export automático junto a DXF)."""
    lote_idx = int(lote_idx or 0)
    ml_src = getattr(tab.app, "resultados_multilote", None) or []
    if 0 <= lote_idx < len(ml_src):
        lote_entry = copy.deepcopy(ml_src[lote_idx])
    else:
        lote_entry = {"lote_k": k_val}
    lote_entry["data"] = copy.deepcopy(mini_resultados or {})
    multilote = [lote_entry]

    try:
        from modules.nesting_engine.display_geometry import asegurar_dxf_export_cache_para_guardar

        dxf_export_cache = asegurar_dxf_export_cache_para_guardar(
            multilote,
            log=lambda msg, **kw: print(f"[ARGANEST-SAVE][EXPORT] {msg}", flush=True),
        )
    except Exception as exc:
        dxf_export_cache = {"transform_ready": False, "error": str(exc)}

    datos_partes = _clonar_datos_partes(getattr(tab.app, "datos_partes_actuales", []))
    editable_inputs_by_lote_full = getattr(tab.app, "editable_inputs_by_lote", []) or []
    if editable_inputs_by_lote_full and 0 <= lote_idx < len(editable_inputs_by_lote_full):
        editable_inputs = _clonar_datos_partes(editable_inputs_by_lote_full[lote_idx])
    elif getattr(tab.app, "editable_inputs_actuales", None) and lote_idx == int(
        getattr(tab, "lote_actual_idx", 0) or 0
    ):
        editable_inputs = _clonar_datos_partes(tab.app.editable_inputs_actuales)
    else:
        editable_inputs = _clonar_datos_partes(datos_partes)
    editable_inputs_by_lote = [editable_inputs]

    payload = {
        "schema": SCHEMA_WORKSPACE,
        "workspace_material_kind": clasificar_material_workspace(
            {
                "resultados_multilote": multilote,
                "datos_partes_actuales": editable_inputs or datos_partes,
                "editable_inputs_by_lote": editable_inputs_by_lote,
            }
        ),
        "saved_at": _now_iso(),
        "workspace_type": "nesting_workspace",
        "job_activo": getattr(tab.app, "job_activo", "NESTING"),
        "lote_actual_idx": 0,
        "resultados_multilote": multilote,
        "datos_partes_actuales": editable_inputs or datos_partes,
        "editable_inputs_by_lote": editable_inputs_by_lote,
        "editable_inputs_actuales": _clonar_datos_partes(editable_inputs),
        "lote_editado_dirty": bool(getattr(tab.app, "lote_editado_dirty", False)),
        "source_dxf_paths": _extraer_rutas_dxf(editable_inputs or datos_partes),
        "source_dxf_paths_by_lote": _extraer_rutas_por_lote(editable_inputs_by_lote),
        "meta_pdf_por_ruta": getattr(tab.app, "meta_pdf_por_ruta", {}),
        "orientacion_cobre_por_ruta": dict(
            getattr(tab.app, "orientacion_cobre_por_ruta", {}) or {}
        ),
        "cu_especial_por_ruta": {
            str(k): bool(v)
            for k, v in (getattr(tab.app, "cu_especial_por_ruta", {}) or {}).items()
            if v
        },
        "plasma_compensada_por_ruta": {
            str(k): bool(v)
            for k, v in (getattr(tab.app, "plasma_compensada_por_ruta", {}) or {}).items()
            if v
        },
        "plasma_dxf_por_ruta": {
            str(k): str(v)
            for k, v in (getattr(tab.app, "plasma_dxf_por_ruta", {}) or {}).items()
            if v
        },
        "wo_reales_por_lote": {0: str(n_wo)},
        "ultimos_escenarios": getattr(tab.app, "ultimos_escenarios", []),
        "dxf_export_cache": dxf_export_cache,
        "ui_state": _construir_ui_state_workspace(tab, multilote, lote_idx=0),
        "vista_actual": _vista_desde_resultados(mini_resultados),
        "export_meta": {
            "lote_idx_origen": lote_idx,
            "work_order": str(n_wo),
            "lote_k": k_val,
        },
    }
    return _deepcopy_jsonsafe(payload)


def construir_payload_workspace(tab):
    multilote = _get_resultados_multilote(tab)
    if not multilote:
        raise ValueError("No hay resultados de nesting para guardar.")

    try:
        from modules.nesting_engine.display_geometry import asegurar_dxf_export_cache_para_guardar

        dxf_export_cache = asegurar_dxf_export_cache_para_guardar(
            multilote,
            log=lambda msg, **kw: print(f"[ARGANEST-SAVE] {msg}", flush=True),
        )
    except Exception as exc:
        dxf_export_cache = {"transform_ready": False, "error": str(exc)}

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
        "workspace_material_kind": clasificar_material_workspace(
            {
                "resultados_multilote": multilote,
                "datos_partes_actuales": datos_partes,
                "editable_inputs_by_lote": editable_inputs_by_lote,
            }
        ),
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
        "orientacion_cobre_por_ruta": dict(
            getattr(tab.app, "orientacion_cobre_por_ruta", {}) or {}
        ),
        "cu_especial_por_ruta": {
            str(k): bool(v)
            for k, v in (getattr(tab.app, "cu_especial_por_ruta", {}) or {}).items()
            if v
        },
        "plasma_compensada_por_ruta": {
            str(k): bool(v)
            for k, v in (getattr(tab.app, "plasma_compensada_por_ruta", {}) or {}).items()
            if v
        },
        "plasma_dxf_por_ruta": {
            str(k): str(v)
            for k, v in (getattr(tab.app, "plasma_dxf_por_ruta", {}) or {}).items()
            if v
        },
        "wo_reales_por_lote": getattr(tab.app, "wo_reales_por_lote", {}) or {},
        "ultimos_escenarios": getattr(tab.app, "ultimos_escenarios", []),
        "dxf_export_cache": dxf_export_cache,
        "ui_state": _construir_ui_state_workspace(
            tab, multilote, lote_idx=int(getattr(tab, "lote_actual_idx", 0) or 0)
        ),
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


def guardar_workspace_payload(payload, ruta_archivo):
    import tempfile

    if isinstance(payload, dict) and not payload.get("workspace_material_kind"):
        try:
            payload["workspace_material_kind"] = clasificar_material_workspace(payload)
        except Exception:
            payload["workspace_material_kind"] = MATERIAL_KIND_STEEL

    carpeta = os.path.dirname(os.path.abspath(ruta_archivo))
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)

    ext = os.path.splitext(str(ruta_archivo))[1].lower()
    json_text = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # Poka-yoke: escritura atómica (tmp + replace) para no truncar .arganest.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".arganest_",
        suffix=".tmp",
        dir=carpeta or None,
    )
    try:
        os.close(fd)
        if ext in COMPRESSED_WORKSPACE_EXTS:
            with gzip.open(tmp_path, "wb", compresslevel=6) as f:
                f.write(json_text.encode("utf-8"))
        else:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(json_text)
        os.replace(tmp_path, ruta_archivo)
    except Exception:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise

    return payload


def guardar_workspace(tab, ruta_archivo):
    payload = construir_payload_workspace(tab)
    return guardar_workspace_payload(payload, ruta_archivo)


def cargar_workspace_desde_archivo(ruta_archivo, *, log=None):
    _log = log or (lambda msg, **_: None)
    t_read = time.perf_counter()
    size_mb = 0.0
    try:
        size_mb = os.path.getsize(ruta_archivo) / (1024 * 1024)
    except OSError:
        pass
    _log(f"Leyendo archivo ({size_mb:.2f} MB)…", phase="ARCHIVO")

    with open(ruta_archivo, "rb") as f:
        raw = f.read()

    if not raw:
        raise ValueError("El archivo está vacío.")

    # Compatibilidad automática:
    # - Workspaces nuevos .arganest/.navanest comprimidos con gzip.
    # - Workspaces viejos en JSON plano.
    if raw[:2] == b"\x1f\x8b":
        _log("Descomprimiendo gzip…", phase="ARCHIVO")
        text = gzip.decompress(raw).decode("utf-8")
    else:
        text = raw.decode("utf-8")

    _log(f"Parseando JSON ({len(text) / (1024 * 1024):.2f} MB texto)…", phase="ARCHIVO")
    payload = json.loads(text)
    _log(f"JSON listo ({time.perf_counter() - t_read:.1f}s)", phase="ARCHIVO")

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


def enlazar_rutas_en_payload(payload, *, log=None) -> int:
    """Fase rápida: solo rutas DXF desde PARTS (sin leer geometría)."""
    _log = log or log_arganest_load
    if not isinstance(payload, dict):
        return 0
    try:
        from modules.nesting_engine.manager import enriquecer_hoja_export_desde_partes
    except Exception:
        return 0

    multilote = payload.get("resultados_multilote") or []
    datos_partes = payload.get("datos_partes_actuales") or []
    total = 0
    _log("Enlazando rutas DXF desde PARTS (sin geometría)…", phase="RUTAS")
    t0 = time.perf_counter()

    def _enlazar_mapa(data) -> int:
        n = 0
        if not isinstance(data, dict):
            return 0
        for clave, info in data.items():
            if not isinstance(info, dict):
                continue
            for hoja in info.get("hojas") or []:
                if isinstance(hoja, dict):
                    n += enriquecer_hoja_export_desde_partes(hoja, clave, datos_partes)
        return n

    for lote in multilote:
        if not isinstance(lote, dict):
            continue
        total += _enlazar_mapa(lote.get("data"))
    # Workspaces con resultados_nesting plano (LAB / nests antiguos) también necesitan rutas.
    total += _enlazar_mapa(payload.get("resultados_nesting") or payload.get("resultados"))
    _log(f"Rutas enlazadas: {total} ({time.perf_counter() - t0:.1f}s)", phase="RUTAS")
    return total


def preparar_dxf_en_payload(payload, *, log=None, solo_rutas: bool = False) -> dict:
    """
    En background: rutas (+ geometría 1:1 paralela salvo solo_rutas=True).
    Modifica resultados_multilote dentro del payload in-place.
    """
    _log = log or log_arganest_load
    if not isinstance(payload, dict):
        return {}

    multilote = payload.get("resultados_multilote") or []
    n_rutas = enlazar_rutas_en_payload(payload, log=_log)

    if solo_rutas:
        payload["_geom_prep_done"] = False
        payload["_rutas_prep_done"] = True
        return {"rutas": n_rutas, "geom_ok": 0, "solo_rutas": True}

    _log(f"Refinando transformaciones export 1:1 en paralelo…", phase="DXF")
    t_dxf = time.perf_counter()
    try:
        from modules.nesting_engine.display_geometry import preparar_transform_export_multilote_paralelo

        geom_stats = preparar_transform_export_multilote_paralelo(multilote, log=_log)
    except Exception as exc:
        _log(f"Geom paralelo falló: {exc}", phase="DXF")
        geom_stats = {}

    elapsed = time.perf_counter() - t_dxf
    stats = {
        "lotes": len(multilote),
        "rutas": n_rutas,
        "geom_ok": int(geom_stats.get("geom_ok", 0) or 0),
        "piezas": int(geom_stats.get("piezas", 0) or 0),
        "omitidas": int(geom_stats.get("omitidas", 0) or 0),
        "pendientes": int(geom_stats.get("pendientes", 0) or 0),
        "workers": geom_stats.get("workers", 0),
        "dxf_unicos": geom_stats.get("dxf_unicos", 0),
        "segundos_dxf": round(elapsed, 1),
    }
    payload["_geom_prep_done"] = True
    payload["_geom_prep_stats"] = stats
    _log(
        f"DXF listo en {elapsed:.1f}s — {stats['geom_ok']}/{stats['piezas']} piezas "
        f"({stats.get('omitidas', 0)} omitidas, {stats.get('workers', '?')} workers)",
        phase="DXF",
    )
    return stats


def preparar_dxf_en_app(app, *, log=None) -> dict:
    """Transformaciones export 1:1 sobre el nest ya cargado (background post-UI)."""
    multilote = getattr(app, "resultados_multilote", None) or []
    payload = {"resultados_multilote": multilote}
    enlazar_rutas_en_payload(payload, log=log)
    _log = log or log_arganest_load
    _log("Transformaciones export 1:1 en app (paralelo)…", phase="DXF")
    t0 = time.perf_counter()
    try:
        from modules.nesting_engine.display_geometry import preparar_transform_export_multilote_paralelo

        geom_stats = preparar_transform_export_multilote_paralelo(multilote, log=_log)
    except Exception as exc:
        _log(f"Transform paralelo falló: {exc}", phase="DXF")
        return {}
    elapsed = time.perf_counter() - t0
    stats = {
        "geom_ok": int(geom_stats.get("ok", geom_stats.get("geom_ok", 0)) or 0),
        "piezas": int(geom_stats.get("piezas", 0) or 0),
        "omitidas": int(geom_stats.get("omitidas", 0) or 0),
        "segundos_dxf": round(elapsed, 1),
        "workers": geom_stats.get("workers", 0),
        "cache_hits": int(geom_stats.get("omitidas", 0) or 0),
    }
    setattr(app, "_geom_prep_stats", stats)
    setattr(app, "_transform_export_done", True)
    setattr(app, "_geom_prep_done", True)
    _log(
        f"Transform listo en {elapsed:.1f}s — "
        f"{stats['geom_ok']}/{stats['piezas']} piezas",
        phase="DXF",
    )
    return stats


def preparar_dxf_cache_en_payload(payload, *, log=None) -> dict:
    """
    Tras leer .arganest: reconoce transform ya guardada y evita reproceso
    si dxf_export_cache / sellos por pieza están completos.
    """
    _log = log or log_arganest_load
    if not isinstance(payload, dict):
        return {}
    multilote = payload.get("resultados_multilote") or []
    try:
        from modules.nesting_engine.display_geometry import (
            auditar_cache_dxf_multilote,
            normalizar_sellos_dxf_en_multilote,
        )

        normalizar_sellos_dxf_en_multilote(multilote)
        audit = auditar_cache_dxf_multilote(multilote)
    except Exception as exc:
        _log(f"Auditoría cache DXF falló: {exc}", phase="DXF")
        return {}

    saved_cache = payload.get("dxf_export_cache") or {}
    cache_version = int(saved_cache.get("version", 0) or 0)
    payload["dxf_export_cache"] = audit

    if audit.get("transform_ready"):
        payload["_geom_prep_done"] = True
        payload["_transform_export_done"] = True
        payload["_geom_prep_stats"] = {
            "geom_ok": audit.get("transform_ok", 0),
            "piezas": audit.get("piezas_con_ruta", 0),
            "omitidas": audit.get("piezas_virtual", 0) + audit.get("piezas_sin_ruta", 0),
            "segundos_dxf": 0,
            "desde_cache": True,
        }
        src = "archivo" if saved_cache.get("transform_ready") and cache_version else "piezas"
        _log(
            f"Cache DXF export OK ({src}): "
            f"{audit.get('transform_ok', 0)}/{audit.get('piezas_con_ruta', 0)} "
            f"piezas — sin reproceso",
            phase="DXF",
        )
    else:
        payload["_geom_prep_done"] = False
        payload["_transform_export_done"] = False
        _log(
            f"Cache DXF incompleta: {audit.get('pendientes_transform', '?')} "
            f"pieza(s) pendiente(s) de transform",
            phase="DXF",
        )

    return audit


def _relink_rutas_dxf_en_multilote(app, datos_partes, *, incluir_geom: bool = True) -> int:
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

    if incluir_geom:
        try:
            from modules.nesting_engine.display_geometry import preparar_transform_export_multilote_paralelo

            log_arganest_load("Refrescando transform export (modo sync, paralelo)…", phase="DXF")
            t0 = time.perf_counter()
            preparar_transform_export_multilote_paralelo(multilote, log=log_arganest_load)
            log_arganest_load(
                f"Transform export lista ({time.perf_counter() - t0:.1f}s)",
                phase="DXF",
            )
        except Exception as exc:
            log_arganest_load(f"Geometría 1:1 omitida: {exc}", phase="DXF")

    return total


def aplicar_workspace(tab, payload, *, carga_rapida: bool = False):
    if not isinstance(payload, dict):
        raise ValueError("Payload inválido para aplicar workspace.")

    def _avanzar(msg, pct):
        log_arganest_load(msg, phase="UI")
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
    # Rotación manual de piezas CU (PARTS): debe sobrevivir guardar/abrir .arganest
    # para que cualquier renesteo respete la orientación elegida por el usuario.
    try:
        tab.app.orientacion_cobre_por_ruta = {
            str(k): int(v) % 360
            for k, v in (payload.get("orientacion_cobre_por_ruta") or {}).items()
        }
    except Exception:
        tab.app.orientacion_cobre_por_ruta = {}
    try:
        tab.app.cu_especial_por_ruta = {
            str(k): bool(v)
            for k, v in (payload.get("cu_especial_por_ruta") or {}).items()
            if v
        }
    except Exception:
        tab.app.cu_especial_por_ruta = {}
    try:
        tab.app.plasma_compensada_por_ruta = {
            str(k): bool(v)
            for k, v in (payload.get("plasma_compensada_por_ruta") or {}).items()
            if v
        }
    except Exception:
        tab.app.plasma_compensada_por_ruta = {}
    try:
        tab.app.plasma_dxf_por_ruta = {
            str(k): str(v)
            for k, v in (payload.get("plasma_dxf_por_ruta") or {}).items()
            if v
        }
    except Exception:
        tab.app.plasma_dxf_por_ruta = {}
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

    if payload.get("_geom_prep_done"):
        stats = payload.get("_geom_prep_stats") or {}
        log_arganest_load(
            f"DXF ya preparado "
            f"({stats.get('geom_ok', '?')}/{stats.get('piezas', '?')} piezas, "
            f"{stats.get('segundos_dxf', '?')}s)",
            phase="UI",
        )
    elif payload.get("_rutas_prep_done"):
        log_arganest_load("Rutas enlazadas; geom 1:1 continúa en background", phase="UI")
    else:
        log_arganest_load("Enlace DXF en hilo UI (modo sync)…", phase="UI")
        _relink_rutas_dxf_en_multilote(tab.app, datos_partes, incluir_geom=True)

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
        gk = float(ui.get("global_kerf_val", 0.15) or 0.15)
    except Exception:
        gk = 0.15
    if gk <= 0:
        gk = 0.15
    tab.global_kerf_val = gk
    if hasattr(tab, "_sync_kerf_widget"):
        tab._sync_kerf_widget()
    tab.global_corner_val = str(ui.get("global_corner_val", "INFERIOR IZQUIERDA") or "INFERIOR IZQUIERDA")
    tab.costo_usd_val = float(ui.get("costo_usd_val", 0.0) or 0.0)
    tab.costo_mxn_val = float(ui.get("costo_mxn_val", 0.0) or 0.0)
    tab.lote_actual_idx = lote_idx

    ml = tab.app.resultados_multilote or []
    if ml and 0 <= lote_idx < len(ml):
        slot = ml[lote_idx].get("data")
        if ml[lote_idx].get("gemelo_desync") and isinstance(slot, dict):
            tab.app.resultados_nesting = copy.deepcopy(slot)
            ml[lote_idx]["data"] = tab.app.resultados_nesting
        else:
            tab.app.resultados_nesting = slot or {}
    else:
        tab.app.resultados_nesting = {}

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

        kerf_g = float(getattr(tab, "global_kerf_val", 0.15) or 0.15)
        if kerf_g <= 0:
            kerf_g = 0.15
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
    log_arganest_load("Workspace aplicado en UI", phase="FIN")
    return True