import os
import sys
import glob
import traceback

# 1. Redirigir todos los mensajes a un archivo de texto para ver qué pasa en modo GUI
log_path = r"C:\NEST_EXPORTS\freecad_log.txt"
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

def convert_one_dxf(dxf_path: str, out_dir: str, thk_mm: float, scale: float, off_x: float, off_y: float, off_z: float) -> None:
    name = os.path.splitext(os.path.basename(dxf_path))[0]
    out_step = os.path.join(out_dir, f"{name}.step")

    doc = App.newDocument(f"DXF_{name}")
    importDXF.insert(dxf_path, doc.Name)
    doc.recompute()

    outer_wires, inner_wires, plate_wires, mark_edges = [], [], [], []

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

        # Clasificación
        if "MARK" in layer_str or "ETCH" in layer_str or "TEXT" in layer_str:
            mark_edges.extend(_collect_edges_from_obj(obj))
        elif "INTER" in layer_str or "INNER" in layer_str or "HOLE" in layer_str:
            inner_wires.extend(_collect_closed_wires_from_obj(obj))
        elif "PLATE" in layer_str:
            plate_wires.extend(_collect_closed_wires_from_obj(obj))
        elif "OUTER" in layer_str or "EXTER" in layer_str or "0" in layer_str:
            outer_wires.extend(_collect_closed_wires_from_obj(obj))
        else:
            # Salvavidas: si la capa es rara, asumimos que es corte externo para no perderla
            outer_wires.extend(_collect_closed_wires_from_obj(obj))

    print(f" -> Resultados: {len(outer_wires)} OUTER, {len(inner_wires)} INNER, {len(mark_edges)} LÍNEAS DE MARCAJE.")

    if not outer_wires:
        print(f"[WARN] {name}: Sin piezas de corte. Saltando...")
        App.closeDocument(doc.Name)
        return

    # 2. CONVERSIÓN A SÓLIDOS Y BOOLEANOS
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

    objects_to_export = []
    jade_color = (0.0, 0.66, 0.42, 0.0) # Verde Jade
    mark_color = (1.0, 0.84, 0.0, 0.0)  # Amarillo Oro para Marcas

    # 3. ENSAMBLAJE (PLACAS Y PIEZAS)
    if plate_solids:
        comp_plate = Part.makeCompound(plate_solids)
        comp_plate.translate(App.Vector(off_x, off_y, off_z)) 
        feat_plate = doc.addObject("Part::Feature", "JADE_REFERENCE_PLATE")
        feat_plate.Label = "Jade_Plate"
        feat_plate.Shape = comp_plate
        if feat_plate.ViewObject: feat_plate.ViewObject.ShapeColor = jade_color
        objects_to_export.append(feat_plate)

    if final_parts_solids:
        comp_parts = Part.makeCompound(final_parts_solids)
        comp_parts.translate(App.Vector(off_x, off_y, off_z)) 
        feat_parts = doc.addObject("Part::Feature", "JADE_NESTED_PARTS")
        feat_parts.Label = "Jade_Parts"
        feat_parts.Shape = comp_parts
        if feat_parts.ViewObject: feat_parts.ViewObject.ShapeColor = jade_color
        objects_to_export.append(feat_parts)

    # 4. INYECCIÓN DE MARCAS EN 3D SUPERFICIAL
    if mark_edges:
        comp_marks = Part.makeCompound(mark_edges)
        
        if abs(scale - 1.0) > 1e-9:
            m = App.Matrix()
            m.A11 = m.A22 = scale; m.A33 = 1.0
            comp_marks = comp_marks.transformGeometry(m)
            
        # ELEVAR LÍNEAS A LA SUPERFICIE (thk_mm)
        comp_marks.translate(App.Vector(off_x, off_y, off_z + thk_mm))
        
        feat_marks = doc.addObject("Part::Feature", "YELLOW_MARKS")
        feat_marks.Label = "Marcas_Grabado"
        feat_marks.Shape = comp_marks
        if feat_marks.ViewObject:
            feat_marks.ViewObject.LineColor = mark_color
            feat_marks.ViewObject.PointColor = mark_color
            feat_marks.ViewObject.LineWidth = 3.0 # Más gruesas para el visualizador
        objects_to_export.append(feat_marks)

    doc.recompute()
    FreeCADGui.updateGui() 

    # 5. EXPORTACIÓN FINAL
    ImportGui.export(objects_to_export, out_step)
    print(f"[OK] {name}: STEP Creado exitosamente CON color y marcas 3D.")

    App.closeDocument(doc.Name)

def main():
    dxf_in = _env("FREECAD_DXF_IN", r"C:\NEST_EXPORTS")
    step_out = _env("FREECAD_STEP_OUT", dxf_in)
    thk_mm = _env_float("FREECAD_THK_MM", 6.35)
    scale = _env_float("FREECAD_SCALE", 1.0)
    
    off_x = _env_float("FREECAD_OFFSET_X", 0.0)
    off_y = _env_float("FREECAD_OFFSET_Y", 0.0)
    off_z = _env_float("FREECAD_OFFSET_Z", 0.0)

    print(f"Buscando DXF en: {dxf_in}")
    files = sorted(glob.glob(os.path.join(dxf_in, "*.dxf")))
    
    for dxf in files:
        try:
            print(f"Procesando: {dxf}")
            convert_one_dxf(dxf, step_out, thk_mm, scale, off_x, off_y, off_z)
        except Exception as e:
            print(f"[ERROR] Falló {dxf}: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    main()