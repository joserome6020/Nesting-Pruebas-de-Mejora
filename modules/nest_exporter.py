# modules/nest_exporter.py
import os
import math
import uuid
import ezdxf
from ezdxf.addons import Importer
from ezdxf.math import Matrix44
from ezdxf import colors  
import config

from freecad_runner import ejecutar_macro_freecad

# =========================================================
# NEST DXF EXPORTER
# =========================================================

def _ensure_closed(poly):
    if not poly or len(poly) < 2:
        return poly
    if poly[0] != poly[-1]:
        return poly + [poly[0]]
    return poly

def _rotate_point(x, y, deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (x * c - y * s, x * s + y * c)

def _transform_poly(poly, tx=0.0, ty=0.0, rot_deg=0.0):
    out = []
    # 🚀 BLINDAJE: Validación de estructura de lista
    if not poly or not isinstance(poly, (list, tuple)): 
        return out
        
    for pt in poly:
        # 🚀 BLINDAJE: Evita 'tuple index out of range' en retazos incompletos
        if not isinstance(pt, (list, tuple)) or len(pt) < 2: 
            continue
            
        x, y = pt[0], pt[1]
        xr, yr = _rotate_point(x, y, rot_deg) if rot_deg else (x, y)
        out.append((xr + tx, yr + ty))
    return out

def _add_lwpolyline(msp, points, layer, closed=True):
    """
    Versión Purificada: 
    Delega el cierre del polígono puramente a la etiqueta 'closed'.
    """
    # 🚀 BLINDAJE: AutoCAD requiere al menos 2 puntos para una polilínea
    if not points or len(points) < 2: 
        return
    msp.add_lwpolyline(points, dxfattribs={"layer": layer, "closed": bool(closed)})

def _setup_layers(doc):
    layers = doc.layers
    def ensure(name, color_index):
        if name not in layers:
            layers.new(name, dxfattribs={"color": int(color_index)})
        else:
            layers.get(name).dxf.color = int(color_index)

    ensure("Plate", 3)      # Verde
    ensure("Plate_Text", 7) # Blanco
    ensure("CUT_OUTER", 1)  # Rojo
    ensure("CUT_INNER", 2)  # Amarillo
    ensure("MARK", 4)       # Cyan
    
# =========================================================================
# EXPORTACIÓN PRINCIPAL
# =========================================================================
def export_nest_to_dxf(
    out_path: str,
    sheet: dict,
    placements: list,
    *,
    title: str = "NEST_EXPORT",
    draw_holes: bool = True,
    draw_labels: bool = False,  
    draw_marks: bool = True,
    label_height: float = 25.0,
    margin_text: float = 15.0,
):
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4  
    _setup_layers(doc)

    msp = doc.modelspace()

    # ----- Dibuja la placa -----
    L = float(sheet.get("length", sheet.get("Length", 0)))
    W = float(sheet.get("width",  sheet.get("Width",  0)))

    sheet_poly = [(0, 0), (L, 0), (L, W), (0, W)]
    _add_lwpolyline(msp, sheet_poly, layer="Plate", closed=True)

    # ----- Texto de encabezado -----
    material = sheet.get("material", sheet.get("Material", ""))
    thickness = sheet.get("thickness", sheet.get("Thickness", ""))
    arga_code = sheet.get("arga_code", sheet.get("Arga Code", ""))

    header = f"{title} | {arga_code} | {material} | THK:{thickness} | {L:.1f}x{W:.1f} mm"
    msp.add_text(
        header,
        dxfattribs={"layer": "Plate_Text", "height": label_height} 
    ).set_placement((0, W + margin_text))

    # =========================================================================
    # INSERCIÓN DE PIEZAS: CLONACIÓN GÉNESIS
    # =========================================================================
    cache_blocks = {} 

    for i, p in enumerate(placements, start=1):
        ruta_original = p.get("ruta")
        part_name = str(p.get("part_name", p.get("name", f"PART_{i}")))
        inserted_block = False

        if ruta_original and os.path.exists(ruta_original):
            if ruta_original not in cache_blocks:
                safe_block_name = f"BLK_{uuid.uuid4().hex[:8]}"
                try:
                    part_doc = ezdxf.readfile(ruta_original)
                    blk = doc.blocks.new(name=safe_block_name)
                    importer = Importer(part_doc, doc)
                    importer.import_modelspace(blk)
                    importer.finalize()
                    cache_blocks[ruta_original] = safe_block_name
                except Exception as e:
                    print(f"[ERROR] No se pudo leer el DXF base {ruta_original}: {e}")
            
            safe_block_name = cache_blocks.get(ruta_original)
            
            if safe_block_name and safe_block_name in doc.blocks:
                try:
                    # 🚀 DATOS DE REINGENIERÍA INVERSA
                    orig_minx = float(p.get("orig_minx", 0.0))
                    orig_miny = float(p.get("orig_miny", 0.0))
                    shift_x = float(p.get("shift_x", 0.0))
                    shift_y = float(p.get("shift_y", 0.0))
                    rot_deg = float(p.get("rot_deg", 0.0))

                    # 1. Escala a MM
                    m = Matrix44.scale(25.4, 25.4, 25.4)
                    
                    # 2. Normalizar al origen Génesis
                    m @= Matrix44.translate(-orig_minx, -orig_miny, 0)
                    
                    # 3. Rotar según deducción matemática
                    if rot_deg != 0.0:
                        m @= Matrix44.z_rotate(math.radians(rot_deg))
                        
                    # 4. Posicionar en la coordenada final de la lámina
                    m @= Matrix44.translate(shift_x, shift_y, 0)

                    blockref = msp.add_blockref(safe_block_name, insert=(0, 0))
                    blockref.transform(m)
                    blockref.explode() 
                    inserted_block = True

                except Exception as e:
                    print(f"[ERROR] Transformación falló para {part_name}: {e}")

        # =========================================================================
        # FALLBACK PARA RETAZOS Y LÍNEAS PLANAS
        # =========================================================================
        if not inserted_block:
            outer = p.get("outer") or p.get("outer_poly")
            if outer:
                # 🚀 Los retazos YA vienen en coordenadas absolutas (tx=0, ty=0, rot=0)
                outer_t = _transform_poly(outer, tx=0.0, ty=0.0, rot_deg=0.0)
                if outer_t:
                    layer_destino = str(p.get("layer_override") or "CUT_OUTER")
                    closed_destino = bool(p.get("closed", True))
                    _add_lwpolyline(msp, outer_t, layer=layer_destino, closed=closed_destino)

                if draw_holes:
                    holes = p.get("holes") or p.get("inner") or []
                    for h in holes:
                        h_t = _transform_poly(h, tx=0.0, ty=0.0, rot_deg=0.0)
                        if h_t:
                            _add_lwpolyline(msp, h_t, layer="CUT_INNER", closed=True)

                if draw_marks:
                    marks = p.get("marks") or p.get("mark") or []
                    for mk in marks:
                        if not mk: continue
                        mk_t = _transform_poly(mk, tx=0.0, ty=0.0, rot_deg=0.0)
                        if mk_t:
                            _add_lwpolyline(msp, mk_t, layer="MARK", closed=True) 

    doc.saveas(out_path)
    return out_path

def export_all_sheets(out_dir, nests, *, base_name="NEST", draw_holes=True, draw_labels=False, draw_marks=True):
    os.makedirs(out_dir, exist_ok=True)
    exported = []
    for idx, n in enumerate(nests, start=1):
        sheet = n.get("sheet", {})
        placements = n.get("placements", [])
        out_path = os.path.join(out_dir, f"{base_name}_SHEET_{idx:02d}.dxf")
        export_nest_to_dxf(out_path, sheet, placements, title=f"{base_name} - SHEET {idx}", 
                           draw_holes=draw_holes, draw_labels=draw_labels, draw_marks=draw_marks)
        exported.append(out_path)
    return exported

class NestExporter:
    def export_all_sheets(self, job_folder_or_out_dir, *args, **kwargs):
        # Esta clase es un puente para mantener compatibilidad con la UI
        return []