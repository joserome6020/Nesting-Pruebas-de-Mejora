import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
import re
import math

import ezdxf
from shapely.geometry import Polygon, MultiPolygon

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QFileDialog,
        QDoubleSpinBox,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QCheckBox,
        QTextEdit,
        QVBoxLayout,
    )
    HAS_QT = True
except Exception:
    HAS_QT = False


OUTER_LAYERS_DEFAULT = ("CUT_OUTER", "OUTER_CUT", "IV_OUTER_PROFILE", "OUTER", "CORTE_EXTERNO")
INNER_LAYERS_DEFAULT = ("CUT_INNER", "INNER_CUT", "IV_INTERIOR_PROFILES")
NESTING_DXF_SEGMENTS = (
    ("CAMA LASER SIN MINI NEST", "DXF"),
    ("CAMA LASER 12 KW SIN MINI NEST", "DXF"),
    ("ROBOT LASER + MINI NEST", "DXF"),
    ("ROBOT PLASMA", "DXF"),
)


def compute_plasma_offset_mm(thickness_in: float) -> float:
    """
    Regla de compensación plasma (export + PARTS):
    - > 0.75 in  => 0.250 in
    - <= 0.75 in => 0.0125 in
    Convertido a mm.
    """
    base_in = 0.250 if float(thickness_in) > 0.75 else 0.0125
    return base_in * 25.4


def aplicar_compensacion_poligono(poly, offset_mm: float):
    """Offset exterior para geometría nesting (mm). None si falla."""
    try:
        from modules.plasma_offset2d import offset_simple_ring

        off = float(offset_mm or 0.0)
        if poly is None or getattr(poly, "is_empty", True) or off <= 0:
            return None
        p = poly.buffer(0)
        if p is None or p.is_empty:
            return None
        geoms = list(p.geoms) if isinstance(p, MultiPolygon) else [p]
        parts = []
        for g in geoms:
            coords = list(g.exterior.coords)
            res = offset_simple_ring(coords, delta=off)
            if not res.ok or not res.rings:
                continue
            for ring in res.rings:
                try:
                    parts.append(Polygon(ring))
                except Exception:
                    continue
        if not parts:
            return None
        out = parts[0]
        for q in parts[1:]:
            try:
                out = out.union(q)
            except Exception:
                pass
        return out.buffer(0) if out is not None and not out.is_empty else None
    except Exception:
        return None


def _normalize_layers(values: Iterable[str]) -> set:
    return {str(v).strip().upper() for v in values if str(v).strip()}


def _rol_capa_plasma(layer: str) -> str | None:
    """outer / inner / None — alineado a Processed / nesting."""
    u = str(layer or "").upper().strip()
    if not u:
        return None
    if any(
        m in u
        for m in ("MARK", "ETCH", "IV_MARK", "IV_FEATURE", "GRABADO", "MARCAJE", "SCRIBE")
    ):
        return None
    if ("CUT_INNER" in u) or ("INNER_CUT" in u) or ("IV_INTERIOR" in u) or (
        "INTERIOR" in u and "OUTER" not in u
    ):
        return "inner"
    if (
        ("CUT_OUTER" in u)
        or ("OUTER_CUT" in u)
        or ("IV_OUTER" in u)
        or u in ("0", "OUTER", "CORTE_EXTERNO")
    ):
        return "outer"
    if "CUT" in u:
        return "outer"
    return None


def ruta_dxf_plasma_compensado(ruta_origen: str | Path) -> Path:
    """
    Processed Files/foo.dxf → Processed Files/Plasma Compensated/foo.dxf
    (si no hay carpeta Processed, crea Plasma Compensated junto al DXF).
    """
    src = Path(str(ruta_origen or ""))
    parent = src.parent
    if parent.name.lower() == "processed files":
        out_dir = parent / "Plasma Compensated"
    else:
        out_dir = parent / "Plasma Compensated"
    return out_dir / src.name


def asegurar_dxf_plasma_compensado(
    ruta_origen: str | Path,
    offset_mm: float,
    *,
    forzar: bool = False,
) -> tuple[str | None, str]:
    """
    Genera DXF compensado (OUTER+, INNER−) desde el Processed original.
    Returns (ruta_salida, error).
    """
    from modules.plasma_offset2d import (
        PLASMA_OFFSET_ALGO_VERSION,
        compensated_dxf_is_current,
        write_version_sidecar,
    )

    src = Path(str(ruta_origen or ""))
    if not src.is_file():
        return None, f"No existe el DXF origen:\n{src}"
    off = float(offset_mm or 0.0)
    if off <= 0:
        return None, "Offset plasma inválido."
    dst = ruta_dxf_plasma_compensado(src)
    try:
        if not forzar and compensated_dxf_is_current(src, dst):
            return str(dst), ""
        stats = compensate_dxf_for_plasma(src, dst, offset_mm=off)
        if int(stats.get("changed") or 0) <= 0:
            return None, (
                "No se pudo compensar el DXF (sin contornos CUT_OUTER/CUT_INNER cerrados). "
                f"targets={stats.get('total_targets')} skipped={stats.get('skipped')} "
                f"algo={PLASMA_OFFSET_ALGO_VERSION} backend={stats.get('backend')}"
            )
        write_version_sidecar(dst, backend=str(stats.get("backend") or ""))
        return str(dst), ""
    except Exception as exc:
        return None, f"Error al compensar DXF:\n{exc}"


