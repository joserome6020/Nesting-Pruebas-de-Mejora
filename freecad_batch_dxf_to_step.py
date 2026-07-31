import os
import sys
import glob
import re
import traceback

# 1. Redirigir todos los mensajes a un archivo de texto para ver qué pasa en modo GUI
_log_dir = os.getenv("FREECAD_LOG_DIR") or os.path.join(
    os.getenv("FREECAD_STEP_OUT") or os.getcwd(), "_logs"
)
os.makedirs(_log_dir, exist_ok=True)
log_path = os.getenv("FREECAD_LOG_PATH") or os.path.join(_log_dir, "freecad_log.txt")
log_file = open(log_path, "w", encoding="utf-8")
sys.stdout = log_file
sys.stderr = log_file

print("=== INICIANDO PROCESO CON MOTOR GRÁFICO ===")

try:
    import FreeCAD as App
    import FreeCADGui
    import ImportGui  
    import Part
    import importDXF
    
    # ---------------------------------------------------------
    # HACK VITAL: OBLIGAR A FREECAD A GUARDAR LÍNEAS EN EL STEP
    # ---------------------------------------------------------
    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Import/hSTEP")
    prefs.SetBool("ExportFreeEdges", True)  # Obliga a guardar marcas sueltas
    prefs.SetBool("ExportFreeWires", True)  
    prefs.SetInt("WriteSchema", 2)          # AP214 (Conserva colores y capas)
    
    FreeCADGui.updateGui()
except ImportError as e:
    print(f"[ERROR FATAL] Falló la importación de módulos GUI: {e}")
    sys.exit(1)

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default

def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return default

