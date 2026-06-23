# modules/nest_exporter.py
import os
import math
import uuid
import ezdxf
from ezdxf.addons import Importer
from ezdxf.math import Matrix44
from ezdxf import colors  
import config

from ezdxf import path as ezdxf_path

from modules.dxf_native_curves import export_ring_native, normalize_ring
from modules.nesting_engine.geometry_parser import ESCALA_DXF, _clasificar_capa
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

def _poly_bounds(poly):
    if not poly:
        return None
    xs, ys = [], []
    for pt in poly:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _bounds_close(a, b, tol=1.2):
    if a is None or b is None:
        return False
    return all(abs(float(a[i]) - float(b[i])) <= tol for i in range(4))


def _resolve_placement(p: dict) -> dict:
    """Completa shift desde el bbox del contorno colocado (p. ej. largos CU sin metadata 2D)."""
    out = dict(p)
    bounds = _poly_bounds(out.get("outer") or out.get("outer_poly"))
    if bounds:
        sx = float(out.get("shift_x", 0.0) or 0.0)
        sy = float(out.get("shift_y", 0.0) or 0.0)
        if abs(sx) < 1e-6 and abs(sy) < 1e-6:
            out["shift_x"] = bounds[0]
            out["shift_y"] = bounds[1]
    return out


def _build_placement_matrix(p) -> Matrix44:
    """Misma cadena que el visor/nesting: pulgadas→mm, normalizar, rotar en centroide, colocar en placa."""
    p = _resolve_placement(p)
    ox = float(p.get("orig_minx", 0.0))
    oy = float(p.get("orig_miny", 0.0))
    rot = math.radians(float(p.get("rot_deg", 0.0) or 0.0))
    sx = float(p.get("shift_x", 0.0))
    sy = float(p.get("shift_y", 0.0))
    rcx = float(p.get("rot_origin_cx", 0.0) or 0.0)
    rcy = float(p.get("rot_origin_cy", 0.0) or 0.0)

    m = Matrix44.scale(ESCALA_DXF, ESCALA_DXF, ESCALA_DXF)
    m @= Matrix44.translate(-ox, -oy, 0)
    if abs(rot) > 1e-12:
        m @= Matrix44.translate(rcx, rcy, 0)
        m @= Matrix44.z_rotate(rot)
        m @= Matrix44.translate(-rcx, -rcy, 0)
    m @= Matrix44.translate(sx, sy, 0)
    return m