def _is_closed_polyline(entity) -> bool:
    etype = entity.dxftype()
    if etype == "LWPOLYLINE":
        return bool(getattr(entity, "closed", False))
    if etype == "POLYLINE":
        try:
            return bool(entity.is_closed)
        except Exception:
            return False
    return False


def _arc_points_from_bulge(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    bulge: float,
    max_deg_step: float = 7.5,
) -> List[Tuple[float, float]]:
    """
    Convierte un segmento con bulge (DXF) a puntos intermedios de arco.
    Devuelve [p1, ..., p2].
    """
    if abs(bulge) < 1e-12:
        return [p1, p2]

    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    chord = math.hypot(dx, dy)
    if chord <= 1e-9:
        return [p1, p2]

    theta = 4.0 * math.atan(bulge)  # signed central angle
    half_theta = abs(theta) / 2.0
    sin_half = math.sin(half_theta)
    if abs(sin_half) < 1e-12:
        return [p1, p2]

    radius = chord / (2.0 * sin_half)
    mx = (x1 + x2) / 2.0
    my = (y1 + y2) / 2.0

    alpha = math.atan2(dy, dx)
    h_sq = max(radius * radius - (chord / 2.0) ** 2, 0.0)
    h = math.sqrt(h_sq)
    nx = -math.sin(alpha)
    ny = math.cos(alpha)
    sign = 1.0 if bulge > 0 else -1.0
    cx = mx + sign * h * nx
    cy = my + sign * h * ny

    a1 = math.atan2(y1 - cy, x1 - cx)
    # avanzamos por theta signed desde a1 hasta a2
    step = math.radians(max_deg_step)
    n = max(2, int(math.ceil(abs(theta) / max(step, 1e-6))))
    pts: List[Tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        a = a1 + theta * t
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return pts


def _entity_points_xy(entity) -> Optional[List[Tuple[float, float]]]:
    etype = entity.dxftype()

    if etype == "LWPOLYLINE":
        verts = []
        for item in entity.get_points("xyb"):
            if len(item) >= 3:
                x, y, b = item[0], item[1], item[2]
            else:
                x, y = item[0], item[1]
                b = 0.0
            verts.append((float(x), float(y), float(b or 0.0)))
    elif etype == "POLYLINE":
        verts = []
        for v in entity.vertices:
            x = float(v.dxf.location.x)
            y = float(v.dxf.location.y)
            b = float(getattr(v.dxf, "bulge", 0.0) or 0.0)
            verts.append((x, y, b))
    else:
        return None

    if len(verts) < 3:
        return None

    # Solo contornos cerrados para compensación por área.
    if not _is_closed_polyline(entity):
        return None

    sampled: List[Tuple[float, float]] = []
    n = len(verts)
    for i in range(n):
        x1, y1, b = verts[i]
        x2, y2, _ = verts[(i + 1) % n]
        seg = _arc_points_from_bulge((x1, y1), (x2, y2), b)
        if not seg:
            continue
        if not sampled:
            sampled.extend(seg)
        else:
            sampled.extend(seg[1:])  # evitar duplicado del punto de unión

    if len(sampled) < 3:
        return None
    return sampled


def _buffer_polygon_points(
    points: Sequence[Tuple[float, float]],
    offset_mm: float,
    *,
    join_style: int = 1,
) -> Optional[List[List[Tuple[float, float]]]]:
    """Offset de anillo cerrado vía plasma_offset2d (FreeCAD → Clipper semantics)."""
    from modules.plasma_offset2d import offset_simple_ring

    # offset_mm aquí es en unidades DXF (ya convertido por el caller).
    result = offset_simple_ring(list(points or []), delta=float(offset_mm))
    if not result.ok:
        return None
    return result.rings or None


def _compensate_dxf_occt_exact(
    doc,
    *,
    off_dxf: float,
    outer_set: set,
    inner_set: set,
) -> dict:
    """Desfase productivo con dos motores en cascada.

    1. **OCCT** primero: conserva LINE/ARC/CIRCLE nativos (alta fidelidad).
    2. **Clipper2** si OCCT rechaza o falla: escribe polilínea cerrada.

    Clipper2 es el motor que usa FreeCAD Path/CAM y opera con aritmética
    entera: no rechaza perfiles reales. Es el respaldo bulletproof que evita
    que una geometría de producción bloquee el flujo (visor y export).
    """
    from modules.plasma_occt_offset import (
        occt_available,
        offset_entities as offset_entities_occt,
    )
    from modules.plasma_offset_clipper import (
        clipper_disponible,
        offset_ring_as_lwpolyline_points,
    )

    tiene_occt = occt_available()
    tiene_clipper = clipper_disponible()
    if not tiene_occt and not tiene_clipper:
        raise RuntimeError(
            "Plasma sin motor de offset: instala OCP (Open CASCADE) o pyclipr."
        )
    from modules.plasma_dxf_export import _group_connected_cut_entities

    msp = doc.modelspace()
    pools: dict[str, list] = {"outer": [], "inner": []}
    for ent in list(msp):
        layer = str(ent.dxf.layer or "").upper()
        role = _rol_capa_plasma(layer)
        if role is None:
            role = "outer" if layer in outer_set else ("inner" if layer in inner_set else None)
        if role and ent.dxftype() in {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}:
            pools[role].append(ent)

    def _write_native(spec: dict, *, layer: str, color, linetype) -> None:
        attrs = {"layer": layer}
        if color is not None:
            attrs["color"] = color
        if linetype is not None:
            attrs["linetype"] = linetype
        typ = spec["type"]
        if typ == "LINE":
            msp.add_line(spec["start"], spec["end"], dxfattribs=attrs)
        elif typ == "ARC":
            msp.add_arc(
                spec["center"],
                spec["radius"],
                spec["start_angle"],
                spec["end_angle"],
                dxfattribs=attrs,
            )
        elif typ == "CIRCLE":
            msp.add_circle(spec["center"], spec["radius"], dxfattribs=attrs)
        else:
            raise RuntimeError(f"OCCT devolvió entidad DXF desconocida: {typ}")

    def _escribir_polilinea(unit, delta, layer, color, linetype) -> str:
        """Fallback Clipper2: escribe LWPOLYLINE cerrada; devuelve motivo si falla."""
        if not tiene_clipper:
            return "sin fallback Clipper2 disponible"
        pts, motivo = offset_ring_as_lwpolyline_points(unit, delta=delta)
        if not pts:
            return motivo or "clipper vacío"
        attrs = {"layer": layer}
        if color is not None:
            attrs["color"] = color
        if linetype is not None:
            attrs["linetype"] = linetype
        # Clipper2 devuelve segmentos rectos; format 'xy' basta y el visor calcula
        # el área correctamente porque la polilínea queda cerrada.
        msp.add_lwpolyline(pts, format="xy", close=True, dxfattribs=attrs)
        return ""

    changed = skipped = circles = 0
    backends: set[str] = set()
    for role in ("outer", "inner"):
        pool = pools[role]
        if not pool:
            continue
        delta = off_dxf if role == "outer" else -off_dxf
        circles_ents = [e for e in pool if e.dxftype() == "CIRCLE"]
        chains = [e for e in pool if e.dxftype() in {"LINE", "ARC"}]
        polylines = [e for e in pool if e.dxftype() in {"LWPOLYLINE", "POLYLINE"}]

        for ent in circles_ents:
            radius = float(ent.dxf.radius) + delta
            if radius <= 1e-9:
                raise RuntimeError("OFFSET colapsó un CIRCLE interno; se rechaza el DXF.")
            ent.dxf.radius = radius
            changed += 1
            circles += 1
            backends.add("radius")

        groups = _group_connected_cut_entities(chains) if chains else []
        # Un outer por perfil; todos los inners son cortes independientes.
        if role == "outer" and len(groups) > 1:
            raise RuntimeError("DXF tiene varios OUTER LINE/ARC; no se compensará ambiguamente.")
        units = groups + [[p] for p in polylines]
        for unit in units:
            exemplar = unit[0]
            layer = str(exemplar.dxf.layer or "")
            color = getattr(exemplar.dxf, "color", None)
            linetype = getattr(exemplar.dxf, "linetype", None)
            era_polilinea = exemplar.dxftype() in {"LWPOLYLINE", "POLYLINE"}

            # 1) OCCT: alta fidelidad (LINE/ARC/CIRCLE nativos)
            ok_occt = False
            if tiene_occt:
                result = offset_entities_occt(unit, delta=delta)
                if result.ok:
                    for ent in unit:
                        ent.destroy()
                    escrito = False
                    if era_polilinea:
                        from modules.plasma_occt_offset import (
                            lwpolyline_points_from_specs,
                        )

                        pts = lwpolyline_points_from_specs(result.entities)
                        if pts:
                            attrs = {"layer": layer}
                            if color is not None:
                                attrs["color"] = color
                            if linetype is not None:
                                attrs["linetype"] = linetype
                            msp.add_lwpolyline(
                                pts, format="xyb", close=True, dxfattribs=attrs
                            )
                            escrito = True
                    if not escrito:
                        for spec in result.entities:
                            _write_native(spec, layer=layer, color=color, linetype=linetype)
                    ok_occt = True
                    backends.add("occt")

            # 2) Clipper2: motor bulletproof (FreeCAD Path/CAM). Nunca dejar el DXF
            #    sin compensar por rigidez de OCCT con perfiles reales.
            if not ok_occt:
                motivo = _escribir_polilinea(unit, delta, layer, color, linetype)
                if motivo:
                    raise RuntimeError(f"OFFSET: {motivo}")
                for ent in unit:
                    ent.destroy()
                backends.add("clipper2")
            changed += 1

    backend_final = "+".join(sorted(backends)) if backends else "occt_BRepOffsetAPI_MakeOffset"
    return {
        "changed": changed,
        "skipped": skipped,
        "total_targets": len(pools["outer"]) + len(pools["inner"]),
        "circles": circles,
        "backend": backend_final,
    }


def _pools_por_rol(doc, *, outer_set: set, inner_set: set) -> dict[str, list]:
    pools: dict[str, list] = {"outer": [], "inner": []}
    for ent in list(doc.modelspace()):
        layer = str(ent.dxf.layer or "").upper()
        role = _rol_capa_plasma(layer)
        if role is None:
            role = "outer" if layer in outer_set else ("inner" if layer in inner_set else None)
        if role and ent.dxftype() in {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}:
            pools[role].append(ent)
    return pools


def _verificar_dxf_compensado(
    doc_src,
    doc_out,
    *,
    off_dxf: float,
    outer_set: set,
    inner_set: set,
) -> None:
    """Compuerta fail-closed: el DXF escrito debe medir y cerrar como se espera.

    Releer el resultado es lo único que detecta un perfil deforme (contorno
    abierto, lazos de esquina o tamaño que no creció ``off_dxf`` por lado).
    """
    from modules.plasma_dxf_export import _group_connected_cut_entities, _order_connected_entities
    from modules.plasma_occt_offset import (
        ring_from_specs,
        ring_is_simple,
        specs_bbox,
        specs_from_dxf_entities,
        validate_offset_bbox_growth,
    )

    src_outer = _pools_por_rol(doc_src, outer_set=outer_set, inner_set=inner_set)["outer"]
    out_outer = _pools_por_rol(doc_out, outer_set=outer_set, inner_set=inner_set)["outer"]
    if not src_outer or not out_outer:
        return

    bbox_src = specs_bbox(specs_from_dxf_entities(src_outer))
    bbox_out = specs_bbox(specs_from_dxf_entities(out_outer))
    if bbox_src is None or bbox_out is None:
        raise RuntimeError("PLASMA: no se pudo medir el contorno exterior compensado.")

    motivo = validate_offset_bbox_growth(bbox_src, bbox_out, float(off_dxf))
    if motivo:
        raise RuntimeError(
            "PLASMA: el contorno compensado no mide lo esperado "
            f"({bbox_src[0] + 2.0 * float(off_dxf):.4f}x"
            f"{bbox_src[1] + 2.0 * float(off_dxf):.4f} vs "
            f"{bbox_out[0]:.4f}x{bbox_out[1]:.4f}; {motivo}); "
            "se rechaza el DXF."
        )

    for poli in [e for e in out_outer if e.dxftype() in ("LWPOLYLINE", "POLYLINE")]:
        if not bool(getattr(poli, "closed", False)):
            raise RuntimeError("PLASMA: la polilínea compensada no quedó cerrada.")
        # No se comprueba is_simple aquí: Clipper2 devuelve contornos densos donde
        # el ruido flotante puede marcar falsas auto-intersecciones. El área y la
        # bbox ya verifican el resultado; los lazos reales quedarían fuera del
        # bbox esperado y se rechazarían arriba.

    cadenas = [e for e in out_outer if e.dxftype() in ("LINE", "ARC")]
    if not cadenas:
        return
    grupos = _group_connected_cut_entities(cadenas)
    if len(grupos) != 1:
        raise RuntimeError(
            f"PLASMA: el contorno compensado quedó partido en {len(grupos)} tramos."
        )
    ordenadas = _order_connected_entities(grupos[0], tol=max(abs(off_dxf) * 0.5, 1e-3))
    if not ordenadas or len(ordenadas) != len(grupos[0]):
        raise RuntimeError("PLASMA: el contorno compensado no forma una cadena cerrada.")
    ring = ring_from_specs(specs_from_dxf_entities([ent for ent, _rev in ordenadas]))
    if len(ring) < 4 or math.dist(ring[0], ring[-1]) > max(abs(off_dxf) * 0.5, 1e-3):
        raise RuntimeError("PLASMA: el contorno compensado quedó abierto.")
    if not ring_is_simple(ring):
        raise RuntimeError(
            "PLASMA: el contorno compensado se auto-intersecta (lazos de esquina)."
        )


def compensate_dxf_for_plasma(
    input_dxf: Path,
    output_dxf: Path,
    *,
    offset_mm: float,
    outer_layers: Sequence[str] = OUTER_LAYERS_DEFAULT,
    inner_layers: Sequence[str] = INNER_LAYERS_DEFAULT,
) -> dict:
    """Compensa DXF plasma con OFFSET OCCT exacto; falla cerrado sin degradar."""
    from modules.plasma_offset2d import PLASMA_OFFSET_ALGO_VERSION

    doc = ezdxf.readfile(str(input_dxf))
    msp = doc.modelspace()

    outer_set = _normalize_layers(outer_layers)
    inner_set = _normalize_layers(inner_layers)

    insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    if insunits == 4:
        unit_to_mm = 1.0
    elif insunits == 5:
        unit_to_mm = 10.0
    elif insunits == 1:
        unit_to_mm = 25.4
    else:
        unit_to_mm = 25.4
    off_dxf = float(offset_mm) / unit_to_mm

    exact = _compensate_dxf_occt_exact(
        doc,
        off_dxf=off_dxf,
        outer_set=outer_set,
        inner_set=inner_set,
    )
    changed = int(exact["changed"])
    skipped = int(exact["skipped"])
    circles = int(exact["circles"])
    backend = str(exact["backend"])
    total_targets = int(exact["total_targets"])
    if changed <= 0:
        raise RuntimeError("OCCT OFFSET no encontró un contorno plasma compensable.")

    output_dxf.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(output_dxf))
    try:
        _verificar_dxf_compensado(
            ezdxf.readfile(str(input_dxf)),
            ezdxf.readfile(str(output_dxf)),
            off_dxf=off_dxf,
            outer_set=outer_set,
            inner_set=inner_set,
        )
    except Exception:
        try:
            output_dxf.unlink()
        except OSError:
            pass
        raise
    return {
        "changed": changed,
        "skipped": skipped,
        "total_targets": total_targets,
        "circles": circles,
        "offset_dxf": off_dxf,
        "unit_to_mm": unit_to_mm,
        "backend": backend,
        "algo": PLASMA_OFFSET_ALGO_VERSION,
    }


