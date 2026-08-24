"""Pre-procesa DXF densos de cobre para evitar que importDXF de FreeCAD se cuelgue.

FreeCAD 1.x en modo GUI suele congelarse al importar >~6k entidades de marcaje.
Se genera una copia sin capa MARK y un JSON con los segmentos de marcas.
"""
from __future__ import annotations

import json
import os
from typing import Any

MARK_LAYERS = frozenset({"MARK", "ETCH", "TEXT"})
# H12 (~4744 marcas) importa bien; a partir de ~6000 FreeCAD suele colgarse.
SLIM_MARK_THRESHOLD = 6000


def _entity_layer(entity) -> str:
    return str(getattr(entity.dxf, "layer", "") or "").upper().strip()


def _is_mark_entity(entity) -> bool:
    return _entity_layer(entity) in MARK_LAYERS


def _segment_from_line(entity) -> list[float]:
    return [
        float(entity.dxf.start.x),
        float(entity.dxf.start.y),
        float(entity.dxf.end.x),
        float(entity.dxf.end.y),
    ]


def _segments_from_lwpolyline(entity) -> list[list[float]]:
    """Convierte LWPOLYLINE (incl. bulge/arcos) a segmentos 1:1 con la geometría DXF."""
    try:
        from ezdxf.path import make_path

        out: list[list[float]] = []
        for path in (make_path(entity),):
            prev = None
            for point in path.flattening(distance=0.01):
                xy = (float(point.x), float(point.y))
                if prev is not None:
                    out.append([prev[0], prev[1], xy[0], xy[1]])
                prev = xy
        if out:
            return out
    except Exception:
        pass

    pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
    out = []
    for i in range(len(pts) - 1):
        out.append([pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]])
    if entity.closed and len(pts) > 2:
        out.append([pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]])
    return out


def _segments_from_arc(entity, *, segments: int = 16) -> list[list[float]]:
    center = entity.dxf.center
    radius = float(entity.dxf.radius)
    start = float(entity.dxf.start_angle) % 360.0
    end = float(entity.dxf.end_angle) % 360.0
    if end <= start:
        end += 360.0
    import math

    out: list[list[float]] = []
    steps = max(4, int(segments))
    prev = None
    for i in range(steps + 1):
        ang = math.radians(start + (end - start) * (i / steps))
        xy = (float(center.x) + radius * math.cos(ang), float(center.y) + radius * math.sin(ang))
        if prev is not None:
            out.append([prev[0], prev[1], xy[0], xy[1]])
        prev = xy
    return out


def _collect_mark_segments(msp) -> tuple[int, list[list[float]], list[str]]:
    mark_count = 0
    segments: list[list[float]] = []
    unhandled: list[str] = []
    for entity in msp:
        if not _is_mark_entity(entity):
            continue
        mark_count += 1
        dxftype = entity.dxftype()
        try:
            if dxftype == "LINE":
                segments.append(_segment_from_line(entity))
            elif dxftype in ("LWPOLYLINE", "POLYLINE"):
                segments.extend(_segments_from_lwpolyline(entity))
            elif dxftype == "ARC":
                segments.extend(_segments_from_arc(entity))
            else:
                unhandled.append(dxftype)
        except Exception:
            unhandled.append(dxftype or "?")
    return mark_count, segments, unhandled


def _slim_paths(dxf_path: str, cache_dir: str) -> tuple[str, str]:
    base = os.path.splitext(os.path.basename(dxf_path))[0]
    slim_path = os.path.join(cache_dir, f"_slim_{base}.dxf")
    mark_json = os.path.join(cache_dir, f"_marks_{base}.json")
    return slim_path, mark_json


def _needs_regen(source_path: str, slim_path: str, mark_json: str) -> bool:
    try:
        src_m = os.path.getmtime(source_path)
        if not os.path.isfile(slim_path) or not os.path.isfile(mark_json):
            return True
        return os.path.getmtime(slim_path) < src_m or os.path.getmtime(mark_json) < src_m
    except OSError:
        return True


def prepare_dxf_for_freecad(
    dxf_path: str,
    cache_dir: str,
    *,
    mark_threshold: int = SLIM_MARK_THRESHOLD,
) -> tuple[str, str | None, str]:
    """Devuelve (ruta_import, mark_json|None, nota)."""
    dxf_path = os.path.normpath(str(dxf_path))
    if not os.path.isfile(dxf_path):
        return dxf_path, None, "DXF no encontrado"

    notes: list[str] = []
    try:
        from modules.dxf_join_for_freecad import maybe_join_nest_dxf_for_freecad

        dxf_path, join_note = maybe_join_nest_dxf_for_freecad(dxf_path, cache_dir)
        if join_note and not join_note.startswith("join omitido: ya tiene"):
            notes.append(join_note)
    except Exception as exc:
        notes.append(f"join omitido: {exc}")

    try:
        import ezdxf
    except ImportError:
        note = "; ".join(notes) if notes else "ezdxf no disponible"
        return dxf_path, None, note

    os.makedirs(cache_dir, exist_ok=True)
    slim_path, mark_json = _slim_paths(dxf_path, cache_dir)

    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as exc:
        return dxf_path, None, f"no se pudo leer DXF: {exc}"

    msp = doc.modelspace()
    geom_entities = sum(1 for e in msp if not _is_mark_entity(e))
    mark_count, segments, unhandled = _collect_mark_segments(msp)
    if mark_count < int(mark_threshold):
        note = "; ".join(notes) if notes else f"sin slim ({mark_count} marcas)"
        return dxf_path, None, note

    # Sin slim si no podemos serializar todas las marcas (paridad 1:1 obligatoria).
    if unhandled:
        kinds = ", ".join(sorted(set(unhandled)))
        note = "; ".join(notes + [f"sin slim: marcas no soportadas ({kinds})"])
        return dxf_path, None, note
    if mark_count > 0 and not segments:
        note = "; ".join(notes + ["sin slim: no se pudieron extraer segmentos MARK"])
        return dxf_path, None, note

    if not _needs_regen(dxf_path, slim_path, mark_json):
        note = "; ".join(
            notes + [f"slim cache 1:1 ({mark_count} marcas -> {len(segments)} segmentos)"]
        )
        return slim_path, mark_json, note

    for entity in list(msp):
        if _is_mark_entity(entity):
            msp.delete_entity(entity)

    doc.saveas(slim_path)

    payload: dict[str, Any] = {
        "source": os.path.basename(dxf_path),
        "mark_entities": mark_count,
        "segment_count": len(segments),
        "geometry_entities": geom_entities,
        "parity": "1:1",
        "segments": segments,
    }
    with open(mark_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    remain = sum(1 for _ in msp)
    if remain != geom_entities:
        note = "; ".join(notes + ["sin slim: geometría alterada al quitar MARK"])
        return dxf_path, None, note
    note = "; ".join(
        notes
        + [
            f"slim 1:1: {mark_count} marcas -> {len(segments)} segmentos JSON, "
            f"{remain} entidades geom sin cambio ({os.path.getsize(slim_path) // 1024} KB)"
        ]
    )
    return slim_path, mark_json, note
