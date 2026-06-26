import glob
import os
import re
import shutil
from shapely.geometry import Polygon, MultiPolygon


RUTA_CAMA_LASER = "CAMA LASER SIN MINI NEST"
RUTA_CAMA_LASER_12KW = "CAMA LASER 12 KW SIN MINI NEST"
RUTA_ROBOT_LASER = "ROBOT LASER + MINI NEST"
RUTA_ROBOT_PLASMA = "ROBOT PLASMA"
REPORTE_PDF_NESTING = "REPORTE DE NESTEO PDF"


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


def _publicar_steps_en_3d_nesting(out_dir: str, rutas: dict) -> int:
    """Copia STEP generados a ARGA MODEL CORE/3D NESTING (ruta visible en SWO)."""
    dest = os.path.join(out_dir, "3D NESTING")
    os.makedirs(dest, exist_ok=True)
    copiados = 0
    step_dirs = [
        rutas.get("cama_laser_step"),
        rutas.get("cama_laser_12kw_step"),
        rutas.get("robot_laser_step_A"),
        rutas.get("robot_laser_step_B"),
        rutas.get("robot_plasma_step_A"),
        rutas.get("robot_plasma_step_B"),
    ]
    for step_dir in step_dirs:
        if not step_dir or not os.path.isdir(step_dir):
            continue
        for step_path in glob.glob(os.path.join(step_dir, "*.step")):
            dest_path = os.path.join(dest, os.path.basename(step_path))
            try:
                shutil.copy2(step_path, dest_path)
                copiados += 1
            except Exception as exc:
                print(f"[STEP][WARN] No se pudo copiar {step_path} -> {dest_path}: {exc}")
    return copiados


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
        return RUTA_CAMA_LASER

    partes = str(clave or "").split("_", 1)
    if len(partes) > 1 and str(partes[1]).strip().upper() == "CU":
        return RUTA_CAMA_LASER

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
    if bool(hoja.get("plasma_compensado_manual", False)):
        return True
    _, thk_val = _parse_thickness_from_clave(clave)

    from .efficiency_metrics import hoja_es_sobrante_sin_compra

    # Accesorios / mini nests / sobrante láser (RTZ) sin compensar
    if hoja.get("es_retazo", False):
        return False
    if hoja_es_sobrante_sin_compra(hoja) and not bool(
        hoja.get("plasma_compensado_manual")
    ):
        return False

    # Plasma solo para > 3/8
    if thk_val is None or thk_val <= 0.375:
        return False

    # Y solo si al menos un componente es >= 80 x 140
    return _has_big_component(hoja)

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
    }

    ruta_cmp = os.path.normcase(ruta_normalizada)

    for item in exports:
        ruta_item = os.path.normcase(os.path.normpath(str(item.get("ruta") or "").strip()))
        if ruta_item == ruta_cmp:
            item.update(payload)
            return

    exports.append(payload)

