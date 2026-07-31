import glob
import os
import re
from shapely.geometry import Polygon, MultiPolygon


RUTA_CAMA_LASER = "CAMA LASER SIN MINI NEST"
RUTA_CAMA_LASER_12KW = "CAMA LASER 12 KW SIN MINI NEST"
RUTA_NESTEOS_COBRE = "NESTEOS DE COBRE"
RUTA_ROBOT_LASER = "ROBOT LASER + MINI NEST"
RUTA_ROBOT_PLASMA = "ROBOT PLASMA"
REPORTE_PDF_NESTING = "REPORTE DE NESTEO PDF"
ARCHIVO_ARGANEST_NESTING = "ARCHIVO DE NESTEO ARGANEST"


def step_universal_sin_camas_activo() -> bool:
    """
    Overlay sobre el flujo robot Cama A/B.
    True → STEP 1:1 en todas las carpetas DXF de acero (sin A/B).
    False → solo robot láser/plasma con Cama A + Cama B.
    """
    try:
        import config as _cfg

        return bool(getattr(_cfg, "STEP_UNIVERSAL_SIN_CAMAS", False))
    except Exception:
        return False


def _hay_dxfs(ruta):
    return bool(_listar_dxfs_en_carpeta(ruta))


def _listar_dxfs_en_carpeta(ruta):
    ruta = os.path.normpath(str(ruta or "").strip())
    if not ruta or not os.path.isdir(ruta):
        return []
    archivos = sorted(glob.glob(os.path.join(ruta, "*.dxf")))
    archivos.extend(sorted(glob.glob(os.path.join(ruta, "*.DXF"))))
    vistos = set()
    unicos = []
    for path in archivos:
        key = os.path.normcase(path)
        if key in vistos:
            continue
        vistos.add(key)
        unicos.append(path)
    return unicos


def _localizar_carpeta_dxf(carpeta_esperada: str, job_root_dir: str, etiqueta_familia: str) -> str:
    candidatos = []
    for raw in (
        carpeta_esperada,
        os.path.join(job_root_dir, etiqueta_familia, "DXF") if job_root_dir else "",
        os.path.join(job_root_dir, "NESTING", etiqueta_familia, "DXF") if job_root_dir else "",
    ):
        cand = os.path.normpath(str(raw or "").strip())
        if cand and cand not in candidatos:
            candidatos.append(cand)

    for cand in candidatos:
        if _hay_dxfs(cand):
            if cand != os.path.normpath(carpeta_esperada):
                print(f"[STEP] DXF localizados en ruta alternativa: {cand}")
            return cand

    carpeta_esperada = os.path.normpath(str(carpeta_esperada or "").strip())
    if carpeta_esperada:
        os.makedirs(carpeta_esperada, exist_ok=True)
    return carpeta_esperada


def _es_export_swo(
    base_name: str | None = None,
    wo_label: str | None = None,
    es_swo: bool = False,
) -> bool:
    if es_swo:
        return True
    bn = str(base_name or "").strip().upper()
    wl = str(wo_label or "").strip().upper()
    return bn.startswith("SWO") or "S.W.O" in wl


def _hoja_cobre_export_3d(hoja: dict) -> str:
    """
    Formato 3D por hoja cobre:
    - sin_gap (piezas pegadas): solo DXF láser → 'dxf'
    - con_gap / RTZCU: STEP por pieza → 'step'
    """
    if not isinstance(hoja, dict) or not hoja.get("modo_largos_cu"):
        return "step"
    # RTZCU: misma lógica STEP que con_gap (nunca CyPTube vertical / solo divisorias).
    if hoja.get("cu_rtz_virtual"):
        return "step"
    if str(hoja.get("cu_modo_separacion_barra") or "").strip().lower() == "sin_gap":
        return "dxf"
    return "step"


def _hoja_cobre_export_iges(hoja: dict) -> bool:
    """Compatibilidad tests — IGES ya no se usa en cobre largos."""
    return False


def _cu_dxf_requiere_3d(fmt: str) -> bool:
    return str(fmt or "step").strip().lower() not in ("dxf", "none", "2d")


def _build_cu_formato_por_dxf(resultados: dict) -> dict[str, str]:
    formato: dict[str, str] = {}
    for data in (resultados or {}).values():
        if not isinstance(data, dict) or not data.get("modo_largos_cu"):
            continue
        for hoja in data.get("hojas") or []:
            if not isinstance(hoja, dict):
                continue
            for item in hoja.get("pqart_exports") or []:
                if not isinstance(item, dict):
                    continue
                nombre = str(item.get("nombre_dxf") or os.path.basename(str(item.get("ruta") or ""))).strip()
                if not nombre:
                    continue
                fmt = str(item.get("export_3d_format") or "step").strip().lower()
                if fmt in ("iges", "igs"):
                    fmt = "step"
                if not _cu_dxf_requiere_3d(fmt):
                    continue
                formato[nombre] = fmt
    return formato


def obtener_resumen_step_ultimo_export() -> dict[str, dict[str, int]]:
    """Resumen DXF vs STEP del último export (vacío si no hubo conversión 3D)."""
    return dict(_ULTIMO_RESUMEN_STEP)


_ULTIMO_RESUMEN_STEP: dict[str, dict[str, int]] = {}