def _collect_inputs(explicit_files: Sequence[str], input_dir: Optional[str]) -> List[Path]:
    files: List[Path] = []
    for p in explicit_files:
        px = Path(p)
        if px.is_file() and px.suffix.lower() == ".dxf":
            files.append(px)

    if input_dir:
        base = Path(input_dir)
        if base.is_dir():
            files.extend(sorted(base.rglob("*.dxf")))

    # deduplicar preservando orden
    seen = set()
    uniq: List[Path] = []
    for f in files:
        k = str(f.resolve()).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def _normalize_decimal_str(value: str) -> Optional[str]:
    try:
        return str(float(value))
    except Exception:
        return None


def _extract_caliber_candidates_from_text(text: str) -> List[str]:
    """
    Extrae posibles calibres en texto de nombre/ruta:
      - NESTING_0.375_...
      - Cal 0.375 / CAL_0.375
      - THK:0.375
    """
    out: List[str] = []
    if not text:
        return out

    patterns = (
        r"NESTING[_\-\s]*([0-9]+(?:\.[0-9]+)?)",
        r"SOBRANTE[_\-\s]*([0-9]+(?:\.[0-9]+)?)",
        r"\bCAL(?:IBRE)?[_\-\s:]*([0-9]+(?:\.[0-9]+)?)\b",
        r"\bTHK[_\-\s:]*([0-9]+(?:\.[0-9]+)?)\b",
    )
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            val = _normalize_decimal_str(m.group(1))
            if val is not None:
                out.append(val)

    # Fallback genérico:
    # captura decimales con formato de espesor en el nombre (ej. "_0.375_", " 0.059 ", "-0.25-")
    # evitando confundir con enteros de dimensiones como 48 x 120.
    for m in re.finditer(r"(?<![0-9])([0-9]+\.[0-9]+)(?![0-9])", text):
        val = _normalize_decimal_str(m.group(1))
        if val is not None:
            out.append(val)

    # dedup preservando orden
    seen = set()
    uniq: List[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def _extract_caliber_candidates(path: Path, thk_probe: Optional[float] = None) -> List[str]:
    vals = _extract_caliber_candidates_from_text(path.name)
    if not vals:
        vals = _extract_caliber_candidates_from_text(str(path))
    if thk_probe is not None:
        thk_str = _normalize_decimal_str(str(thk_probe))
        if thk_str and thk_str not in vals:
            vals.append(thk_str)
    return vals


def _filter_inputs_by_caliber(inputs: Sequence[Path], caliber_filter: Optional[str]) -> Tuple[List[Path], List[str]]:
    """
    caliber_filter admite:
      - un valor: "0.25"
      - múltiples: "0.25,0.375,0.5"
    """
    logs: List[str] = []
    if not caliber_filter or not str(caliber_filter).strip():
        return list(inputs), logs

    wanted_raw = [x.strip() for x in str(caliber_filter).split(",") if x.strip()]
    wanted: set = set()
    for w in wanted_raw:
        try:
            wanted.add(str(float(w)))
        except Exception:
            wanted.add(w)

    selected: List[Path] = []
    skipped_no_cal = 0
    skipped_not_match = 0
    matched_by_name_or_path = 0
    matched_by_thk = 0

    for p in inputs:
        # 1) Intento rápido por nombre/ruta
        candidates = _extract_caliber_candidates(p)
        if candidates:
            if any(c in wanted for c in candidates):
                selected.append(p)
                matched_by_name_or_path += 1
            else:
                skipped_not_match += 1
            continue

        # 2) Fallback leyendo THK del DXF cuando no se pudo inferir por nombre/ruta
        thk = infer_thickness_from_exported_dxf(p)
        candidates = _extract_caliber_candidates(p, thk_probe=thk)
        if not candidates:
            skipped_no_cal += 1
            continue
        if any(c in wanted for c in candidates):
            selected.append(p)
            matched_by_thk += 1
        else:
            skipped_not_match += 1

    logs.append(f"[INFO] Filtro de calibre aplicado: {', '.join(sorted(wanted))}")
    logs.append(
        f"[INFO] Seleccionados por calibre: {len(selected)} | "
        f"sin calibre detectable: {skipped_no_cal} | no coinciden: {skipped_not_match}"
    )
    logs.append(
        f"[INFO] Coincidencia por nombre/ruta: {matched_by_name_or_path} | "
        f"coincidencia por THK: {matched_by_thk}"
    )
    return selected, logs


def _find_nesting_dxf_anchor(path: Path) -> Optional[int]:
    parts_upper = [p.upper() for p in path.parts]
    for i in range(len(parts_upper) - 1):
        pair = (parts_upper[i], parts_upper[i + 1])
        if any(pair == (a.upper(), b.upper()) for a, b in NESTING_DXF_SEGMENTS):
            return i
    return None


def _infer_robot_plasma_output(src: Path) -> Optional[Path]:
    """
    Convierte una ruta de DXF de nesting a la ruta equivalente de ROBOT PLASMA/DXF.
    Ejemplo:
      .../ROBOT LASER + MINI NEST/DXF/file.dxf
      -> .../ROBOT PLASMA/DXF/file.dxf
    """
    idx = _find_nesting_dxf_anchor(src)
    if idx is None:
        return None

    parts = list(src.parts)
    parts[idx] = "ROBOT PLASMA"
    parts[idx + 1] = "DXF"
    return Path(*parts)


def infer_thickness_from_exported_dxf(input_dxf: Path) -> Optional[float]:
    """
    Intenta leer THK desde el texto cabecera exportado por nesting:
    "... | THK:<valor> | ..."
    """
    try:
        doc = ezdxf.readfile(str(input_dxf))
        msp = doc.modelspace()
        pattern = re.compile(r"THK\s*:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
        for ent in msp:
            t = ent.dxftype()
            if t == "TEXT":
                value = str(ent.dxf.text or "")
            elif t == "MTEXT":
                value = str(ent.text or "")
            else:
                continue
            m = pattern.search(value)
            if m:
                return float(m.group(1))
    except Exception:
        return None
    return None


def run_compensation_batch(
    inputs: Sequence[Path],
    *,
    thickness_in: Optional[float],
    dest_dir: Optional[Path] = None,
    offset_mm: Optional[float] = None,
    suffix: str = "",
    auto_specs_from_dxf: bool = True,
    caliber_filter: Optional[str] = None,
) -> Tuple[int, int, List[str]]:
    logs: List[str] = []
    fixed_offset = float(offset_mm) if offset_mm is not None else None
    inputs_filtered, filter_logs = _filter_inputs_by_caliber(inputs, caliber_filter)
    logs.extend(filter_logs)
    inputs = inputs_filtered

    logs.append(f"[INFO] Archivos de entrada: {len(inputs)}")
    if fixed_offset is not None:
        logs.append(f"[INFO] Offset manual fijo (mm): {fixed_offset:.4f}")
    elif auto_specs_from_dxf:
        logs.append("[INFO] Modo automático: espesor/offset leídos de cada DXF de nesting")
    else:
        logs.append(f"[INFO] Espesor manual (in): {thickness_in}")
    logs.append(
        f"[INFO] Destino: {dest_dir if dest_dir else 'AUTO (ROBOT PLASMA/DXF desde ruta nesting)'}"
    )

    ok = 0
    fail = 0
    for src in inputs:
        if fixed_offset is not None:
            file_offset_mm = fixed_offset
            file_thk = None
        elif auto_specs_from_dxf:
            file_thk = infer_thickness_from_exported_dxf(src)
            if file_thk is None:
                if thickness_in is None:
                    logs.append(
                        f"[FAIL] {src}: no se pudo leer THK del DXF y no hay espesor manual fallback."
                    )
                    fail += 1
                    continue
                file_thk = float(thickness_in)
            file_offset_mm = compute_plasma_offset_mm(file_thk)
        else:
            if thickness_in is None:
                logs.append("[FAIL] Falta espesor manual para modo no automático.")
                fail += 1
                continue
            file_thk = float(thickness_in)
            file_offset_mm = compute_plasma_offset_mm(file_thk)

        out_name = f"{src.stem}{suffix}.dxf" if suffix else src.name
        if dest_dir:
            out_path = Path(dest_dir) / out_name
        else:
            inferred = _infer_robot_plasma_output(src)
            if inferred is None:
                logs.append(
                    f"[FAIL] {src}: no se pudo inferir ruta nesting. "
                    "Usa destino manual o selecciona DXF en estructura nesting."
                )
                fail += 1
                continue
            out_path = inferred.with_name(out_name)

        try:
            stats = compensate_dxf_for_plasma(
                src,
                out_path,
                offset_mm=file_offset_mm,
                outer_layers=OUTER_LAYERS_DEFAULT,
                inner_layers=INNER_LAYERS_DEFAULT,
            )
            logs.append(
                f"[OK] {src.name} -> {out_path} | "
                f"thk={file_thk if file_thk is not None else '-'} in "
                f"offset={file_offset_mm:.4f} mm "
                f"targets={stats['total_targets']} changed={stats['changed']} skipped={stats['skipped']}"
            )
            ok += 1
        except Exception as exc:
            logs.append(f"[FAIL] {src}: {exc}")
            fail += 1

    logs.append(f"[DONE] OK={ok} FAIL={fail}")
    return ok, fail, logs


if HAS_QT:
    class PlasmaCompensatorDialog(QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Forzar DXF Compensados - ROBOT PLASMA")
            self.resize(860, 620)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

            self.input_dir_edit = QLineEdit()
            self.selected_files: List[Path] = []
            self.dest_dir_edit = QLineEdit()
            self.suffix_edit = QLineEdit("")
            self.offset_edit = QLineEdit("")
            self.caliber_filter_edit = QLineEdit("")
            self.auto_specs_check = QCheckBox("Usar especificaciones automáticas de nesting (THK del DXF)")
            self.auto_specs_check.setChecked(True)
            self.thickness_spin = QDoubleSpinBox()
            self.thickness_spin.setDecimals(4)
            self.thickness_spin.setRange(0.001, 10.0)
            self.thickness_spin.setSingleStep(0.0625)
            self.thickness_spin.setValue(0.5)
            self.log = QTextEdit()
            self.log.setReadOnly(True)

            self._build_ui()

        def _build_ui(self):
            layout = QVBoxLayout(self)

            title = QLabel("Compensación forzada de DXF de nesting para ROBOT PLASMA")
            title.setStyleSheet("font-size: 16px; font-weight: 700;")
            layout.addWidget(title)

            desc = QLabel(
                "Selecciona carpeta de DXF de nesting. Si no defines destino, se infiere "
                "automáticamente la ruta ROBOT PLASMA/DXF por cada archivo."
            )
            desc.setWordWrap(True)
            layout.addWidget(desc)

            grid = QGridLayout()
            grid.addWidget(QLabel("Carpeta DXF origen:"), 0, 0)
            grid.addWidget(self.input_dir_edit, 0, 1)
            btn_in = QPushButton("Examinar...")
            btn_in.clicked.connect(self._pick_input_dir)
            grid.addWidget(btn_in, 0, 2)

            grid.addWidget(QLabel("DXF específicos (opcional):"), 1, 0)
            self.files_label = QLabel("0 archivos seleccionados")
            grid.addWidget(self.files_label, 1, 1)
            btn_files = QPushButton("Seleccionar DXF...")
            btn_files.clicked.connect(self._pick_files)
            grid.addWidget(btn_files, 1, 2)

            grid.addWidget(QLabel("Destino manual (opcional):"), 2, 0)
            grid.addWidget(self.dest_dir_edit, 2, 1)
            btn_out = QPushButton("Examinar...")
            btn_out.clicked.connect(self._pick_dest_dir)
            grid.addWidget(btn_out, 2, 2)

            grid.addWidget(self.auto_specs_check, 3, 1)
            self.auto_specs_check.toggled.connect(self._update_manual_inputs_enabled)

            grid.addWidget(QLabel("Espesor fallback (in):"), 4, 0)
            grid.addWidget(self.thickness_spin, 4, 1)

            grid.addWidget(QLabel("Offset manual mm (opcional):"), 5, 0)
            grid.addWidget(self.offset_edit, 5, 1)

            grid.addWidget(QLabel("Sufijo de salida (opcional):"), 6, 0)
            grid.addWidget(self.suffix_edit, 6, 1)
            grid.addWidget(QLabel("Filtrar por calibre en nombre (opcional):"), 7, 0)
            grid.addWidget(self.caliber_filter_edit, 7, 1)

            layout.addLayout(grid)
            self._update_manual_inputs_enabled()

            btns = QHBoxLayout()
            run_btn = QPushButton("Generar DXF compensados")
            run_btn.clicked.connect(self._run)
            btns.addWidget(run_btn)

            clear_btn = QPushButton("Limpiar log")
            clear_btn.clicked.connect(lambda: self.log.clear())
            btns.addWidget(clear_btn)
            layout.addLayout(btns)

            layout.addWidget(self.log)

        def _pick_input_dir(self):
            d = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta origen DXF")
            if d:
                self.input_dir_edit.setText(d)

        def _pick_dest_dir(self):
            d = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta destino")
            if d:
                self.dest_dir_edit.setText(d)

        def _pick_files(self):
            files, _ = QFileDialog.getOpenFileNames(
                self,
                "Seleccionar DXF de nesting",
                "",
                "DXF (*.dxf)",
            )
            if files:
                self.selected_files = [Path(f) for f in files]
                self.files_label.setText(f"{len(self.selected_files)} archivos seleccionados")

        def _update_manual_inputs_enabled(self):
            auto = self.auto_specs_check.isChecked()
            # Espesor fallback sigue habilitado incluso en automático.
            self.offset_edit.setEnabled(True)
            self.thickness_spin.setEnabled(True)
            if auto:
                self.thickness_spin.setToolTip("Fallback si algún DXF no trae THK en cabecera.")
            else:
                self.thickness_spin.setToolTip("Espesor manual para todos los DXF.")

        def _run(self):
            in_dir = self.input_dir_edit.text().strip()
            if not in_dir and not self.selected_files:
                QMessageBox.warning(self, "Falta origen", "Selecciona carpeta o DXF específicos.")
                return

            inputs = _collect_inputs([str(p) for p in self.selected_files], in_dir)
            if not inputs:
                QMessageBox.warning(self, "Sin DXF", "No se encontraron DXF en la carpeta seleccionada.")
                return

            out_dir = self.dest_dir_edit.text().strip() or None
            suffix = self.suffix_edit.text().strip()
            caliber_filter = self.caliber_filter_edit.text().strip()
            thickness = float(self.thickness_spin.value())
            auto_specs = self.auto_specs_check.isChecked()

            offset_text = self.offset_edit.text().strip()
            offset = None
            if offset_text:
                try:
                    offset = float(offset_text)
                except ValueError:
                    QMessageBox.warning(self, "Offset inválido", "El offset manual debe ser numérico.")
                    return

            ok, fail, logs = run_compensation_batch(
                inputs,
                thickness_in=thickness,
                dest_dir=Path(out_dir) if out_dir else None,
                offset_mm=offset,
                suffix=suffix,
                auto_specs_from_dxf=auto_specs,
                caliber_filter=caliber_filter,
            )
            self.log.append("\n".join(logs) + "\n")

            if fail == 0:
                QMessageBox.information(self, "Proceso completo", f"DXF compensados generados: {ok}")
            else:
                QMessageBox.warning(
                    self,
                    "Proceso con fallos",
                    f"Completado con errores.\nOK={ok} | FAIL={fail}\nRevisa el log.",
                )


def run_gui() -> None:
    if not HAS_QT:
        raise RuntimeError("PySide6 no está disponible para abrir la interfaz gráfica.")
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = PlasmaCompensatorDialog()
    dlg.show()
    app.exec()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Genera DXF compensados para PLASMA a partir de DXF ya exportados. "
            "Usa la misma regla de offset del exportador actual."
        )
    )
    parser.add_argument("--dxf", action="append", default=[], help="Ruta a DXF individual (repetible).")
    parser.add_argument("--input-dir", help="Carpeta origen para buscar DXF recursivamente.")
    parser.add_argument("--gui", action="store_true", help="Abrir interfaz gráfica.")
    parser.add_argument(
        "--auto-specs",
        action="store_true",
        default=False,
        help="Leer THK desde cada DXF de nesting (fallback en --thickness-in si aplica).",
    )
    parser.add_argument(
        "--caliber-filter",
        default=None,
        help=(
            "Filtra DXF por calibre leído del nombre tipo NESTING_<calibre>_... "
            "Ej: 0.25 o 0.25,0.375"
        ),
    )
    parser.add_argument(
        "--dest-dir",
        required=False,
        default=None,
        help=(
            "Carpeta destino (por ejemplo .../ROBOT PLASMA/DXF). "
            "Si se omite, se infiere automáticamente desde la estructura de nesting."
        ),
    )
    parser.add_argument(
        "--thickness-in",
        type=float,
        required=False,
        default=None,
        help="Espesor en pulgadas para calcular compensación plasma.",
    )
    parser.add_argument(
        "--offset-mm",
        type=float,
        default=None,
        help="Offset manual en mm. Si se omite, se calcula desde --thickness-in.",
    )
    parser.add_argument(
        "--suffix",
        default="",
        help=(
            "Sufijo para nombre de archivo de salida (antes de .dxf). "
            "Por defecto vacío para conservar nombre original."
        ),
    )
    args = parser.parse_args()

    # Si no se pasa nada, abrir GUI por defecto (uso operativo tipo despachador).
    if args.gui or len(sys.argv) == 1:
        run_gui()
        return

    if args.thickness_in is None:
        if not args.auto_specs and args.offset_mm is None:
            raise SystemExit(
                "En modo consola debes indicar --thickness-in, o usar --auto-specs, o definir --offset-mm."
            )

    inputs = _collect_inputs(args.dxf, args.input_dir)
    if not inputs:
        raise SystemExit("No se encontraron DXF de entrada.")

    ok, fail, logs = run_compensation_batch(
        inputs,
        thickness_in=args.thickness_in,
        dest_dir=Path(args.dest_dir) if args.dest_dir else None,
        offset_mm=args.offset_mm,
        suffix=args.suffix,
        auto_specs_from_dxf=args.auto_specs,
        caliber_filter=args.caliber_filter,
    )
    print("\n".join(logs))
    if fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