def lanzar_freecad_robotica(rutas, thk, plasma_off, job_root_dir: str | None = None):
    from freecad_runner import ejecutar_macro_freecad

    job_root = os.path.normpath(str(job_root_dir or "").strip())
    resultados = []

    def _convertir(etiqueta, dxf_key, step_key, origen, ox, oy, oz, *, prefer_verde=False):
        dxf_dir = _localizar_carpeta_dxf(
            rutas.get(dxf_key, ""),
            job_root,
            {
                "cama_laser_dxf": RUTA_CAMA_LASER,
                "cama_laser_12kw_dxf": RUTA_CAMA_LASER_12KW,
                "robot_laser_dxf": RUTA_ROBOT_LASER,
                "robot_plasma_dxf": RUTA_ROBOT_PLASMA,
            }.get(dxf_key, ""),
        )
        step_dir = os.path.normpath(str(rutas.get(step_key, "") or "").strip())
        if not _hay_dxfs(dxf_dir):
            print(f"[STEP][SKIP] {etiqueta}: sin DXF en {dxf_dir}")
            resultados.append((etiqueta, False, "sin DXF"))
            return

        os.makedirs(step_dir, exist_ok=True)
        n_dxfs = len(_listar_dxfs_en_carpeta(dxf_dir))
        print(f"[STEP] {etiqueta}: {n_dxfs} DXF -> {step_dir}")
        ok = ejecutar_macro_freecad(
            dxf_dir,
            step_dir,
            thk,
            origen,
            ox, oy, oz,
            prefer_verde=prefer_verde,
            max_intentos=2,
        )
        resultados.append((etiqueta, ok, dxf_dir))
        if not ok:
            print(f"[STEP][WARN] {etiqueta}: FreeCAD no generó STEP (ver _logs/freecad_runner.log)")

    # CAMA LASER (incluye largos de cobre CU)
    if rutas.get("cama_laser_dxf"):
        os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
        _convertir("CAMA LASER", "cama_laser_dxf", "cama_laser_step", "TR", 0.0, 0.0, 0.0)

    if rutas.get("cama_laser_12kw_dxf"):
        os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
        _convertir(
            "CAMA LASER 12KW",
            "cama_laser_12kw_dxf",
            "cama_laser_12kw_step",
            "TR",
            0.0, 0.0, 0.0,
        )

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
        )
        _convertir(
            "ROBOT LASER B",
            "robot_laser_dxf",
            "robot_laser_step_B",
            "BR",
            4235, 840, -700,
            prefer_verde=True,
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
        )
        _convertir(
            "ROBOT PLASMA B",
            "robot_plasma_dxf",
            "robot_plasma_step_B",
            "BR",
            4235, 840, -700,
            prefer_verde=True,
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
):
    from .dxf_export_log import log, log_section, log_sheet_plan

    try:
        from modules.nest_exporter import export_nest_to_dxf, DxfExportValidationError
    except ImportError:
        from nest_exporter import export_nest_to_dxf, DxfExportValidationError

    import config

    nest_folder = "NESTING"
    job_root_dir = os.path.join(out_dir, nest_folder)
    es_swo_export = _es_export_swo(base_name, wo_label, es_swo=es_swo)
    swo_ref = str(swo_id or base_name or "").strip()

    log_section("INICIO EXPORTACION DXF")
    log(f"destino_raiz={out_dir}")
    log(f"carpeta_nesting={job_root_dir}")
    log(f"base_name={base_name} | wo_label={wo_label} | es_swo={es_swo_export} | swo_ref={swo_ref}")
    log(f"generar_step={generar_step} | grupos={len(resultados or {})}")

    rutas = {
        "cama_laser_dxf": os.path.join(job_root_dir, RUTA_CAMA_LASER, "DXF"),
        "cama_laser_12kw_dxf": os.path.join(job_root_dir, RUTA_CAMA_LASER_12KW, "DXF"),
        "robot_laser_dxf": os.path.join(job_root_dir, RUTA_ROBOT_LASER, "DXF"),
        "robot_plasma_dxf": os.path.join(job_root_dir, RUTA_ROBOT_PLASMA, "DXF"),

        "robot_laser_step_A": os.path.join(job_root_dir, RUTA_ROBOT_LASER, "STEP", "Cama A"),
        "robot_laser_step_B": os.path.join(job_root_dir, RUTA_ROBOT_LASER, "STEP", "Cama B"),
        "robot_plasma_step_A": os.path.join(job_root_dir, RUTA_ROBOT_PLASMA, "STEP", "Cama A"),
        "robot_plasma_step_B": os.path.join(job_root_dir, RUTA_ROBOT_PLASMA, "STEP", "Cama B"),
        "cama_laser_step": os.path.join(job_root_dir, RUTA_CAMA_LASER, "STEP"),
        "cama_laser_12kw_step": os.path.join(job_root_dir, RUTA_CAMA_LASER_12KW, "STEP"),
    }

    for r in rutas.values():
        os.makedirs(r, exist_ok=True)
    os.makedirs(os.path.join(job_root_dir, REPORTE_PDF_NESTING), exist_ok=True)

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

            sheet_info = {
                "length": float(w_mm),
                "width": float(h_mm),
                "material": clave.split("_", 1)[1] if "_" in clave else clave,
                "thickness": thickness_name or "",
                "arga_code": hoja["sheet_display_name"],
                "modo_largos_cu": bool(hoja.get("modo_largos_cu")),
            }
            _cu_bar_w = float(h_mm)
            _cu_bar_l = float(w_mm)

            carpeta_principal = _resolver_carpeta_principal(clave, hoja)
            generar_plasma_hoja = _debe_generar_plasma(clave, hoja)
            compensada_manual = bool(hoja.get("plasma_compensado_manual", False))
            off_manual = float(hoja.get("plasma_offset_mm_manual", plasma_offset_job) or plasma_offset_job)

            if datos_partes and generar_plasma_hoja:
                from modules.nesting_engine.manager import enriquecer_hoja_export_desde_partes

                n_rutas = enriquecer_hoja_export_desde_partes(hoja, clave, datos_partes)
                if n_rutas:
                    log(f"enriquecimiento PARTS: {n_rutas} ruta(s) DXF completada(s)")

            placements_principales = []
            placements_plasma = []

            if es_retazo and "poly_borde_retazo" in hoja:
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

                es_cu_hoja = bool(hoja.get("modo_largos_cu"))
                es_linea_corte = nom.startswith("RETAZO_GUILLOTINA") or nom.startswith("CU_CORTE__")

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

                # DXF plasma (solo cuando aplique; cobre no usa plasma)
                if generar_plasma_hoja and not nom.startswith("RETAZO_GUILLOTINA") and not nom.startswith("CU_CORTE__") and not es_cu_hoja:
                    from modules.plasma_dxf_export import (
                        build_plasma_profile_from_nested,
                        sanitize_plasma_profile,
                    )

                    off_piece = float(
                        pz.get("plasma_offset_mm_manual")
                        or hoja.get("plasma_offset_mm_manual")
                        or off_manual
                        or plasma_offset_job
                        or 0.0
                    )
                    if compensada_pieza:
                        plasma_outer, plasma_holes = sanitize_plasma_profile(
                            pols[0],
                            pols[1:] if len(pols) > 1 else [],
                        )
                    else:
                        try:
                            plasma_outer, plasma_holes = build_plasma_profile_from_nested(
                                pols,
                                offset_mm=off_piece,
                                already_compensated=False,
                            )
                        except Exception:
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
                        "compensated_plasma_source": bool(plasma_ruta),
                        "plasma_offset_mm": off_piece,
                        "use_native_curves": True,
                        "orig_minx": pz.get("orig_minx", 0.0),
                        "orig_miny": pz.get("orig_miny", 0.0),
                        "shift_x": pz.get("shift_x", 0.0),
                        "shift_y": pz.get("shift_y", 0.0),
                        "rot_deg": pz.get("rot_deg", 0.0),
                        "rot_origin_cx": pz.get("rot_origin_cx", 0.0),
                        "rot_origin_cy": pz.get("rot_origin_cy", 0.0),
                    })

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
                if carpeta_principal == RUTA_CAMA_LASER:
                    path_principal = os.path.join(rutas["cama_laser_dxf"], nombre_archivo)
                elif carpeta_principal == RUTA_CAMA_LASER_12KW:
                    path_principal = os.path.join(rutas["cama_laser_12kw_dxf"], nombre_archivo)
                else:
                    path_principal = os.path.join(rutas["robot_laser_dxf"], nombre_archivo)

                try:
                    log(f"-> EXPORT PRINCIPAL [{carpeta_principal}]: {path_principal}")
                    export_nest_to_dxf(
                        path_principal,
                        sheet_info,
                        placements_principales,
                        title=f"{carpeta_principal} | {clave}",
                        canal=carpeta_principal,
                        modo_largos_cu=bool(hoja.get("modo_largos_cu")),
                        strict=True,
                    )
                except DxfExportValidationError as exc:
                    raise DxfExportValidationError(
                        f"Exportación abortada ({nombre_archivo}): {exc}"
                    ) from exc
                exportados_principales.append(path_principal)

                _registrar_exportacion_pqart_hoja(
                    hoja,
                    ruta_dxf=path_principal,
                    tipo_corte=_normalizar_tipo_corte_pqart(carpeta_principal),
                )

            # Exportación plasma solo cuando aplique
            lista_plasma = placements_plasma
            if solo_plasma and not lista_plasma:
                # RTZC: nunca reutilizar principales (outer láser limpiado agresivamente)
                from modules.plasma_dxf_export import (
                    build_plasma_profile_from_nested,
                    sanitize_plasma_profile,
                )

                lista_plasma = []
                comp_hoja = bool(hoja.get("plasma_compensado_manual"))
                off_hoja = float(hoja.get("plasma_offset_mm_manual") or plasma_offset_job or 0.0)
                for pz in hoja.get("piezas", []):
                    nom = str(pz.get("nombre", "") or "")
                    if nom.startswith("REF__") or nom.startswith("TATUAJE_"):
                        continue
                    if nom.startswith("RETAZO_GUILLOTINA") or nom.startswith("CU_CORTE__"):
                        continue
                    pols = pz.get("poligonos", []) or []
                    if not pols:
                        continue
                    comp_pz = comp_hoja or bool(pz.get("plasma_compensada_manual"))
                    if comp_pz:
                        po, ph = sanitize_plasma_profile(
                            pols[0],
                            pols[1:] if len(pols) > 1 else [],
                        )
                    else:
                        po, ph = build_plasma_profile_from_nested(
                            pols,
                            offset_mm=off_hoja,
                            already_compensated=False,
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
                        "compensated_plasma_source": bool(plasma_ruta_rtzc),
                        "plasma_offset_mm": off_hoja,
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
                if solo_plasma:
                    exportados_principales.append(path_plasma)

    if generar_step:
        log("Iniciando conversión STEP (FreeCAD)...")
        import time
        time.sleep(0.35)
        lanzar_freecad_robotica(rutas, thickness_para_step, plasma_offset_job, job_root_dir=job_root_dir)
        n_step = _publicar_steps_en_3d_nesting(out_dir, rutas)
        if n_step:
            etiqueta = "SWO" if es_swo_export else "WO"
            log(f"STEP publicados en 3D NESTING: {n_step} archivos ({etiqueta})")
            print(f"[STEP][{etiqueta}] Publicados {n_step} STEP en 3D NESTING", flush=True)

    if es_swo_export and swo_ref.upper().startswith("SWO"):
        from .api_client import enviar_reporte_a_api
        enviar_reporte_a_api(swo_ref, resultados)

    log_section(f"EXPORTACION DXF COMPLETA - {len(exportados_principales)} archivo(s)")
    for pth in exportados_principales:
        log(f"  * {pth}")
    return exportados_principales