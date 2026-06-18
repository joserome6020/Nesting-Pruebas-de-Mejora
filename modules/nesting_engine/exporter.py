import glob
import os
import re
import shutil
from shapely.geometry import Polygon, MultiPolygon


RUTA_CAMA_LASER = "CAMA LASER SIN MINI NEST"
RUTA_CAMA_LASER_12KW = "CAMA LASER 12 KW SIN MINI NEST"
RUTA_ROBOT_LASER = "ROBOT LASER + MINI NEST"
RUTA_ROBOT_PLASMA = "ROBOT PLASMA"


def _hay_dxfs(ruta):
    return os.path.isdir(ruta) and any(
        nombre.lower().endswith(".dxf") for nombre in os.listdir(ruta)
    )


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

    # Accesorios / mini nests sin compensar
    if hoja.get("es_retazo", False):
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


def _resolver_display_name_hoja(hoja, contador_placas):
    display_forzado = str(hoja.get("sheet_display_name") or "").strip()
    if display_forzado:
        return display_forzado

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
    hoja["is_rtz"] = bool(hoja.get("is_rtz", str(display_name).upper().startswith("RTZ")))

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

def lanzar_freecad_robotica(rutas, thk, plasma_off):
    from freecad_runner import ejecutar_macro_freecad

    # CAMA LASER (incluye largos de cobre CU)
    if _hay_dxfs(rutas.get("cama_laser_dxf", "")):
        os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
        os.makedirs(rutas.get("cama_laser_step", ""), exist_ok=True)
        ejecutar_macro_freecad(
            rutas["cama_laser_dxf"],
            rutas["cama_laser_step"],
            thk,
            "TR",
            0.0, 0.0, 0.0,
        )

    if _hay_dxfs(rutas.get("cama_laser_12kw_dxf", "")):
        os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
        os.makedirs(rutas.get("cama_laser_12kw_step", ""), exist_ok=True)
        ejecutar_macro_freecad(
            rutas["cama_laser_12kw_dxf"],
            rutas["cama_laser_12kw_step"],
            thk,
            "TR",
            0.0, 0.0, 0.0,
        )

    # ROBOT LASER
    if _hay_dxfs(rutas["robot_laser_dxf"]):
        os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
        ejecutar_macro_freecad(
            rutas["robot_laser_dxf"],
            rutas["robot_laser_step_A"],
            thk,
            "TR",
            4235, -1015, -700
        )
        ejecutar_macro_freecad(
            rutas["robot_laser_dxf"],
            rutas["robot_laser_step_B"],
            thk,
            "BR",
            4235, 840, -700
        )

    # ROBOT PLASMA
    if _hay_dxfs(rutas["robot_plasma_dxf"]):
        os.environ["FREECAD_PLASMA_OFFSET"] = str(plasma_off)
        ejecutar_macro_freecad(
            rutas["robot_plasma_dxf"],
            rutas["robot_plasma_step_A"],
            thk,
            "TR",
            4235, -1015, -700
        )
        ejecutar_macro_freecad(
            rutas["robot_plasma_dxf"],
            rutas["robot_plasma_step_B"],
            thk,
            "BR",
            4235, 840, -700
        )