def _ring_points_from_entity_mm(entity, *, flat_tol_mm: float = 0.05) -> list | None:
    """Vértices de una entidad ya transformada a mm (placa). None si no aplica."""
    typ = entity.dxftype()
    if typ in ("CIRCLE", "ARC", "LINE"):
        return None
    if typ not in ("LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
        return None
    try:
        p = ezdxf_path.make_path(entity)
        verts = list(p.flattening(distance=max(flat_tol_mm, 1e-4)))
        if len(verts) < 3:
            return None
        return [(float(v[0]), float(v[1])) for v in verts]
    except Exception:
        return None


def _export_inner_ring_native(msp, points, layer: str) -> bool:
    """Barreno: priorizar un CIRCLE nativo sobre segmentos."""
    return export_ring_native(msp, points, layer, closed=True, prefer_circle=True)


def _dest_layer_for_entity(clase: str, p: dict) -> str | None:
    part_name = str(p.get("part_name") or p.get("name") or "")
    if clase == "mark":
        return str(
            p.get("marks_layer")
            or p.get("marks_layer_override")
            or ("RTZ_LABEL" if part_name.startswith("TATUAJE") else "MARK")
        )
    if clase == "inner":
        return "CUT_INNER"
    if clase == "outer":
        return str(p.get("layer_override") or "CUT_OUTER")
    return None


def _export_source_dxf_at_placement(
    msp,
    p: dict,
    *,
    draw_marks: bool = True,
    bounds_tol: float = 3.0,
) -> bool:
    """
    Clona entidades nativas del DXF fuente (CIRCLE/ARC/LINE) en la posición del nest.
    Valida bbox contra el contorno colocado; si no coincide, devuelve False (sin alterar msp).
    """
    ruta = str(p.get("ruta") or "").strip()
    outer = p.get("outer") or p.get("outer_poly") or []
    if not ruta or not os.path.isfile(ruta) or len(outer) < 2:
        return False

    try:
        part_doc = ezdxf.readfile(ruta)
    except Exception as e:
        print(f"[WARN] DXF fuente ilegible {ruta}: {e}")
        return False

    m = _build_placement_matrix(_resolve_placement(p))
    outer_bounds = _poly_bounds(outer)
    staged = []
    staged_inner_rings: list[tuple[list, str]] = []
    outer_pts = []

    for entity in part_doc.modelspace():
        if entity.dxftype() not in (
            "LINE",
            "LWPOLYLINE",
            "POLYLINE",
            "ARC",
            "CIRCLE",
            "ELLIPSE",
            "SPLINE",
        ):
            continue
        clase = _clasificar_capa(str(entity.dxf.layer))
        if clase is None:
            continue
        if clase == "mark" and not draw_marks:
            continue
        dest = _dest_layer_for_entity(clase, p)
        if not dest:
            continue
        try:
            new_e = entity.copy()
            if not new_e.transform(m):
                continue
            new_e.dxf.layer = dest
            if clase == "inner" and new_e.dxftype() in (
                "LWPOLYLINE",
                "POLYLINE",
                "SPLINE",
                "ELLIPSE",
            ):
                ring = _ring_points_from_entity_mm(new_e)
                if ring:
                    staged_inner_rings.append((ring, dest))
                    continue
            staged.append(new_e)
            if clase == "outer":
                try:
                    from ezdxf import bbox as ezdxf_bbox

                    ext = ezdxf_bbox.extents([new_e])
                    outer_pts.extend(
                        [
                            (ext.extmin.x, ext.extmin.y),
                            (ext.extmax.x, ext.extmax.y),
                        ]
                    )
                except Exception:
                    pass
        except Exception:
            continue

    if not staged and not staged_inner_rings:
        return False

    if outer_bounds and outer_pts:
        xs = [pt[0] for pt in outer_pts]
        ys = [pt[1] for pt in outer_pts]
        src_bounds = (min(xs), min(ys), max(xs), max(ys))
        if not _bounds_close(outer_bounds, src_bounds, tol=bounds_tol):
            print(
                f"[WARN] DXF fuente no alinea con nest para "
                f"{p.get('part_name', '?')}: nest={outer_bounds} vs dxf={src_bounds}"
            )
            return False

    for ent in staged:
        msp.add_entity(ent)
    for ring, layer in staged_inner_rings:
        _export_inner_ring_native(msp, ring, layer)
    return True


def _export_ring_exact(msp, points, layer: str, *, closed: bool = True) -> bool:
    """
    Exporta anillo como LINE entre vértices del nest — sin inferir círculos/arcos.
    Posición 1:1 con el visor; respaldo cuando no hay DXF fuente válido.
    """
    pts = normalize_ring(points, closed=closed)
    if len(pts) < 2:
        return False
    if closed:
        for i in range(len(pts)):
            j = (i + 1) % len(pts)
            x1, y1 = pts[i]
            x2, y2 = pts[j]
            if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                continue
            msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})
    else:
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                continue
            msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})
    return True


def _add_lwpolyline(msp, points, layer, closed=True):
    """
    Versión Purificada: 
    Delega el cierre del polígono puramente a la etiqueta 'closed'.
    """
    # 🚀 BLINDAJE: AutoCAD requiere al menos 2 puntos para una polilínea
    if not points or len(points) < 2: 
        return
    msp.add_lwpolyline(points, dxfattribs={"layer": layer, "closed": bool(closed)})


