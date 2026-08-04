"""Convierte los SVG exactos de nesting en láminas PNG para revisión visual."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

# El render se ejecuta también en sesiones sin escritorio gráfico.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def _render_svg(source: Path, width: int) -> QImage:
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f"SVG no válido: {source}")
    default = renderer.defaultSize()
    source_w = max(1, default.width())
    source_h = max(1, default.height())
    height = max(1, round(width * source_h / source_w))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return image


def _save(image: QImage, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination), "PNG"):
        raise RuntimeError(f"No se pudo guardar {destination}")
    return destination


def _contact_sheet(sources: list[Path], *, width: int) -> QImage:
    columns = 2
    cells = [_render_svg(source, width) for source in sources]
    cell_height = max(image.height() for image in cells)
    rows = (len(cells) + columns - 1) // columns
    canvas = QImage(width * columns, cell_height * rows, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("white"))
    painter = QPainter(canvas)
    for index, image in enumerate(cells):
        x = (index % columns) * width
        y = (index // columns) * cell_height
        painter.drawImage(x, y, image)
    painter.end()
    return canvas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera evidencia visual PNG del nesting")
    parser.add_argument(
        "--layout-dir",
        default="benchmarks/results_real/autodxf_desktop_cal11_layout",
    )
    parser.add_argument(
        "--out-dir",
        default="benchmarks/results_real/autodxf_desktop_cal11_visuals",
    )
    args = parser.parse_args(argv)
    app = QGuiApplication.instance() or QGuiApplication([])
    layout_dir = Path(args.layout_dir)
    out_dir = Path(args.out_dir)
    full_pilot_dir = layout_dir / "lab_pilot"
    full_pilot = sorted(full_pilot_dir.glob("placa_*.svg"))
    if len(full_pilot) != 8:
        raise ValueError(f"Se esperaban 8 placas piloto, se encontraron {len(full_pilot)}.")

    outputs = [
        _save(_render_svg(full_pilot[0], 1600), out_dir / "piloto_placa_01.png"),
        _save(_contact_sheet(full_pilot, width=900), out_dir / "piloto_8_placas.png"),
    ]
    quality_sources = [
        layout_dir / "arga_lab_pilot_placa_01.svg",
        layout_dir / "burke_blf_placa_01.svg",
    ]
    if not all(path.is_file() for path in quality_sources):
        missing = ", ".join(str(path) for path in quality_sources if not path.is_file())
        raise FileNotFoundError(f"Faltan SVG de comparación: {missing}")
    outputs.append(
        _save(
            _contact_sheet(quality_sources, width=1200),
            out_dir / "muestra_12_piloto_vs_burke.png",
        )
    )
    app.processEvents()
    print("\n".join(str(path) for path in outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