def exportar_resultados_a_dxf(
    resultados: dict,
    out_dir: str,
    base_name: str = "NEST",
    generar_step: bool = False,
    wo_label: str | None = None,
    es_swo: bool = False,
    swo_id: str | None = None,
):
    try:
        from modules.nest_exporter import export_nest_to_dxf
    except ImportError:
        from nest_exporter import export_nest_to_dxf

    import config

    nest_folder = "NESTING"
    job_root_dir = os.path.join(out_dir, nest_folder)
    es_swo_export = _es_export_swo(base_name, wo_label, es_swo=es_swo)
    swo_ref = str(swo_id or base_name or "").strip()

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

    exportados_principales = []
    global_sheet_counter = 0
    thickness_para_step = getattr(config, "FREECAD_THK_MM", 6.35)
    plasma_offset_job = 0.0125 * 25.4

    for clave, data in (resultados or {}).items():
        if not isinstance(data, dict) or "error" in data or "hojas" not in data:
            continue

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
            global_sheet_counter += 1

            w_mm = hoja.get("placa_w", 2438.4)
            h_mm = hoja.get("placa_h", 1219.2)
            es_retazo = hoja.get("es_retazo", False)

            if es_swo_export and swo_ref.upper().startswith("SWO"):
                order_label = swo_ref
            else:
                order_label = str(wo_label or "").strip() or "W.O."

            display_name = _resolver_display_name_hoja(hoja, contador_placas)

            _inyectar_metadata_hoja(
                hoja,
                order_label=order_label,
                thickness_name=thickness_name,
                sheet_seq=global_sheet_counter,
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
            }

            carpeta_principal = _resolver_carpeta_principal(clave, hoja)
            generar_plasma_hoja = _debe_generar_plasma(clave, hoja)
            compensada_manual = bool(hoja.get("plasma_compensado_manual", False))
            off_manual = float(hoja.get("plasma_offset_mm_manual", plasma_offset_job) or plasma_offset_job)

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
                # Posición 1:1 con visor: exportar polígonos colocados (mm), no clonar bloque DXF.
                ruta_export = ""

                layer_override = pz.get("layer_override")
                closed_flag = pz.get("closed")
                if es_cu_hoja and not layer_override:
                    if es_linea_corte:
                        # Cama láser: solo líneas separadoras (misma capa que nesteo normal).
                        layer_override = "CUT_OUTER"
                        closed_flag = False
                    else:
                        # Contorno completo para STEP interno (no programación láser).
                        layer_override = "CUT_CU"
                        closed_flag = True

                placement = {
                    "part_name": nom,
                    "outer": outer_main,
                    "holes": holes_main,
                    "marks": pz.get("marcas", []),
                    "ruta": ruta_export,
                    "orig_minx": pz.get("orig_minx", 0.0),
                    "orig_miny": pz.get("orig_miny", 0.0),
                    "shift_x": pz.get("shift_x", 0.0),
                    "shift_y": pz.get("shift_y", 0.0),
                    "rot_deg": pz.get("rot_deg", 0.0),
                }
                if layer_override:
                    placement["layer_override"] = str(layer_override)
                if closed_flag is not None:
                    placement["closed"] = bool(closed_flag)
                placements_principales.append(placement)

                # DXF plasma (solo cuando aplique; cobre no usa plasma)
                if generar_plasma_hoja and not nom.startswith("RETAZO_GUILLOTINA") and not nom.startswith("CU_CORTE__") and not es_cu_hoja:
                    try:
                        if compensada_manual:
                            plasma_outer = outer_main
                            plasma_holes = holes_main
                        else:
                            off_use = off_manual if off_manual > 0 else plasma_offset_job
                            outer_poly = Polygon(pols[0])
                            plasma_outer = list(
                                outer_poly.buffer(off_use, join_style=2).exterior.coords
                            )

                            plasma_holes = []
                            for h in (pols[1:] if len(pols) > 1 else []):
                                h_comp = Polygon(h).buffer(-off_use, join_style=2)
                                if not h_comp.is_empty:
                                    plasma_holes.append(list(h_comp.exterior.coords))
                    except Exception:
                        plasma_outer = outer_main
                        plasma_holes = holes_main

                    plasma_outer, plasma_holes = _clean_profile_for_production(plasma_outer, plasma_holes)

                    placements_plasma.append({
                        "part_name": nom + "_PLASMA",
                        "outer": plasma_outer,
                        "holes": plasma_holes,
                        "marks": pz.get("marcas", []),
                        "ruta": "",
                        "orig_minx": pz.get("orig_minx", 0.0),
                        "orig_miny": pz.get("orig_miny", 0.0),
                        "shift_x": pz.get("shift_x", 0.0),
                        "shift_y": pz.get("shift_y", 0.0),
                        "rot_deg": pz.get("rot_deg", 0.0),
                    })

            sheet_code = hoja.get("sheet_code") or f"{order_label}-H{global_sheet_counter}"
            nest_tag = swo_ref if es_swo_export else str(base_name).strip() or "NEST"
            nombre_archivo = f"{nest_tag}_{thickness_name}_{sheet_code}.dxf"

            # Exportación principal
            if carpeta_principal == RUTA_CAMA_LASER:
                path_principal = os.path.join(rutas["cama_laser_dxf"], nombre_archivo)
            elif carpeta_principal == RUTA_CAMA_LASER_12KW:
                path_principal = os.path.join(rutas["cama_laser_12kw_dxf"], nombre_archivo)
            else:
                path_principal = os.path.join(rutas["robot_laser_dxf"], nombre_archivo)

            export_nest_to_dxf(
                path_principal,
                sheet_info,
                placements_principales,
                title=f"{carpeta_principal} | {clave}",
            )
            exportados_principales.append(path_principal)

            _registrar_exportacion_pqart_hoja(
                hoja,
                ruta_dxf=path_principal,
                tipo_corte=_normalizar_tipo_corte_pqart(carpeta_principal),
            )

            # Exportación plasma solo cuando aplique
            if generar_plasma_hoja and placements_plasma:
                path_plasma = os.path.join(rutas["robot_plasma_dxf"], nombre_archivo)
                export_nest_to_dxf(
                    path_plasma,
                    sheet_info,
                    placements_plasma,
                    title=f"{RUTA_ROBOT_PLASMA} | {clave}",
                )

                _registrar_exportacion_pqart_hoja(
                    hoja,
                    ruta_dxf=path_plasma,
                    tipo_corte=_normalizar_tipo_corte_pqart(RUTA_ROBOT_PLASMA),
                )

    if generar_step:
        lanzar_freecad_robotica(rutas, thickness_para_step, plasma_offset_job)
        if es_swo_export:
            n_step = _publicar_steps_en_3d_nesting(out_dir, rutas)
            print(f"[STEP][SWO] Publicados {n_step} STEP en 3D NESTING")

    if es_swo_export and swo_ref.upper().startswith("SWO"):
        from .api_client import enviar_reporte_a_api
        enviar_reporte_a_api(swo_ref, resultados)

    return exportados_principales