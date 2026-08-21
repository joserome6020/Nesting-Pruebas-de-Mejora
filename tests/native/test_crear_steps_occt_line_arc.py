"""Candado: nest DXF LINE/ARC → STEP vía despachador OCCT (sin reescribir DXF).

Bug 2026-08-21 S.W.O 27 X1: FreeCAD SKIP OUTER:0 con CUT_OUTER en LINE/ARC.
OCCT une bordes en memoria (_stitch_edges_to_closed_wires); el DXF en disco
sigue 1:1 (exactitud plasma/láser).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))


def _write_line_arc_nest_dxf(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.layers.add("CUT_OUTER")
    doc.layers.add("CUT_INNER")
    msp = doc.modelspace()
    # Rectángulo 10×5 in como LINE (como export nest ANS), no LWPOLYLINE.
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((10, 0), (10, 5), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((10, 5), (0, 5), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((0, 5), (0, 0), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_circle((5, 2.5), 0.5, dxfattribs={"layer": "CUT_INNER"})
    doc.saveas(str(path))


def main() -> None:
    from despachador_nocturno import (
        _usar_occt_para_crear_steps,
        procesar_familia,
        resolver_destinos_step,
    )

    assert _usar_occt_para_crear_steps() is True

    with tempfile.TemporaryDirectory() as tmp:
        nest = Path(tmp) / "NESTING" / "CAMA LASER SIN MINI NEST"
        dxf_dir = nest / "DXF"
        step_dir = nest / "STEP"
        dxf_dir.mkdir(parents=True)
        step_dir.mkdir(parents=True)
        dxf_path = dxf_dir / "SWO-027_0.1875_SWO-027-H1.dxf"
        _write_line_arc_nest_dxf(dxf_path)

        # Exactitud: el archivo en disco no debe mutar tras convertir.
        before = dxf_path.read_bytes()

        destinos = resolver_destinos_step(str(step_dir))
        familia = {
            "nombre": "CAMA LASER SIN MINI NEST",
            "tipo": "CAMA_LASER",
            "ruta_base": str(nest),
            "dxf_dir": str(dxf_dir),
            "step_root": str(step_dir),
            "destinos_step": destinos,
        }
        resultados = procesar_familia(familia, thk_mm=4.7625, plasma_off_mm=0.0)
        assert resultados, resultados
        assert all(ok for _, ok in resultados), resultados

        after = dxf_path.read_bytes()
        assert after == before, "El DXF nest no debe reescribirse al crear STEP"

        steps = list(step_dir.glob("*.step"))
        assert steps, "Debió generarse al menos un .step"
        assert steps[0].stat().st_size > 512, steps[0].stat().st_size

        # Join en memoria: collect ve wires cerrados desde LINE.
        sys.path.insert(0, str(RAIZ / "CAD (OCCT)"))
        from engine.dxf_to_step import collect_dxf_nest

        geom = collect_dxf_nest(dxf_path)
        assert len(geom.outer_wires) >= 1, len(geom.outer_wires)
        assert len(geom.inner_wires) >= 1, len(geom.inner_wires)

    print("SMOKE OK")


if __name__ == "__main__":
    main()