def _auditar_steps_en_rutas(
    rutas: dict,
    *,
    cu_formato_por_dxf: dict[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    universal = step_universal_sin_camas_activo()
    if universal:
        familias = [
            ("NESTEOS DE COBRE", "nesteos_cobre_dxf", "nesteos_cobre_step"),
            ("CAMA LASER", "cama_laser_dxf", "cama_laser_step"),
            ("CAMA LASER 12KW", "cama_laser_12kw_dxf", "cama_laser_12kw_step"),
            ("ROBOT LASER", "robot_laser_dxf", "robot_laser_step"),
            ("ROBOT PLASMA", "robot_plasma_dxf", "robot_plasma_step"),
        ]
    else:
        familias = [
            ("NESTEOS DE COBRE", "nesteos_cobre_dxf", "nesteos_cobre_step"),
            ("ROBOT LASER", "robot_laser_dxf", "robot_laser_step_A"),
            ("ROBOT PLASMA", "robot_plasma_dxf", "robot_plasma_step_A"),
        ]
    resumen: dict[str, dict[str, int]] = {}
    fmt_map = {str(k): str(v).lower() for k, v in (cu_formato_por_dxf or {}).items()}
    for etiqueta, dxf_key, step_key in familias:
        dxf_dir = os.path.normpath(str(rutas.get(dxf_key) or "").strip())
        step_dir = os.path.normpath(str(rutas.get(step_key) or "").strip())
        candidatos_dxf = _listar_dxfs_en_carpeta(dxf_dir) if dxf_dir else []
        n_dxf = len(candidatos_dxf)
        n_dxf_3d = n_dxf
        if fmt_map and etiqueta == "NESTEOS DE COBRE":
            n_dxf_3d = sum(
                1
                for p in candidatos_dxf
                if _cu_dxf_requiere_3d(fmt_map.get(os.path.basename(p), "step"))
            )
        n_step = 0
        if dxf_dir and fmt_map and etiqueta == "NESTEOS DE COBRE":
            try:
                from freecad_runner import _cad_path_for_dxf

                for dxf_path in candidatos_dxf:
                    nombre = os.path.basename(dxf_path)
                    fmt = fmt_map.get(nombre, "step")
                    if not _cu_dxf_requiere_3d(fmt):
                        continue
                    # Solo STEP (IGES ya no se genera).
                    cad_path = _cad_path_for_dxf(dxf_path, step_dir, "step")
                    if os.path.isfile(cad_path) and os.path.getsize(cad_path) > 512:
                        n_step += 1
            except Exception:
                pass
        else:
            if step_dir and os.path.isdir(step_dir):
                n_step = len(glob.glob(os.path.join(step_dir, "*.step")))
        if n_dxf > 0 or n_step > 0:
            resumen[etiqueta] = {
                "dxf": n_dxf,
                "dxf_3d": n_dxf_3d,
                "step": n_step,
            }
    return resumen


def estimar_conteos_export(resultados: dict, *, generar_step: bool) -> tuple[int, int]:
    """
    Estima (n_dxf, n_step) para barras de progreso.
    Flujo normal: robot láser/plasma → 2 STEP (Cama A + Cama B); CAMA LASER solo DXF.
    Overlay STEP_UNIVERSAL_SIN_CAMAS: 1 STEP por cada DXF de acero (sin A/B).
    Cobre: STEP solo si la hoja requiere 3D.
    """
    n_dxf = 0
    n_step = 0
    universal = step_universal_sin_camas_activo()
    for clave, data in (resultados or {}).items():
        if not isinstance(data, dict):
            continue
        for hoja in data.get("hojas") or []:
            if not isinstance(hoja, dict):
                continue
            carpeta = _resolver_carpeta_principal(clave, hoja)
            n_dxf += 1
            generar_plasma = _debe_generar_plasma(clave, hoja)
            if generar_plasma:
                n_dxf += 1
            if not generar_step:
                continue
            if carpeta == RUTA_NESTEOS_COBRE:
                if _cu_dxf_requiere_3d(_hoja_cobre_export_3d(hoja)):
                    n_step += 1
            elif universal:
                n_step += 1
                if generar_plasma:
                    n_step += 1
            else:
                if carpeta == RUTA_ROBOT_LASER:
                    n_step += 2
                if generar_plasma:
                    n_step += 2
    return n_dxf, n_step


def _step_dir_key_for_familia(etiqueta: str) -> str:
    """Clave de rutas[] para la carpeta STEP según overlay o flujo A/B."""
    universal = step_universal_sin_camas_activo()
    mapping = {
        "NESTEOS DE COBRE": "nesteos_cobre_step",
        "CAMA LASER": "cama_laser_step",
        "CAMA LASER 12KW": "cama_laser_12kw_step",
        "ROBOT LASER": "robot_laser_step" if universal else "robot_laser_step_A",
        "ROBOT PLASMA": "robot_plasma_step" if universal else "robot_plasma_step_A",
    }
    return mapping.get(etiqueta, "")


def _dxf_dir_key_for_familia(etiqueta: str) -> str:
    mapping = {
        "NESTEOS DE COBRE": "nesteos_cobre_dxf",
        "CAMA LASER": "cama_laser_dxf",
        "CAMA LASER 12KW": "cama_laser_12kw_dxf",
        "ROBOT LASER": "robot_laser_dxf",
        "ROBOT PLASMA": "robot_plasma_dxf",
    }
    return mapping.get(etiqueta, "")


def _validar_steps_tras_export(
    rutas: dict,
    *,
    log_fn=None,
    cu_formato_por_dxf: dict[str, str] | None = None,
    motor_3d: str = "freecad",
) -> dict[str, dict[str, int]]:
    """Falla el export si había DXF de cobre/láser y no se generó el STEP esperado."""
    _log = log_fn or (lambda _msg: None)
    motor = str(motor_3d or "freecad").strip().lower()
    motor_label = "Arga Nesting Suite (OCCT)" if motor in ("occt", "arga", "nans") else "FreeCAD"
    universal = step_universal_sin_camas_activo()
    fmt_map = {str(k): str(v).lower() for k, v in (cu_formato_por_dxf or {}).items()}
    resumen = _auditar_steps_en_rutas(rutas, cu_formato_por_dxf=cu_formato_por_dxf)
    for etiqueta, counts in resumen.items():
        n_dxf = int(counts.get("dxf") or 0)
        n_dxf_3d = int(counts.get("dxf_3d") if counts.get("dxf_3d") is not None else n_dxf)
        n_step = int(counts.get("step") or 0)
        if n_dxf <= 0:
            continue
        _log(
            f"3D audit [{etiqueta}] ({motor_label}): {n_step} STEP / {n_dxf_3d} DXF con 3D"
            + (f" ({n_dxf - n_dxf_3d} solo DXF)" if n_dxf > n_dxf_3d else "")
        )
        if n_dxf_3d <= 0:
            continue
        step_key = _step_dir_key_for_familia(etiqueta)
        dxf_key = _dxf_dir_key_for_familia(etiqueta)
        if n_step <= 0:
            step_dir = rutas.get(step_key, "")
            if motor in ("occt", "arga", "nans"):
                log_hint = step_dir or "(sin carpeta STEP)"
            else:
                log_hint = os.path.join(step_dir, "_logs", "freecad_macro.log") if step_dir else ""
            raise RuntimeError(
                f"{motor_label} no generó archivos STEP en {etiqueta} "
                f"({n_dxf_3d} DXF con 3D, 0 STEP). Revisa: {log_hint}"
            )
        if n_step < n_dxf_3d:
            step_dir = os.path.normpath(str(rutas.get(step_key, "") or "").strip())
            dxf_dir = os.path.normpath(str(rutas.get(dxf_key, "") or "").strip())
            faltantes: list[str] = []
            if dxf_dir:
                try:
                    from freecad_runner import _cad_path_for_dxf, _cad_is_current

                    for dxf_path in _listar_dxfs_en_carpeta(dxf_dir):
                        nombre = os.path.basename(dxf_path)
                        if fmt_map and etiqueta == "NESTEOS DE COBRE":
                            fmt = fmt_map.get(nombre, "step")
                            if not _cu_dxf_requiere_3d(fmt):
                                continue
                        if not step_dir:
                            continue
                        cad_path = _cad_path_for_dxf(dxf_path, step_dir, "step")
                        if not _cad_is_current(dxf_path, cad_path):
                            faltantes.append(nombre)
                except Exception:
                    pass
            detalle_faltantes = (
                f" Faltan: {', '.join(faltantes[:12])}"
                + (f" (+{len(faltantes) - 12} más)" if len(faltantes) > 12 else "")
                if faltantes
                else ""
            )
            _log(
                f"WARN [{etiqueta}]: STEP incompletos ({n_step} / {n_dxf_3d})."
                f"{detalle_faltantes} "
                "Re-exportar reanuda solo los pendientes."
            )

    # Poka-yoke Cama A/B: solo en flujo normal (láser y plasma)
    if not universal:
        try:
            from .nest_poka_yoke import validar_step_cama_ab_pares

            for etiqueta, key_a, key_b in (
                ("ROBOT LASER", "robot_laser_step_A", "robot_laser_step_B"),
                ("ROBOT PLASMA", "robot_plasma_step_A", "robot_plasma_step_B"),
            ):
                ok_ab, msg_ab = validar_step_cama_ab_pares(
                    rutas.get(key_a, ""),
                    rutas.get(key_b, ""),
                    etiqueta=etiqueta,
                    motor_label=motor_label,
                )
                if not ok_ab:
                    raise RuntimeError(msg_ab)
                na = int((resumen.get(etiqueta) or {}).get("step") or 0)
                if na > 0:
                    _log(f"3D poka-yoke [{etiqueta}]: Cama A/B OK ({na} STEP en A)")
        except RuntimeError:
            raise
        except Exception as exc_py:
            _log(f"WARN poka-yoke Cama A/B: {exc_py}")
    else:
        _log("3D overlay: STEP_UNIVERSAL_SIN_CAMAS activo (sin poka-yoke Cama A/B)")

    return resumen


def _generar_steps_cobre_fuentes_in_place(resultados: dict, job_root_dir: str, *, log_fn=None) -> int:
    """Escribe manifiesto de DXF fuente cobre y genera STEP junto a cada DXF (sin mover)."""
    from modules.cobre_step_fuentes import (
        escribir_manifest_cobre,
        procesar_steps_cobre_en_ubicacion_fuentes,
        recolectar_fuentes_cobre_desde_resultados,
    )

    manifest = recolectar_fuentes_cobre_desde_resultados(resultados or {})
    fuentes = manifest.get("fuentes") or []
    if not fuentes:
        return 0

    dest_manifest = os.path.join(job_root_dir, RUTA_NESTEOS_COBRE)
    path = escribir_manifest_cobre(dest_manifest, manifest)
    if log_fn:
        log_fn(f"Manifiesto cobre: {path} ({len(fuentes)} DXF fuente)")

    esp_in = float(manifest.get("espesor_in") or 0.25)
    thk_mm = esp_in * 25.4
    res = procesar_steps_cobre_en_ubicacion_fuentes(
        manifest,
        thk_mm=thk_mm,
        log_fn=log_fn,
    )
    ok_dirs = sum(1 for _c, ok, _d in res if ok)
    if log_fn:
        log_fn(f"STEP cobre in-place: {ok_dirs}/{len(res)} carpeta(s) fuente")
    return ok_dirs


def _safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def _sheet_dims_in(hoja):
    w_mm = _safe_float(hoja.get("placa_w", 0.0), 0.0)
    h_mm = _safe_float(hoja.get("placa_h", 0.0), 0.0)
    a_in = w_mm / 25.4
    b_in = h_mm / 25.4
    return min(a_in, b_in), max(a_in, b_in)  # W, L


def _parse_thickness_from_clave(clave):
    raw = str(clave).split("_", 1)[0].strip()
    return raw, _safe_float(raw)


def _is_gauge_range_half_to_2(raw_thk, thk_val):
    """
    Regla nueva:
    - Espesor en rango 1/2" a 2.000"
    - No interpreta calibres enteros (16, 14, etc.) para 12KW.
    """
    if thk_val is None:
        return False

    if thk_val > 2.0:
        return False

    return 0.5 <= thk_val <= 2.0


def _piece_dims_from_polys_in(pz):
    pols = pz.get("poligonos", []) or []
    if not pols or not pols[0]:
        return 0.0, 0.0

    pts = []
    for poly in pols:
        if not poly:
            continue
        for pt in poly:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                try:
                    pts.append((float(pt[0]), float(pt[1])))
                except Exception:
                    pass

    if not pts:
        return 0.0, 0.0

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    dx_in = (max(xs) - min(xs)) / 25.4
    dy_in = (max(ys) - min(ys)) / 25.4
    return min(dx_in, dy_in), max(dx_in, dy_in)  # W, L


def _has_big_component(hoja):
    for pz in hoja.get("piezas", []):
        nom = pz.get("nombre", "PART")

        if nom.startswith("REF__"):
            continue
        if nom.startswith("TATUAJE_"):
            continue
        if nom.startswith("RETAZO_GUILLOTINA"):
            continue
        if nom.startswith("CU_CORTE__"):
            continue

        w_in, l_in = _piece_dims_from_polys_in(pz)
        if w_in >= 80.0 and l_in >= 140.0:
            return True

    return False


def _resolver_carpeta_principal(clave, hoja):
    if bool(hoja.get("modo_largos_cu")):
        return RUTA_NESTEOS_COBRE

    partes = str(clave or "").split("_", 1)
    if len(partes) > 1 and str(partes[1]).strip().upper() == "CU":
        return RUTA_NESTEOS_COBRE

    raw_thk, thk_val = _parse_thickness_from_clave(clave)
    w_in, l_in = _sheet_dims_in(hoja)

    # 1) Placas de rango 1/2 a 2 y máximo 60x120
    if _is_gauge_range_half_to_2(raw_thk, thk_val) and w_in <= 60.0 and l_in <= 120.0:
        return RUTA_CAMA_LASER_12KW

    # 2) Placas <= 3/8 y largo <= 120
    if thk_val is not None and thk_val <= 0.375 and l_in <= 120.0:
        return RUTA_CAMA_LASER

    # 3) Todo lo demás queda en Robot Láser
    # Aquí caen:
    # - placas <= 3/8 con largo hasta 240
    # - placas > 3/8 que NO van a plasma
    return RUTA_ROBOT_LASER


def _debe_generar_plasma(clave, hoja):
    """
    ROBOT PLASMA solo si hubo compensación plasma (manual).
    Antes también se generaba para calibre > 3/8 con pieza grande aunque
    no se hubiera compensado; eso metía DXF 1:1 en la carpeta plasma.
    """
    if bool(hoja.get("plasma_compensado_manual", False)):
        return True
    for pz in hoja.get("piezas") or []:
        if bool(pz.get("plasma_compensada_manual")):
            return True
    return False

def _slug_token(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_") or "NA"


def _resolver_display_name_hoja(
    hoja,
    contador_placas,
    *,
    contador_rtz=None,
    contador_rtzc=None,
    calibre=None,
    wo_name=None,
):
    display_forzado = str(hoja.get("sheet_display_name") or "").strip()
    if display_forzado:
        return display_forzado

    from .efficiency_metrics import (
        allocar_nombre_rtz_sobrante_db,
        allocar_nombre_rtzc_sobrante_db,
        hoja_es_sobrante_plasma_compensado,
        hoja_es_sobrante_sin_compra,
        hoja_excluida_de_rtz_sobrante,
    )

    if hoja_es_sobrante_sin_compra(hoja) and not hoja_excluida_de_rtz_sobrante(hoja):
        nombre_rtz = str(hoja.get("placa_id") or hoja.get("sheet_display_name") or "").strip()
        if nombre_rtz.upper().startswith("RTZC") or nombre_rtz.upper().startswith("RTZ"):
            return nombre_rtz
        if hoja_es_sobrante_plasma_compensado(hoja) and contador_rtzc is not None and calibre and wo_name:
            return allocar_nombre_rtzc_sobrante_db(
                contador_rtzc, calibre, wo_name, hoja=hoja
            )
        if contador_rtz is not None and calibre and wo_name:
            return allocar_nombre_rtz_sobrante_db(
                contador_rtz, calibre, wo_name, hoja=hoja
            )

    codigo_base = str(hoja.get("placa_id", "STOCK")).strip() or "STOCK"

    # Los RTZ se quedan con su nombre visible original
    if "RTZ" in codigo_base.upper():
        return codigo_base

    # El resto conserva la lógica actual P1 / P2 / P3
    contador_placas[codigo_base] = contador_placas.get(codigo_base, 0) + 1
    return f"{codigo_base} P{contador_placas[codigo_base]}"


def _inyectar_metadata_hoja(
    hoja: dict,
    *,
    order_label: str,
    thickness_name: str,
    sheet_seq: int,
    display_name: str,
    source_nest_name: str,
):
    if not isinstance(hoja, dict):
        return

    placa_w = _safe_float(hoja.get("placa_w", 0.0), 0.0) or 0.0
    placa_h = _safe_float(hoja.get("placa_h", 0.0), 0.0) or 0.0

    if hoja.get("cu_rtz_virtual"):
        sheet_code = str(
            hoja.get("placa_id")
            or hoja.get("cu_rtz_id")
            or hoja.get("sheet_code")
            or f"{order_label}-H{sheet_seq}"
        ).strip()
    else:
        sheet_code = str(hoja.get("sheet_code") or f"{order_label}-H{sheet_seq}")
    default_sheet_uid = (
        f"{_slug_token(order_label)}__"
        f"{_slug_token(thickness_name)}__"
        f"H{int(sheet_seq):03d}__"
        f"{_slug_token(display_name)}"
    )

    sheet_uid = str(hoja.get("sheet_uid") or default_sheet_uid)
    nest_instance_id = str(
        hoja.get("nest_instance_id") or f"{_slug_token(source_nest_name)}__{int(sheet_seq):03d}"
    )

    hoja["sheet_seq"] = int(hoja.get("sheet_seq") or sheet_seq)
    hoja["sheet_code"] = sheet_code
    hoja["sheet_uid"] = sheet_uid
    hoja["sheet_display_name"] = str(hoja.get("sheet_display_name") or display_name)
    hoja["plate_group_key"] = str(hoja.get("plate_group_key") or sheet_uid)
    hoja["placa_ancho_canonico_mm"] = round(float(hoja.get("placa_ancho_canonico_mm") or placa_w), 2)
    hoja["placa_largo_canonico_mm"] = round(float(hoja.get("placa_largo_canonico_mm") or placa_h), 2)
    hoja["nest_instance_id"] = nest_instance_id
    hoja["source_nest_name"] = str(hoja.get("source_nest_name") or source_nest_name)
    from .efficiency_metrics import hoja_es_sobrante_plasma_compensado, hoja_es_sobrante_sin_compra

    es_rtzc = bool(
        hoja.get("is_rtz_plasma_sobrante") or hoja_es_sobrante_plasma_compensado(hoja)
    )
    hoja["is_rtz_plasma_sobrante"] = es_rtzc
    if es_rtzc:
        hoja["rtz_tipo"] = str(hoja.get("rtz_tipo") or "COMPENSADO")

    hoja["is_rtz"] = bool(
        hoja.get("is_rtz")
        or hoja_es_sobrante_sin_compra(hoja)
        or str(display_name).upper().startswith("RTZ")
    )

def _normalizar_tipo_corte_pqart(nombre_carpeta: str) -> str:
    carpeta = str(nombre_carpeta or "").strip().upper()

    if carpeta in {
        RUTA_NESTEOS_COBRE.upper(),
        RUTA_CAMA_LASER.upper(),
        RUTA_CAMA_LASER_12KW.upper(),
    }:
        return "CamaLaser"

    if carpeta == RUTA_ROBOT_LASER.upper():
        return "RobotLaser"

    if carpeta == RUTA_ROBOT_PLASMA.upper():
        return "Plasma"

    return "CamaLaser"


def _sanitize_ring_coords(ring, decimals=4):
    out = []
    for pt in (ring or []):
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            x = round(float(pt[0]), decimals)
            y = round(float(pt[1]), decimals)
        except Exception:
            continue
        if out and abs(out[-1][0] - x) < 1e-9 and abs(out[-1][1] - y) < 1e-9:
            continue
        out.append((x, y))
    if len(out) >= 2 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out = out[:-1]
    return out


def _clean_profile_for_production(outer, holes):
    """
    Normaliza geometría para DXF de producción:
    - valida/topología con buffer(0)
    - elimina vértices duplicados
    - redondea coordenadas
    - filtra huecos degenerados
    """
    try:
        outer_s = _sanitize_ring_coords(outer)
        holes_s = []
        for h in (holes or []):
            hh = _sanitize_ring_coords(h)
            if len(hh) >= 3:
                holes_s.append(hh)
        if len(outer_s) < 3:
            return outer, holes

        poly = Polygon(outer_s, holes_s)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly is None or poly.is_empty:
            return outer, holes
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: float(g.area))

        out_outer = _sanitize_ring_coords(list(poly.exterior.coords))
        out_holes = []
        for interior in poly.interiors:
            ring = _sanitize_ring_coords(list(interior.coords))
            if len(ring) < 3:
                continue
            try:
                if abs(Polygon(ring).area) < 0.50:  # mm² mínimos para evitar ruido geométrico
                    continue
            except Exception:
                continue
            out_holes.append(ring)
        if len(out_outer) < 3:
            return outer, holes
        return out_outer, out_holes
    except Exception:
        return outer, holes


def _registrar_exportacion_pqart_hoja(
    hoja: dict,
    *,
    ruta_dxf: str,
    tipo_corte: str,
):
    if not isinstance(hoja, dict):
        return

    ruta_normalizada = os.path.normpath(str(ruta_dxf or "").strip())
    if not ruta_normalizada:
        return

    exports = hoja.setdefault("pqart_exports", [])

    payload = {
        "nombre_dxf": os.path.basename(ruta_normalizada),
        "ruta": ruta_normalizada,
        "tipo_corte": str(tipo_corte or "").strip(),
        "sheet_uid": str(hoja.get("sheet_uid") or "").strip(),
        "sheet_code": str(hoja.get("sheet_code") or "").strip(),
        "sheet_display_name": str(hoja.get("sheet_display_name") or "").strip(),
        "export_3d_format": str(hoja.get("export_3d_format") or "step").strip().lower(),
    }

    ruta_cmp = os.path.normcase(ruta_normalizada)

    for item in exports:
        ruta_item = os.path.normcase(os.path.normpath(str(item.get("ruta") or "").strip()))
        if ruta_item == ruta_cmp:
            item.update(payload)
            return

    exports.append(payload)

def _job_tiene_cobre(resultados: dict) -> bool:
    for data in (resultados or {}).values():
        if isinstance(data, dict) and data.get("modo_largos_cu"):
            return True
    return False


def lanzar_freecad_robotica(
    rutas,
    thk,
    plasma_off,
    job_root_dir: str | None = None,
    *,
    es_cobre: bool = False,
    cu_formato_por_dxf: dict[str, str] | None = None,
    progress_cb=None,
):
    from freecad_runner import ejecutar_macro_freecad

    job_root = os.path.normpath(str(job_root_dir or "").strip())
    resultados = []
    fmt_map = {str(k): str(v).lower() for k, v in (cu_formato_por_dxf or {}).items()}

    def _notify(msg: str):
        if not callable(progress_cb):
            return
        try:
            progress_cb(mensaje=msg)
        except Exception:
            pass

    def _convertir(
        etiqueta,
        dxf_key,
        step_key,
        origen,
        ox,
        oy,
        oz,
        *,
        prefer_verde=False,
        material="STEEL",
        export_format="step",
        out_key=None,
        dxf_filter=None,
    ):
        dxf_dir = _localizar_carpeta_dxf(
            rutas.get(dxf_key, ""),
            job_root,
            {
                "cama_laser_dxf": RUTA_CAMA_LASER,
                "nesteos_cobre_dxf": RUTA_NESTEOS_COBRE,
                "cama_laser_12kw_dxf": RUTA_CAMA_LASER_12KW,
                "robot_laser_dxf": RUTA_ROBOT_LASER,
                "robot_plasma_dxf": RUTA_ROBOT_PLASMA,
            }.get(dxf_key, ""),
        )
        out_dir = os.path.normpath(str(rutas.get(out_key or step_key, "") or "").strip())
        cad_label = "STEP"
        candidatos = _listar_dxfs_en_carpeta(dxf_dir)
        if callable(dxf_filter):
            candidatos = [p for p in candidatos if dxf_filter(p)]
        if not candidatos:
            print(f"[{cad_label}][SKIP] {etiqueta}: sin DXF aplicables en {dxf_dir}")
            resultados.append((etiqueta, True, dxf_dir))
            _notify(f"FreeCAD [{etiqueta}]: sin DXF")
            return

        os.makedirs(out_dir, exist_ok=True)
        print(f"[{cad_label}] {etiqueta}: {len(candidatos)} DXF -> {out_dir}")
        _notify(f"FreeCAD STEP [{etiqueta}]: {len(candidatos)} DXF…")
        ok = ejecutar_macro_freecad(
            dxf_dir,
            out_dir,
            thk,
            origen,
            ox, oy, oz,
            prefer_verde=prefer_verde,
            max_intentos=2,
            material=material,
            export_format=export_format,
            dxf_filter=dxf_filter,
        )
        resultados.append((etiqueta, ok, dxf_dir))
        _notify(f"FreeCAD STEP [{etiqueta}]: {'OK' if ok else 'WARN'}")
        if not ok:
            print(
                f"[{cad_label}][WARN] {etiqueta}: FreeCAD no generó {cad_label} "
                f"(ver _logs/freecad_runner.log)"
            )

    def _fmt_for_dxf(path: str) -> str:
        nombre = os.path.basename(path)
        if nombre in fmt_map:
            return fmt_map[nombre]
        # sin_gap no entra al manifiesto pqart → no debe caer en STEP por defecto
        if es_cobre:
            return "dxf"
        return "step"

    universal = step_universal_sin_camas_activo()

    # NESTEOS DE COBRE (largos CU: madre + RTZCU) — siempre igual (no entra al overlay)
    if es_cobre and rutas.get("nesteos_cobre_dxf"):
        os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
        if not fmt_map:
            print(
                "[STEP][SKIP] NESTEOS DE COBRE: cobre solo DXF "
                "(todas las barras sin_gap / sin conversión 3D)"
            )
        else:
            _convertir(
                "NESTEOS DE COBRE STEP",
                "nesteos_cobre_dxf",
                "nesteos_cobre_step",
                "TR",
                0.0, 0.0, 0.0,
                material="CU",
                export_format="step",
                dxf_filter=lambda p: _cu_dxf_requiere_3d(_fmt_for_dxf(p)),
            )

    if universal:
        # Overlay: 1 STEP por DXF de acero, coords DXF 1:1 (origen NONE, sin A/B).
        print(
            "[STEP] Overlay STEP_UNIVERSAL_SIN_CAMAS activo: "
            "todas las carpetas DXF acero → 1 STEP sin desfase de camas"
        )
        acero_jobs = (
            ("CAMA LASER", "cama_laser_dxf", "cama_laser_step", "0.0"),
            ("CAMA LASER 12KW", "cama_laser_12kw_dxf", "cama_laser_12kw_step", "0.0"),
            ("ROBOT LASER", "robot_laser_dxf", "robot_laser_step", "0.0"),
            ("ROBOT PLASMA", "robot_plasma_dxf", "robot_plasma_step", str(plasma_off)),
        )
        for etiqueta, dxf_key, step_key, plasma_env in acero_jobs:
            if not rutas.get(dxf_key) and not rutas.get(step_key):
                continue
            os.environ["FREECAD_PLASMA_OFFSET"] = plasma_env
            _convertir(
                etiqueta,
                dxf_key,
                step_key,
                "NONE",
                0.0,
                0.0,
                0.0,
                prefer_verde=True,
                material="STEEL",
            )
    else:
        # Flujo normal: CAMA LASER solo DXF; robot A/B con ancla + offset.
        # CAMA LASER (acero): solo DXF — no genera STEP.

        # ROBOT LASER
        if rutas.get("robot_laser_dxf"):
            os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
            _convertir(
                "ROBOT LASER A",
                "robot_laser_dxf",
                "robot_laser_step_A",
                "TR",
                4235, -1015, -700,
                prefer_verde=True,
                material="STEEL",
            )
            _convertir(
                "ROBOT LASER B",
                "robot_laser_dxf",
                "robot_laser_step_B",
                "BR",
                4235, 840, -700,
                prefer_verde=True,
                material="STEEL",
            )

        # ROBOT PLASMA
        if rutas.get("robot_plasma_dxf"):
            os.environ["FREECAD_PLASMA_OFFSET"] = str(plasma_off)
            _convertir(
                "ROBOT PLASMA A",
                "robot_plasma_dxf",
                "robot_plasma_step_A",
                "TR",
                4235, -1015, -700,
                prefer_verde=True,
                material="STEEL",
            )
            _convertir(
                "ROBOT PLASMA B",
                "robot_plasma_dxf",
                "robot_plasma_step_B",
                "BR",
                4235, 840, -700,
                prefer_verde=True,
                material="STEEL",
            )

    ok_total = sum(1 for _, ok, _ in resultados if ok)
    if resultados:
        print(f"[STEP] Resumen: {ok_total}/{len(resultados)} conversiones exitosas")
    return resultados


def exportar_resultados_a_dxf(
    resultados: dict,
    out_dir: str,
    base_name: str = "NEST",
    generar_step: bool = False,
    wo_label: str | None = None,
    es_swo: bool = False,
    swo_id: str | None = None,
    datos_partes=None,
    motor_3d: str = "freecad",
    progress_cb=None,
):
    from .dxf_export_log import log, log_section, log_sheet_plan

    try:
        from modules.nest_exporter import export_nest_to_dxf, DxfExportValidationError
        from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf
    except ImportError:
        from nest_exporter import export_nest_to_dxf, DxfExportValidationError
        from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf

    import config

    nest_folder = "NESTING"
    job_root_dir = os.path.join(out_dir, nest_folder)
    es_swo_export = _es_export_swo(base_name, wo_label, es_swo=es_swo)
    swo_ref = str(swo_id or base_name or "").strip()
    motor = str(motor_3d or "freecad").strip().lower()
    if motor in ("arga", "nans", "arga nesting", "arga_nesting"):
        motor = "occt"
    if motor not in ("freecad", "occt"):
        motor = "freecad"

    n_dxf_est, n_step_est = estimar_conteos_export(resultados, generar_step=bool(generar_step))
    dxf_done = 0

    def _progress(**kwargs):
        if not callable(progress_cb):
            return
        payload = {
            "dxf_done": dxf_done,
            "dxf_total": n_dxf_est,
            "step_done": kwargs.get("step_done"),
            "step_total": n_step_est if generar_step else 0,
            "mensaje": kwargs.get("mensaje", ""),
        }
        if kwargs.get("step_total") is not None:
            payload["step_total"] = kwargs["step_total"]
        try:
            progress_cb(**{k: v for k, v in payload.items() if v is not None or k == "mensaje"})
        except Exception:
            pass

    log_section("INICIO EXPORTACION DXF")
    log(f"destino_raiz={out_dir}")
    log(f"carpeta_nesting={job_root_dir}")
    log(f"base_name={base_name} | wo_label={wo_label} | es_swo={es_swo_export} | swo_ref={swo_ref}")
    log(
        f"generar_step={generar_step} | motor_3d={motor} | "
        f"step_universal_sin_camas={step_universal_sin_camas_activo()} | "
        f"estimado_dxf={n_dxf_est} | estimado_step={n_step_est} | "
        f"grupos={len(resultados or {})}"
    )
    _progress(mensaje="Escribiendo DXF de producción…", step_done=0)

    rutas = {
        "nesteos_cobre_dxf": os.path.join(job_root_dir, RUTA_NESTEOS_COBRE, "DXF"),
        "nesteos_cobre_step": os.path.join(job_root_dir, RUTA_NESTEOS_COBRE, "STEP"),
        "cama_laser_dxf": os.path.join(job_root_dir, RUTA_CAMA_LASER, "DXF"),
        "cama_laser_12kw_dxf": os.path.join(job_root_dir, RUTA_CAMA_LASER_12KW, "DXF"),
        "robot_laser_dxf": os.path.join(job_root_dir, RUTA_ROBOT_LASER, "DXF"),
        "robot_plasma_dxf": os.path.join(job_root_dir, RUTA_ROBOT_PLASMA, "DXF"),
    }
    if step_universal_sin_camas_activo():
        # Overlay: STEP plano por familia (sin Cama A/B).
        rutas.update(
            {
                "cama_laser_step": os.path.join(job_root_dir, RUTA_CAMA_LASER, "STEP"),
                "cama_laser_12kw_step": os.path.join(
                    job_root_dir, RUTA_CAMA_LASER_12KW, "STEP"
                ),
                "robot_laser_step": os.path.join(job_root_dir, RUTA_ROBOT_LASER, "STEP"),
                "robot_plasma_step": os.path.join(job_root_dir, RUTA_ROBOT_PLASMA, "STEP"),
            }
        )
    else:
        rutas.update(
            {
                "robot_laser_step_A": os.path.join(
                    job_root_dir, RUTA_ROBOT_LASER, "STEP", "Cama A"
                ),
                "robot_laser_step_B": os.path.join(
                    job_root_dir, RUTA_ROBOT_LASER, "STEP", "Cama B"
                ),
                "robot_plasma_step_A": os.path.join(
                    job_root_dir, RUTA_ROBOT_PLASMA, "STEP", "Cama A"
                ),
                "robot_plasma_step_B": os.path.join(
                    job_root_dir, RUTA_ROBOT_PLASMA, "STEP", "Cama B"
                ),
            }
        )

    for r in rutas.values():
        os.makedirs(r, exist_ok=True)
    os.makedirs(os.path.join(job_root_dir, REPORTE_PDF_NESTING), exist_ok=True)
    os.makedirs(os.path.join(job_root_dir, ARCHIVO_ARGANEST_NESTING), exist_ok=True)

    exportados_principales = []
    thickness_para_step = getattr(config, "FREECAD_THK_MM", 6.35)
    plasma_offset_job = 0.0125 * 25.4

    from .sheet_numbering import (
        asignar_numeracion_global_hojas,
        iterar_grupos_nesting_ordenados,
        resolver_order_label_sheet_codes,
    )

    order_label_global = resolver_order_label_sheet_codes(
        es_swo=es_swo_export,
        swo_id=swo_ref,
        wo_label=wo_label,
    )
    asignar_numeracion_global_hojas(resultados, order_label_global, sobrescribir=True)
    from .cu_rtz_sin_gap import (
        asignar_rtz_cu_sin_gap_ids,
        es_overlay_rtz_cu,
        largo_export_madre_cu_mm,
        pieza_excluida_dxf_madre_cu,
    )

    asignar_rtz_cu_sin_gap_ids(resultados)

    from .efficiency_metrics import (
        inicializar_contador_rtz_sobrante,
        inicializar_contador_rtzc_sobrante,
    )

    contador_rtz_sobrante = inicializar_contador_rtz_sobrante(resultados)
    contador_rtzc_sobrante = inicializar_contador_rtzc_sobrante(resultados)

    for clave, data in iterar_grupos_nesting_ordenados(resultados):
        if "hojas" not in data:
            continue

        log_section(f"GRUPO {clave}")
        log(f"hojas_en_grupo={len(data.get('hojas') or [])}")

        thickness_str, espesor_pulgadas = _parse_thickness_from_clave(clave)
        thickness_name = thickness_str or str(clave).split("_", 1)[0].strip()

        if espesor_pulgadas is not None and espesor_pulgadas <= 1.0:
            thickness_para_step = espesor_pulgadas * 25.4

        # Conserva tu regla actual de compensación para plasma
        espesor_ref = espesor_pulgadas if espesor_pulgadas is not None else 0.25
        plasma_offset_job = (0.250 if espesor_ref > 0.75 else 0.0125) * 25.4

        # Mantiene la lógica visible actual por grupo de calibre
        contador_placas = {}

        for idx_hoja, hoja in enumerate(data.get("hojas", []), start=1):
            sheet_seq = int(hoja.get("sheet_seq") or idx_hoja)

            w_mm = hoja.get("placa_w", 2438.4)
            h_mm = hoja.get("placa_h", 1219.2)
            es_retazo = hoja.get("es_retazo", False)
            es_cu_rtz_virtual = bool(hoja.get("cu_rtz_virtual"))
            es_cu_hoja = bool(hoja.get("modo_largos_cu"))

            if es_swo_export and swo_ref.upper().startswith("SWO"):
                order_label = swo_ref
            else:
                order_label = str(wo_label or "").strip() or "W.O."

            display_name = _resolver_display_name_hoja(
                hoja,
                contador_placas,
                contador_rtz=contador_rtz_sobrante,
                contador_rtzc=contador_rtzc_sobrante,
                calibre=thickness_name,
                wo_name=order_label,
            )

            _inyectar_metadata_hoja(
                hoja,
                order_label=order_label,
                thickness_name=thickness_name,
                sheet_seq=sheet_seq,
                display_name=display_name,
                source_nest_name=swo_ref if es_swo_export else (str(base_name).strip() or "NEST"),
            )
            hoja["pqart_exports"] = []

            if bool(hoja.get("modo_largos_cu")):
                if hoja.get("cu_rtz_virtual"):
                    hoja["cu_modo_separacion_barra"] = "con_gap"
                    hoja["export_3d_format"] = "step"
                else:
                    hoja["export_3d_format"] = _hoja_cobre_export_3d(hoja)
                if hoja["export_3d_format"] == "dxf":
                    log(
                        f"hoja {hoja.get('sheet_code') or sheet_seq}: cobre sin separación "
                        f"(sin_gap) → solo DXF (BAR_START; sin Plate, CUT_CU ni 3D)"
                    )
                elif hoja.get("cu_rtz_virtual"):
                    log(
                        f"hoja {hoja.get('sheet_code') or sheet_seq}: RTZCU → "
                        f"con_gap / STEP (horizontal, CUT_OUTER cerrado; sin vertical CyPTube)"
                    )

            sheet_info = {
                "length": float(w_mm),
                "width": float(h_mm),
                "material": clave.split("_", 1)[1] if "_" in clave else clave,
                "thickness": thickness_name or "",
                "arga_code": hoja["sheet_display_name"],
                "modo_largos_cu": bool(hoja.get("modo_largos_cu")),
                "export_3d_format": str(hoja.get("export_3d_format") or "step"),
                "cu_modo_separacion_barra": str(
                    hoja.get("cu_modo_separacion_barra") or ""
                ),
                "cu_rtz_virtual": bool(hoja.get("cu_rtz_virtual")),
                "cu_rtz_activo": bool(hoja.get("cu_rtz_activo")),
                "cu_rtz_inicio_mm": float(hoja.get("cu_rtz_inicio_mm") or 0.0),
            }
            # Madre sin_gap + RTZCU: el DXF de la madre solo cubre hasta el inicio RTZ.
            if es_cu_hoja and not es_cu_rtz_virtual:
                largo_madre = largo_export_madre_cu_mm(hoja)
                if largo_madre is not None and largo_madre > 0.5:
                    sheet_info["length"] = float(largo_madre)
                    sheet_info["cu_rtz_inicio_mm"] = float(largo_madre)
                    sheet_info["cu_rtz_activo"] = True
                    w_mm = float(largo_madre)
            _cu_bar_w = float(h_mm)
            _cu_bar_l = float(w_mm)

            carpeta_principal = _resolver_carpeta_principal(clave, hoja)
            generar_plasma_hoja = _debe_generar_plasma(clave, hoja)
            compensada_manual = bool(hoja.get("plasma_compensado_manual", False))
            off_manual = float(hoja.get("plasma_offset_mm_manual", plasma_offset_job) or plasma_offset_job)

            if datos_partes:
                from modules.nesting_engine.manager import enriquecer_hoja_export_desde_partes

                n_rutas = enriquecer_hoja_export_desde_partes(hoja, clave, datos_partes)
                if n_rutas:
                    log(f"enriquecimiento PARTS: {n_rutas} ruta(s) DXF completada(s)")

            try:
                from modules.nesting_engine.display_geometry import completar_transform_export_hoja

                n_tf = completar_transform_export_hoja(hoja)
                if n_tf:
                    log(f"transformaciones DXF inferidas: {n_tf} pieza(s)")
            except Exception:
                pass

            placements_principales = []
            placements_plasma = []

            if es_retazo and "poly_borde_retazo" in hoja and not es_cu_hoja:
                placements_principales.append({
                    "part_name": f"BORDE_RETAZO_{hoja['placa_id']}",
                    "outer": hoja["poly_borde_retazo"],
                    "holes": [],
                    "marks": [],
                    "ruta": "",
                    "layer_override": "Plate",
                    "orig_minx": 0.0,
                    "orig_miny": 0.0,
                    "shift_x": 0.0,
                    "shift_y": 0.0,
                    "rot_deg": 0.0,
                })

            for pz in hoja.get("piezas", []):
                nom = pz.get("nombre", "PART")

                if nom.startswith("REF__"):
                    continue

                if es_overlay_rtz_cu(nom):
                    continue

                # Madre CU: nada de zona RTZCU (piezas, overlays ni CU_CORTE del retazo).
                if es_cu_hoja and not es_cu_rtz_virtual and pieza_excluida_dxf_madre_cu(pz, hoja):
                    continue

                if nom.startswith("TATUAJE_"):
                    if es_cu_hoja:
                        continue
                    placements_principales.append({
                        "part_name": nom,
                        "outer": [],
                        "holes": [],
                        "marks": pz.get("marcas", []),
                        "marks_layer": "RTZ_LABEL",
                        "ruta": "",
                        "orig_minx": 0.0,
                        "orig_miny": 0.0,
                        "shift_x": 0.0,
                        "shift_y": 0.0,
                        "rot_deg": 0.0,
                    })
                    continue

                pols = pz.get("poligonos", []) or []
                if not pols:
                    continue

                es_linea_corte = (
                    nom.startswith("RETAZO_GUILLOTINA")
                    or nom.startswith("CU_CORTE__")
                )
                if es_cu_hoja and es_linea_corte:
                    if es_overlay_rtz_cu(nom):
                        continue
                    if str(hoja.get("export_3d_format") or "").strip().lower() == "step":
                        continue

                # DXF principal (sin compensación)
                outer_main, holes_main = _clean_profile_for_production(
                    pols[0],
                    pols[1:] if len(pols) > 1 else [],
                )
                compensada_pieza = bool(
                    pz.get("plasma_compensada_manual") or compensada_manual
                )
                ruta_src = str(pz.get("ruta") or "").strip()
                # Láser: siempre 1:1 desde Processed; la compensación plasma no altera este canal.
                use_source_dxf = (
                    bool(ruta_src)
                    and os.path.isfile(ruta_src)
                    and not es_linea_corte
                )

                layer_override = pz.get("layer_override")
                closed_flag = pz.get("closed")
                cu_largos_piece = False
                if es_cu_hoja and not layer_override:
                    if es_linea_corte:
                        layer_override = "CUT_OUTER"
                        closed_flag = False
                    else:
                        cu_largos_piece = True
                        closed_flag = True

                placement = {
                    "part_name": nom,
                    "outer": outer_main,
                    "holes": holes_main,
                    "marks": pz.get("marcas", []),
                    "ruta": ruta_src if use_source_dxf else "",
                    "prefer_source_dxf": use_source_dxf,
                    "compensated": False,
                    "cu_largos_piece": cu_largos_piece,
                    "cu_slice_idx": int(pz.get("cu_slice_idx", 0) or 0),
                    "cu_slice_count": int(pz.get("cu_slice_count", 1) or 1),
                    "cu_bar_w_mm": _cu_bar_w if es_cu_hoja else 0.0,
                    "cu_bar_l_mm": _cu_bar_l if es_cu_hoja else 0.0,
                    "cu_sin_separacion": bool(pz.get("cu_sin_separacion")),
                    "omit_cut_cu": True,
                    "largo_cu_in": float(pz.get("largo_cu_in") or 0.0),
                    "orig_minx": pz.get("orig_minx", 0.0),
                    "orig_miny": pz.get("orig_miny", 0.0),
                    "shift_x": pz.get("shift_x", 0.0),
                    "shift_y": pz.get("shift_y", 0.0),
                    "rot_deg": pz.get("rot_deg", 0.0),
                    "rot_origin_cx": pz.get("rot_origin_cx", 0.0),
                    "rot_origin_cy": pz.get("rot_origin_cy", 0.0),
                }
                if layer_override:
                    placement["layer_override"] = str(layer_override)
                if closed_flag is not None:
                    placement["closed"] = bool(closed_flag)
                placements_principales.append(placement)

                # DXF plasma (solo cuando aplique; cobre no usa plasma).
                # Desfase geométrico SOLO si la hoja/pieza se compensó manualmente.
                # Sin compensación: mismo nest 1:1 (no empujar corte fuera de placa).
                if generar_plasma_hoja and not nom.startswith("RETAZO_GUILLOTINA") and not nom.startswith("CU_CORTE__") and not es_cu_hoja:
                    from modules.plasma_dxf_export import sanitize_plasma_profile

                    off_piece = float(
                        pz.get("plasma_offset_mm_manual")
                        or hoja.get("plasma_offset_mm_manual")
                        or off_manual
                        or plasma_offset_job
                        or 0.0
                    )
                    off_export = off_piece if compensada_pieza else 0.0
                    if compensada_pieza:
                        plasma_outer, plasma_holes = sanitize_plasma_profile(
                            pols[0],
                            pols[1:] if len(pols) > 1 else [],
                        )
                    else:
                        plasma_outer, plasma_holes = outer_main, holes_main

                    ruta_src = str(pz.get("ruta") or "").strip()
                    plasma_ruta = (
                        ruta_src
                        if ruta_src and os.path.isfile(ruta_src) and not es_linea_corte
                        else ""
                    )

                    placements_plasma.append({
                        "part_name": nom + "_PLASMA",
                        "outer": plasma_outer,
                        "holes": plasma_holes,
                        "nested_poligonos": [list(pols[0])] + [list(h) for h in (pols[1:] if len(pols) > 1 else [])],
                        "marks": pz.get("marcas", []),
                        "ruta": plasma_ruta,
                        "prefer_source_dxf": False,
                        "compensated": compensada_pieza,
                        "plasma_export": True,
                        "compensated_plasma_source": bool(plasma_ruta) and off_export > 0,
                        "plasma_offset_mm": off_export,
                        "use_native_curves": True,
                        "orig_minx": pz.get("orig_minx", 0.0),
                        "orig_miny": pz.get("orig_miny", 0.0),
                        "shift_x": pz.get("shift_x", 0.0),
                        "shift_y": pz.get("shift_y", 0.0),
                        "rot_deg": pz.get("rot_deg", 0.0),
                        "rot_origin_cx": pz.get("rot_origin_cx", 0.0),
                        "rot_origin_cy": pz.get("rot_origin_cy", 0.0),
                    })

            if es_cu_rtz_virtual:
                # RTZCU{n}-H{m}: nunca heredar un W.O.-H* stale del numerador global.
                sheet_code = str(
                    hoja.get("placa_id")
                    or hoja.get("cu_rtz_id")
                    or hoja.get("sheet_code")
                    or f"{order_label}-H{sheet_seq}"
                ).strip()
            else:
                sheet_code = hoja.get("sheet_code") or f"{order_label}-H{sheet_seq}"
            nest_tag = swo_ref if es_swo_export else str(base_name).strip() or "NEST"
            nombre_archivo = f"{nest_tag}_{thickness_name}_{sheet_code}.dxf"

            from .efficiency_metrics import hoja_export_solo_plasma

            solo_plasma = hoja_export_solo_plasma(hoja)

            log_sheet_plan(
                clave=clave,
                hoja=hoja,
                sheet_info=sheet_info,
                nombre_archivo=nombre_archivo,
                carpeta_principal=carpeta_principal,
                generar_plasma=generar_plasma_hoja,
                solo_plasma=solo_plasma,
                compensada_manual=compensada_manual,
                off_manual=off_manual,
                n_principal=len(placements_principales),
                n_plasma=len(placements_plasma),
            )

            # Exportación principal (omitida en RTZC: solo Robot Plasma)
            if not solo_plasma:
                if carpeta_principal == RUTA_NESTEOS_COBRE:
                    path_principal = os.path.join(rutas["nesteos_cobre_dxf"], nombre_archivo)
                elif carpeta_principal == RUTA_CAMA_LASER:
                    path_principal = os.path.join(rutas["cama_laser_dxf"], nombre_archivo)
                elif carpeta_principal == RUTA_CAMA_LASER_12KW:
                    path_principal = os.path.join(rutas["cama_laser_12kw_dxf"], nombre_archivo)
                else:
                    path_principal = os.path.join(rutas["robot_laser_dxf"], nombre_archivo)

                try:
                    log(f"-> EXPORT PRINCIPAL [{carpeta_principal}]: {path_principal}")
                    if es_cu_hoja:
                        export_cobre_hoja_to_dxf(
                            path_principal,
                            sheet_info,
                            placements_principales,
                            title=f"{carpeta_principal} | {clave}",
                            strict=not es_cu_rtz_virtual,
                        )
                    else:
                        export_nest_to_dxf(
                            path_principal,
                            sheet_info,
                            placements_principales,
                            title=f"{carpeta_principal} | {clave}",
                            canal=carpeta_principal,
                            modo_largos_cu=False,
                            strict=True,
                        )
                except DxfExportValidationError as exc:
                    raise DxfExportValidationError(
                        f"Exportación abortada ({nombre_archivo}): {exc}"
                    ) from exc
                exportados_principales.append(path_principal)
                dxf_done += 1
                _progress(
                    mensaje=f"DXF {dxf_done}/{n_dxf_est}: {nombre_archivo}",
                    step_done=0,
                )

                _registrar_exportacion_pqart_hoja(
                    hoja,
                    ruta_dxf=path_principal,
                    tipo_corte=_normalizar_tipo_corte_pqart(carpeta_principal),
                )

            # Exportación plasma solo cuando aplique
            lista_plasma = placements_plasma
            if solo_plasma and not lista_plasma:
                # RTZC: nunca reutilizar principales (outer láser limpiado agresivamente)
                from modules.plasma_dxf_export import sanitize_plasma_profile

                lista_plasma = []
                comp_hoja = bool(hoja.get("plasma_compensado_manual"))
                off_hoja = float(hoja.get("plasma_offset_mm_manual") or plasma_offset_job or 0.0)
                for pz in hoja.get("piezas", []):
                    nom = str(pz.get("nombre", "") or "")
                    if nom.startswith("REF__") or nom.startswith("TATUAJE_"):
                        continue
                    if nom.startswith("RTZCU_ZONA__") or es_overlay_rtz_cu(nom):
                        continue
                    if nom.startswith("RETAZO_GUILLOTINA") or nom.startswith("CU_CORTE__"):
                        continue
                    pols = pz.get("poligonos", []) or []
                    if not pols:
                        continue
                    comp_pz = comp_hoja or bool(pz.get("plasma_compensada_manual"))
                    off_export = off_hoja if comp_pz else 0.0
                    if comp_pz:
                        po, ph = sanitize_plasma_profile(
                            pols[0],
                            pols[1:] if len(pols) > 1 else [],
                        )
                    else:
                        po, ph = _clean_profile_for_production(
                            pols[0],
                            pols[1:] if len(pols) > 1 else [],
                        )
                    ruta_rtzc = str(pz.get("ruta") or "").strip()
                    plasma_ruta_rtzc = (
                        ruta_rtzc
                        if ruta_rtzc and os.path.isfile(ruta_rtzc)
                        else ""
                    )
                    lista_plasma.append({
                        "part_name": nom + "_PLASMA",
                        "outer": po,
                        "holes": ph,
                        "nested_poligonos": [list(pols[0])] + [list(h) for h in (pols[1:] if len(pols) > 1 else [])],
                        "marks": pz.get("marcas", []),
                        "ruta": plasma_ruta_rtzc,
                        "compensated": comp_pz,
                        "plasma_export": True,
                        "compensated_plasma_source": bool(plasma_ruta_rtzc) and off_export > 0,
                        "plasma_offset_mm": off_export,
                        "use_native_curves": True,
                        "orig_minx": pz.get("orig_minx", 0.0),
                        "orig_miny": pz.get("orig_miny", 0.0),
                        "shift_x": pz.get("shift_x", 0.0),
                        "shift_y": pz.get("shift_y", 0.0),
                        "rot_deg": pz.get("rot_deg", 0.0),
                        "rot_origin_cx": pz.get("rot_origin_cx", 0.0),
                        "rot_origin_cy": pz.get("rot_origin_cy", 0.0),
                    })

            if generar_plasma_hoja and lista_plasma:
                path_plasma = os.path.join(rutas["robot_plasma_dxf"], nombre_archivo)
                log(f"-> EXPORT PLASMA [{RUTA_ROBOT_PLASMA}]: {path_plasma}")
                export_nest_to_dxf(
                    path_plasma,
                    sheet_info,
                    lista_plasma,
                    title=f"{RUTA_ROBOT_PLASMA} | {clave}",
                    canal=RUTA_ROBOT_PLASMA,
                    strict=True,
                )

                _registrar_exportacion_pqart_hoja(
                    hoja,
                    ruta_dxf=path_plasma,
                    tipo_corte=_normalizar_tipo_corte_pqart(RUTA_ROBOT_PLASMA),
                )
                dxf_done += 1
                _progress(
                    mensaje=f"DXF {dxf_done}/{n_dxf_est}: {nombre_archivo} (plasma)",
                    step_done=0,
                )
                if solo_plasma:
                    exportados_principales.append(path_plasma)

    if generar_step:
        motor_label = (
            "Arga Nesting Suite (OCCT)" if motor == "occt" else "FreeCAD"
        )
        log(f"Iniciando conversión 3D ({motor_label})...")
        import time
        time.sleep(0.35)
        cu_formato_por_dxf = _build_cu_formato_por_dxf(resultados)
        _progress(
            mensaje=f"Convirtiendo a STEP con {motor_label}…",
            step_done=0,
        )
        if motor == "occt":
            # Arga Nesting Suite: reutilizar FreeCAD/generador_verde si está
            # instalado (mismo resultado de producción). OCCT solo de respaldo.
            from freecad_runner import freecad_listo_para_step

            if freecad_listo_para_step(prefer_verde=True):
                log(
                    "Arga Nesting Suite: usando FreeCAD + generador_verde "
                    "(motor de producción; sin reimplementar STEP)"
                )
                _progress(
                    mensaje="Arga Nesting Suite → FreeCAD (generador_verde)…",
                    step_done=0,
                )
                lanzar_freecad_robotica(
                    rutas,
                    thickness_para_step,
                    plasma_offset_job,
                    job_root_dir=job_root_dir,
                    es_cobre=_job_tiene_cobre(resultados),
                    cu_formato_por_dxf=cu_formato_por_dxf,
                    progress_cb=_progress,
                )
                _generar_steps_cobre_fuentes_in_place(
                    resultados, job_root_dir, log_fn=log
                )
                _progress(
                    mensaje="Validando STEP (FreeCAD vía Arga)…",
                    step_done=n_step_est,
                )
                # Auditoría como FreeCAD (mismo motor real)
                motor = "freecad"
            else:
                log(
                    "Arga Nesting Suite: FreeCAD no disponible → "
                    "fallback OCCT embebido"
                )
                from .occt_step_export import (
                    generar_steps_cobre_fuentes_occt,
                    lanzar_occt_robotica,
                )

                lanzar_occt_robotica(
                    rutas,
                    thickness_para_step,
                    plasma_offset_job,
                    job_root_dir,
                    es_cobre=_job_tiene_cobre(resultados),
                    cu_formato_por_dxf=cu_formato_por_dxf,
                    progress_cb=_progress,
                )
                generar_steps_cobre_fuentes_occt(
                    resultados,
                    job_root_dir,
                    log_fn=log,
                    progress_cb=_progress,
                )
        else:
            lanzar_freecad_robotica(
                rutas,
                thickness_para_step,
                plasma_offset_job,
                job_root_dir=job_root_dir,
                es_cobre=_job_tiene_cobre(resultados),
                cu_formato_por_dxf=cu_formato_por_dxf,
                progress_cb=_progress,
            )
            _generar_steps_cobre_fuentes_in_place(resultados, job_root_dir, log_fn=log)
            _progress(
                mensaje="Validando STEP (FreeCAD)…",
                step_done=n_step_est,
            )
        _ULTIMO_RESUMEN_STEP.clear()
        _ULTIMO_RESUMEN_STEP.update(
            _validar_steps_tras_export(
                rutas,
                log_fn=log,
                cu_formato_por_dxf=cu_formato_por_dxf,
                motor_3d=motor,
            )
        )
        _progress(
            mensaje=f"Exportación 3D completa ({motor_label})",
            step_done=n_step_est,
        )
        # STEP solo en NESTING/<familia>/STEP (sin copia a 3D NESTING).

    log_section(f"EXPORTACION DXF COMPLETA - {len(exportados_principales)} archivo(s)")
    for pth in exportados_principales:
        log(f"  * {pth}")
    return exportados_principales
    return exportados_principales