_NESTING_THK_IN_RE = re.compile(r"NESTING[_\s-]*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_SWO_THK_IN_RE = re.compile(r"SWO[-\s]*\d+[_\s-]+([0-9]*\.?[0-9]+)", re.IGNORECASE)


def _thk_mm_from_dxf_name(path_or_name: str, default_mm: float) -> float:
    """Lee calibre en pulgadas de NESTING_<thk>_… o SWO-###_<thk>_… → mm."""
    name = os.path.basename(str(path_or_name or ""))
    m = _NESTING_THK_IN_RE.search(name) or _SWO_THK_IN_RE.search(name)
    if not m:
        return float(default_mm)
    try:
        return float(m.group(1)) * 25.4
    except Exception:
        return float(default_mm)

def _export_format() -> str:
    return _env("FREECAD_EXPORT_FORMAT", "step").strip().lower()

def _export_extension() -> str:
    fmt = _export_format()
    return ".igs" if fmt in ("iges", "igs") else ".step"

def _export_label() -> str:
    return "IGES" if _export_extension() == ".igs" else "STEP"

def _is_iges_export() -> bool:
    return _export_format() in ("iges", "igs")

def _cad_base_from_dxf_name(name: str) -> str:
    step_base = name
    idx = name.upper().find("W.O.")
    if idx != -1:
        step_base = name[idx:].strip()
    return step_base

def _configure_export_units():
    unit = _env("FREECAD_EXPORT_LINEAR_UNIT", "").strip().upper()
    fmt = _export_format()
    is_iges = fmt in ("iges", "igs")
    if not unit:
        unit = "MM" if is_iges else "IN"
    occt_unit = "MM" if unit == "MM" else "IN"
    try:
        h_part = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part")
        if is_iges:
            h_part.SetInt("UnitIges", 0)
            try:
                h_part.GetGroup("IGES").SetInt("Unit", 0)
            except Exception:
                pass
        else:
            h_part.SetInt("Unit", 2)
            try:
                h_part.SetInt("UnitStep", 2)
            except Exception:
                pass
    except Exception as exc:
        print(f"WARN Part unit prefs: {exc}")

    key = f"write.{'iges' if is_iges else 'step'}.unit"
    ok = False
    for static in _interface_static_candidates():
        for val in ([occt_unit] + (["INCH"] if occt_unit == "IN" else [])):
            try:
                static.SetCVal(key, val)
                print(f"OCCT {key}={val}")
                ok = True
                break
            except Exception:
                continue
        if ok:
            break
    if not ok:
        try:
            from OCC.Core.Interface import Interface_Static as OccStatic

            for val in ([occt_unit] + (["INCH"] if occt_unit == "IN" else [])):
                try:
                    OccStatic.SetCVal(key, val)
                    print(f"OCCT {key}={val}")
                    ok = True
                    break
                except Exception:
                    continue
        except Exception:
            pass
    if not ok:
        print(f"WARN: no se pudo fijar {key}={occt_unit}")
    print(f"Unidades export {('IGES' if is_iges else 'STEP')}: {unit}")

def _interface_static_candidates():
    out = []
    try:
        from OCC.Core.Interface import Interface_Static as OccStatic
        out.append(OccStatic)
    except Exception:
        pass
    try:
        if hasattr(Part, "Interface_Static"):
            out.append(Part.Interface_Static)
    except Exception:
        pass
    try:
        from Part.App import Interface
        out.append(Interface.Interface_Static)
    except Exception:
        pass
    return out

def _normalize_material_kind(raw: str) -> str:
    u = str(raw or "").strip().upper()
    if u in ("CU", "COBRE", "COPPER"):
        return "copper"
    return "steel"

def _material_kind_for_export() -> str:
    return _normalize_material_kind(_env("FREECAD_MATERIAL", "STEEL"))

def _appearance_preset(kind: str) -> dict:
    if kind == "copper":
        return {
            "diffuse": (0.78, 0.48, 0.22),
            "ambient": (0.32, 0.20, 0.09),
            "specular": (0.95, 0.78, 0.45),
            "shininess": 0.90,
        }
    return {
        "diffuse": (0.58, 0.60, 0.63),
        "ambient": (0.22, 0.23, 0.25),
        "specular": (0.90, 0.90, 0.92),
        "shininess": 0.72,
    }

def _apply_part_appearance(view_obj, kind: str) -> None:
    if not view_obj:
        return
    preset = _appearance_preset(kind)
    try:
        view_obj.ShapeAppearance = App.Material(
            DiffuseColor=preset["diffuse"],
            AmbientColor=preset["ambient"],
            SpecularColor=preset["specular"],
            EmissiveColor=(0.0, 0.0, 0.0),
            Shininess=float(preset["shininess"]),
            Transparency=0.0,
        )
        return
    except Exception:
        pass
    try:
        d = preset["diffuse"]
        view_obj.ShapeColor = (d[0], d[1], d[2], 0.0)
    except Exception:
        pass

def _collect_closed_wires_from_obj(obj):
    wires = []
    if not hasattr(obj, "Shape") or obj.Shape.isNull(): return wires
    try:
        for w in obj.Shape.Wires:
            if w and w.isClosed(): wires.append(w)
    except Exception: pass
    
    if not wires:
        try:
            groups = Part.sortEdges(obj.Shape.Edges)
            for g in groups:
                w = Part.Wire(g)
                if w.isClosed(): wires.append(w)
        except Exception: pass
    return wires

def _collect_edges_from_obj(obj):
    edges = []
    if not hasattr(obj, "Shape") or obj.Shape.isNull(): return edges
    try:
        for e in obj.Shape.Edges:
            edges.append(e)
    except Exception: pass
    return edges

def _wires_to_solids(wires, thk_mm: float, scale: float):
    solids = []
    vec = App.Vector(0, 0, thk_mm)
    for w in wires:
        try:
            solid = Part.Face(w).extrude(vec)
            if abs(scale - 1.0) > 1e-9:
                m = App.Matrix()
                m.A11 = m.A22 = scale; m.A33 = 1.0
                solid = solid.transformGeometry(m)
            solids.append(solid)
        except Exception: pass
    return solids

def _scale_shape_xy(shape, scale: float):
    if shape is None or shape.isNull() or abs(scale - 1.0) < 1e-9:
        return shape
    m = App.Matrix()
    m.A11 = m.A22 = scale
    m.A33 = 1.0
    return shape.transformGeometry(m)

def _scale_wires(wires, scale: float):
    out = []
    for w in wires or []:
        try:
            out.append(_scale_shape_xy(w, scale))
        except Exception:
            pass
    return out

def _scale_edges(edges, scale: float):
    out = []
    for e in edges or []:
        try:
            out.append(_scale_shape_xy(e, scale))
        except Exception:
            pass
    return out

def _bbox_of_shapes(shapes):
    bb = None
    for sh in shapes or []:
        if sh is None or sh.isNull():
            continue
        try:
            b = sh.BoundBox
        except Exception:
            continue
        if bb is None:
            bb = b
        else:
            bb.add(b)
    return bb

def _collect_open_cut_edges_from_obj(obj):
    """Aristas abiertas de corte (guillotinas, relieves) no incluidas en wires cerrados."""
    edges = []
    if not hasattr(obj, "Shape") or obj.Shape.isNull():
        return edges
    closed = set()
    try:
        for w in obj.Shape.Wires:
            if w and w.isClosed():
                for e in w.Edges:
                    closed.add(e.hashCode())
    except Exception:
        pass
    try:
        for e in obj.Shape.Edges:
            if e.hashCode() not in closed:
                edges.append(e)
    except Exception:
        pass
    return edges

def _normalize_shape_to_origin(shape):
    bb = shape.BoundBox
    shape.translate(App.Vector(-bb.XMin, -bb.YMin, -bb.ZMin))
    return shape

def _cyptube_rotate_length_to_z(shape, pivot):
    shape.rotate(pivot, App.Vector(0, 1, 0), 90)
    return shape

def _build_cyptube_flatbar_iges(
    doc,
    outer_wires,
    inner_wires,
    plate_wires,
    cut_open_edges,
    thk_mm: float,
    scale: float,
    off_x: float,
    off_y: float,
    off_z: float,
    material_kind: str,
):
    """IGES CypTube: sólido + largo en +Z + contornos láser."""
    ow = _scale_wires(outer_wires, scale)
    iw = _scale_wires(inner_wires, scale)
    pw = _scale_wires(plate_wires, scale)
    oe = _scale_edges(cut_open_edges, scale)

    ref = list(pw or ow or iw)
    if not ref and not oe:
        return []

    bb = _bbox_of_shapes(ref + oe)
    if bb is None or bb.XLength < 0.5 or bb.YLength < 0.5:
        print("[WARN] IGES CypTube: bbox inválido, sin stock.")
        return []

    shift = App.Vector(-bb.XMin + float(off_x), -bb.YMin + float(off_y), float(off_z))
    pivot = App.Vector(shift.x, shift.y, shift.z)
    bar_len = float(bb.XLength)
    bar_wid = float(bb.YLength)
    bar_thk = float(thk_mm)

    print(
        f" -> IGES CypTube: stock {bar_len:.2f}×{bar_wid:.2f}×{bar_thk:.2f} mm, "
        f"eje largo→+Z | cortes: {len(ow)} outer, {len(iw)} inner, {len(oe)} abiertos"
    )

    stock = Part.makeBox(bar_len, bar_wid, bar_thk)
    stock.translate(shift)
    result = stock

    hole_tools = []
    for iw in iw:
        try:
            iw2 = iw.copy()
            iw2.translate(shift)
            tool = Part.Face(iw2).extrude(App.Vector(0, 0, bar_thk + 0.4))
            tool.translate(App.Vector(0, 0, -0.2))
            hole_tools.append(tool)
        except Exception:
            pass
    if hole_tools:
        try:
            result = result.cut(Part.makeCompound(hole_tools))
        except Exception:
            pass

    _cyptube_rotate_length_to_z(result, pivot)

    cut_parts = []
    top = shift + App.Vector(0, 0, bar_thk)
    for w in list(ow) + list(iw):
        try:
            w2 = w.copy()
            w2.translate(top)
            _cyptube_rotate_length_to_z(w2, pivot)
            cut_parts.append(w2)
        except Exception:
            pass
    for e in oe:
        try:
            e2 = e.copy()
            e2.translate(top)
            _cyptube_rotate_length_to_z(e2, pivot)
            cut_parts.append(e2)
        except Exception:
            pass

    all_shapes = [result] + cut_parts
    bb_out = _bbox_of_shapes(all_shapes)
    if bb_out is not None:
        delta = App.Vector(-bb_out.XMin, -bb_out.YMin, -bb_out.ZMin)
        result.translate(delta)
        for sh in cut_parts:
            try:
                sh.translate(delta)
            except Exception:
                pass

    export_parts = [result]
    if cut_parts:
        export_parts.append(Part.makeCompound(cut_parts))

    export_shape = Part.makeCompound(export_parts) if len(export_parts) > 1 else export_parts[0]

    objects = []
    feat = doc.addObject("Part::Feature", "CYPTUBE_FLATBAR")
    feat.Label = "CypTube_Barra"
    feat.Shape = export_shape
    _apply_part_appearance(feat.ViewObject, material_kind)
    objects.append(feat)
    return objects

def convert_one_dxf(dxf_path: str, out_dir: str, thk_mm: float, scale: float, off_x: float, off_y: float, off_z: float) -> None:
    name = os.path.splitext(os.path.basename(dxf_path))[0]
    cad_ext = _export_extension()
    cad_label = _export_label()
    cad_base = _cad_base_from_dxf_name(name)
    out_cad = os.path.join(out_dir, f"{cad_base}{cad_ext}")

    if os.getenv("FREECAD_SKIP_EXISTING", "1").strip().lower() not in ("0", "false", "no"):
        try:
            if os.path.isfile(out_cad) and os.path.getsize(out_cad) > 512:
                if os.path.getmtime(out_cad) >= os.path.getmtime(dxf_path):
                    print(f"[SKIP] {name}: {cad_label} vigente -> {out_cad}")
                    return
        except Exception:
            pass

    doc = App.newDocument(f"DXF_{name}")
    importDXF.insert(dxf_path, doc.Name)
    doc.recompute()

    outer_wires, inner_wires, plate_wires, mark_edges, cut_open_edges = [], [], [], [], []
    has_cut_cu = False
    cyptube_iges = _is_iges_export()

    print(f"\n--- Analizando DXF: {name} ---")

    # 1. ESCANEO PROFUNDO DE CAPAS (A prueba de fallos)
    for obj in doc.Objects:
        if not hasattr(obj, "Shape") or obj.Shape.isNull(): continue
        if obj.isDerivedFrom("App::DocumentObjectGroup"): continue # Ignorar la carpeta/grupo en sí

        # Reconstruir el nombre de la capa mirando a sus padres
        layer_str = obj.Label.upper()
        if hasattr(obj, "InList"):
            for parent in obj.InList:
                if hasattr(parent, "Group"):
                    layer_str += "_" + parent.Label.upper()

        if "CUT_CU" in layer_str:
            has_cut_cu = True

        # Clasificación
        # CUT_CU: contorno cerrado de piezas cobre → sólidos STEP.
        if "CUT_CU" in layer_str:
            outer_wires.extend(_collect_closed_wires_from_obj(obj))
        elif "MARK" in layer_str or "ETCH" in layer_str or "TEXT" in layer_str:
            mark_edges.extend(_collect_edges_from_obj(obj))
        elif "INTER" in layer_str or "INNER" in layer_str or "HOLE" in layer_str:
            inner_wires.extend(_collect_closed_wires_from_obj(obj))
        elif "PLATE" in layer_str:
            plate_wires.extend(_collect_closed_wires_from_obj(obj))
        elif (
            "OUTER" in layer_str
            or "EXTER" in layer_str
            or "CUT_OUTER" in layer_str
            or layer_str.endswith("_CUT")
            or layer_str == "CUT"
        ):
            outer_wires.extend(_collect_closed_wires_from_obj(obj))
            if cyptube_iges:
                cut_open_edges.extend(_collect_open_cut_edges_from_obj(obj))
        else:
            # Salvavidas: si la capa es rara, asumimos que es corte externo para no perderla
            outer_wires.extend(_collect_closed_wires_from_obj(obj))
            if cyptube_iges:
                cut_open_edges.extend(_collect_open_cut_edges_from_obj(obj))

    print(f" -> Resultados: {len(outer_wires)} OUTER, {len(inner_wires)} INNER, {len(mark_edges)} LÍNEAS DE MARCAJE.")
    if cyptube_iges:
        print(f" -> Cortes abiertos (guillotina/relieve): {len(cut_open_edges)}")

    if not outer_wires and not cut_open_edges:
        print(f"[WARN] {name}: Sin piezas de corte. Saltando...")
        App.closeDocument(doc.Name)
        return

    objects_to_export = []
    material_kind = "copper" if has_cut_cu else _material_kind_for_export()
    mark_color = (0.55, 0.55, 0.58, 0.0)  # grabado sutil

    if cyptube_iges:
        objects_to_export = _build_cyptube_flatbar_iges(
            doc,
            outer_wires,
            inner_wires,
            plate_wires,
            cut_open_edges,
            thk_mm,
            scale,
            off_x,
            off_y,
            off_z,
            material_kind,
        )
        if not objects_to_export:
            print(f"[WARN] {name}: IGES CypTube sin geometría exportable.")
            App.closeDocument(doc.Name)
            return
    else:
        # 2. CONVERSIÓN A SÓLIDOS Y BOOLEANOS (STEP / visualización 3D)
        outer_solids = _wires_to_solids(outer_wires, thk_mm, scale)
        inner_solids = _wires_to_solids(inner_wires, thk_mm, scale)
        plate_solids = _wires_to_solids(plate_wires, thk_mm, scale)

        final_parts_solids = []
        if inner_solids:
            inner_compound = Part.makeCompound(inner_solids)
            for osol in outer_solids:
                try:
                    final_parts_solids.append(osol.cut(inner_compound))
                except Exception:
                    final_parts_solids.append(osol)
        else:
            final_parts_solids = outer_solids

        # 3. ENSAMBLAJE (PLACAS Y PIEZAS)
        if plate_solids:
            comp_plate = Part.makeCompound(plate_solids)
            comp_plate.translate(App.Vector(off_x, off_y, off_z))
            feat_plate = doc.addObject("Part::Feature", "JADE_REFERENCE_PLATE")
            feat_plate.Label = "Jade_Plate"
            feat_plate.Shape = comp_plate
            _apply_part_appearance(feat_plate.ViewObject, material_kind)
            objects_to_export.append(feat_plate)

        if final_parts_solids:
            comp_parts = Part.makeCompound(final_parts_solids)
            comp_parts.translate(App.Vector(off_x, off_y, off_z))
            feat_parts = doc.addObject("Part::Feature", "JADE_NESTED_PARTS")
            feat_parts.Label = "Jade_Parts"
            feat_parts.Shape = comp_parts
            _apply_part_appearance(feat_parts.ViewObject, material_kind)
            objects_to_export.append(feat_parts)

        # 4. INYECCIÓN DE MARCAS EN 3D SUPERFICIAL (solo STEP)
        if mark_edges:
            comp_marks = Part.makeCompound(mark_edges)

            if abs(scale - 1.0) > 1e-9:
                m = App.Matrix()
                m.A11 = m.A22 = scale; m.A33 = 1.0
                comp_marks = comp_marks.transformGeometry(m)

            comp_marks.translate(App.Vector(off_x, off_y, off_z + thk_mm))

            feat_marks = doc.addObject("Part::Feature", "YELLOW_MARKS")
            feat_marks.Label = "Marcas_Grabado"
            feat_marks.Shape = comp_marks
            if feat_marks.ViewObject:
                feat_marks.ViewObject.LineColor = mark_color
                feat_marks.ViewObject.PointColor = mark_color
                feat_marks.ViewObject.LineWidth = 3.0
            objects_to_export.append(feat_marks)

    doc.recompute()
    FreeCADGui.updateGui() 

    # 5. EXPORTACIÓN FINAL
    _configure_export_units()
    ImportGui.export(objects_to_export, out_cad)
    print(f"[OK] {name}: {cad_label} creado exitosamente.")

    App.closeDocument(doc.Name)

def main():
    dxf_in = _env("FREECAD_DXF_IN", r"C:\NEST_EXPORTS")
    step_out = _env("FREECAD_STEP_OUT", dxf_in)
    thk_mm = _env_float("FREECAD_THK_MM", 6.35)
    scale = _env_float("FREECAD_SCALE", 1.0)
    
    off_x = _env_float("FREECAD_OFFSET_X", 0.0)
    off_y = _env_float("FREECAD_OFFSET_Y", 0.0)
    off_z = _env_float("FREECAD_OFFSET_Z", 0.0)

    single = os.getenv("FREECAD_DXF_SINGLE", "").strip()
    if single and os.path.isfile(single):
        files = [os.path.normpath(single)]
        print(f"Procesando 1 archivo (FREECAD_DXF_SINGLE): {files[0]}")
    else:
        files = sorted(glob.glob(os.path.join(dxf_in, "*.dxf")))
        print(f"Buscando DXF en: {dxf_in} ({len(files)} archivo(s))")
    
    for dxf in files:
        try:
            thk_file = _thk_mm_from_dxf_name(dxf, thk_mm)
            print(f"Procesando: {dxf} | thk_mm={thk_file:.4f}")
            convert_one_dxf(dxf, step_out, thk_file, scale, off_x, off_y, off_z)
        except Exception as e:
            print(f"[ERROR] Falló {dxf}: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    main()