"""Renderizador SVG ligero para inspeccionar placas de nesting reales."""
from __future__ import annotations

import hashlib
import html
from pathlib import Path
from typing import Any


_PALETTE = (
    "#0f766e",
    "#2563eb",
    "#7c3aed",
    "#c2410c",
    "#b91c1c",
    "#4d7c0f",
    "#0369a1",
    "#a21caf",
    "#9a3412",
    "#475569",
)


def _as_point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _path_data(rings: list, plate_h_mm: float) -> tuple[str, tuple[float, float, float, float] | None]:
    commands: list[str] = []
    points: list[tuple[float, float]] = []
    for ring in rings or []:
        converted = [point for raw in ring if (point := _as_point(raw)) is not None]
        if len(converted) < 3:
            continue
        points.extend(converted)
        first_x, first_y = converted[0]
        commands.append(f"M {first_x:.3f} {plate_h_mm - first_y:.3f}")
        for x, y in converted[1:]:
            commands.append(f"L {x:.3f} {plate_h_mm - y:.3f}")
        commands.append("Z")
    if not points:
        return "", None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return " ".join(commands), (min(xs), min(ys), max(xs), max(ys))


def _colour(name: str) -> str:
    digest = hashlib.blake2s(name.encode("utf-8", "replace"), digest_size=2).digest()
    return _PALETTE[int.from_bytes(digest, "big") % len(_PALETTE)]


def write_sheet_svg(
    hoja: dict[str, Any],
    *,
    plate_w_mm: float,
    plate_h_mm: float,
    output: Path,
    title: str,
) -> Path:
    """Escribe una placa navegable: hover revela nombre y geometría real."""
    placed = list(hoja.get("piezas") or [])
    elements: list[str] = []
    labels: list[str] = []
    for index, piece in enumerate(placed, start=1):
        name = str(piece.get("nombre") or f"Pieza {index}")
        path, bounds = _path_data(list(piece.get("poligonos") or []), plate_h_mm)
        if not path:
            continue
        safe_name = html.escape(name, quote=True)
        elements.append(
            f'<path d="{path}" fill="{_colour(name)}" fill-opacity="0.72" '
            f'stroke="#0f172a" stroke-width="2.2" fill-rule="evenodd">'
            f"<title>#{index} · {safe_name}</title></path>"
        )
        if bounds:
            min_x, min_y, max_x, max_y = bounds
            cx = (min_x + max_x) / 2.0
            cy = plate_h_mm - ((min_y + max_y) / 2.0)
            if (max_x - min_x) >= 28.0 and (max_y - min_y) >= 20.0:
                labels.append(
                    f'<text x="{cx:.2f}" y="{cy:.2f}" class="piece-label">{index}</text>'
                )

    output.parent.mkdir(parents=True, exist_ok=True)
    safe_title = html.escape(title, quote=True)
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="760"
     viewBox="-100 -160 {plate_w_mm + 200:.3f} {plate_h_mm + 260:.3f}">
  <style>
    .heading {{ font: 72px sans-serif; fill: #0f172a; font-weight: 700; }}
    .meta {{ font: 42px sans-serif; fill: #334155; }}
    .piece-label {{
      font: 31px sans-serif; fill: #ffffff; font-weight: 700; text-anchor: middle;
      dominant-baseline: middle; paint-order: stroke; stroke: #0f172a; stroke-width: 7px;
    }}
  </style>
  <rect x="0" y="0" width="{plate_w_mm:.3f}" height="{plate_h_mm:.3f}"
        fill="#f8fafc" stroke="#0f172a" stroke-width="8"/>
  <text x="0" y="-92" class="heading">{safe_title}</text>
  <text x="0" y="-35" class="meta">{len(placed)} piezas · hover sobre una pieza para ver su nombre · número = índice</text>
  <g>{''.join(elements)}</g>
  <g>{''.join(labels)}</g>
</svg>
"""
    output.write_text(svg, encoding="utf-8")
    return output