def _export_placed_geometry(msp, p, *, draw_holes=True, draw_marks=True) -> bool:
    """
    Exporta contorno/marcas en coordenadas de placa (mm), 1:1 con el visor de nesting.
    Fuente de verdad: poligonos/marcas ya colocados por el motor.
    """
    outer = p.get("outer") or p.get("outer_poly")
    has_outer = bool(outer and len(outer) >= 2)
    marks = p.get("marks") or p.get("mark") or []
    if not has_outer and not marks:
        return False

    if has_outer:
        outer_t = _transform_poly(outer, tx=0.0, ty=0.0, rot_deg=0.0)
        if outer_t:
            layer_destino = str(p.get("layer_override") or "CUT_OUTER")
            closed_destino = bool(p.get("closed", True))
            if p.get("use_native_curves"):
                export_ring_native(msp, outer_t, layer_destino, closed=closed_destino)
            else:
                _export_ring_exact(msp, outer_t, layer_destino, closed=closed_destino)

        if draw_holes:
            holes = p.get("holes") or p.get("inner") or []
            for h in holes:
                h_t = _transform_poly(h, tx=0.0, ty=0.0, rot_deg=0.0)
                if h_t:
                    # Barrenos: siempre intentar CIRCLE/ARC nativo (anillos circulares cerrados).
                    # El contorno exterior usa líneas exactas en respaldo para no inventar
                    # círculos en esquinas de rectángulos.
                    _export_inner_ring_native(msp, h_t, "CUT_INNER")

    if draw_marks:
        part_name = str(p.get("part_name") or p.get("name") or "")
        marks_layer = str(
            p.get("marks_layer")
            or p.get("marks_layer_override")
            or ("RTZ_LABEL" if part_name.startswith("TATUAJE") else "MARK")
        )
        for mk in marks:
            if not mk:
                continue
            mk_t = _transform_poly(mk, tx=0.0, ty=0.0, rot_deg=0.0)
            if mk_t:
                _add_lwpolyline(msp, mk_t, layer=marks_layer, closed=False)

    return has_outer or bool(marks)

def _setup_layers(doc):
    layers = doc.layers
    def ensure(name, color_index):
        if name not in layers:
            layers.new(name, dxfattribs={"color": int(color_index)})
        else:
            layers.get(name).dxf.color = int(color_index)

    ensure("Plate", 3)      # Verde
    ensure("Plate_Text", 7) # Blanco
    ensure("CUT_OUTER", 1)  # Rojo — corte láser (rebanadas verticales en largos CU)
    ensure("CUT_INNER", 2)  # Amarillo — huecos internos
    ensure("CUT_CU", 1)     # Contorno pieza cobre — solo para generación STEP
    ensure("MARK", 4)       # Cyan — marcaje láser en piezas reales
    ensure("RTZ_LABEL", 4)  # Cyan — nombre RTZ (solo referencia; NO marcar en máquina)
    
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
        prefer_source = bool(p.get("prefer_source_dxf"))
        compensated = bool(p.get("compensated"))

        # 1) DXF fuente nativo en posición de nest (sin compensación; validado vs contorno colocado).
        if prefer_source and not compensated and ruta_original:
            if _export_source_dxf_at_placement(msp, p, draw_marks=draw_marks):
                continue

        # 2) Geometría colocada en placa (mm) — misma posición que el visor Qt (fuente de verdad).
        if _export_placed_geometry(
            msp, p, draw_holes=draw_holes, draw_marks=draw_marks
        ):
            continue

        # 3) Fallback: clonar bloque DXF si no hubo contorno colocado ni fuente preferida.
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
                    m = _build_placement_matrix(_resolve_placement(p))
                    blockref = msp.add_blockref(safe_block_name, insert=(0, 0))
                    blockref.transform(m)
                    blockref.explode()
                    inserted_block = True

                except Exception as e:
                    print(f"[ERROR] Transformación falló para {part_name}: {e}")

        if not inserted_block:
            print(
                f"[WARN] Sin geometría exportable para {part_name} "
                f"(sin outer colocado ni DXF fuente válido)"
            )

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