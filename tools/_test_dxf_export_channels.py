"""Verifica que exportación láser y cobre no mezclen capas DXF."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf

from modules.nest_exporter import export_nest_to_dxf
from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf

COBRE_ONLY = frozenset({"CUT_CU", "BAR_START", "ARGA_META"})
LASER_FORBIDDEN_EMPTY = COBRE_ONLY


def _layer_names(path: str) -> set[str]:
    doc = ezdxf.readfile(path)
    return {str(l.dxf.name) for l in doc.layers}


def test_laser_robot_mini_nest() -> None:
    sheet = {
        "length": 1854.0,
        "width": 914.0,
        "material": "A36",
        "thickness": 0.25,
        "arga_code": "TEST",
    }
    placements = [
        {
            "part_name": "PIEZA-A",
            "outer": [(10, 10), (110, 10), (110, 60), (10, 60)],
            "holes": [[(30, 25), (50, 25), (50, 45), (30, 45)]],
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "laser.dxf")
        export_nest_to_dxf(
            out,
            sheet,
            placements,
            title="ROBOT LASER + MINI NEST",
            canal="ROBOT LASER + MINI NEST",
        )
        layers = _layer_names(out)
        leaked = layers & LASER_FORBIDDEN_EMPTY
        assert not leaked, f"láser: capas cobre en tabla DXF: {sorted(leaked)}"
        assert "CUT_OUTER" in layers and "CUT_INNER" in layers


def test_cobre_largos_con_gap_step() -> None:
    sheet = {
        "length": 6000.0,
        "width": 40.0,
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "con_gap",
        "export_3d_format": "step",
    }
    placements = [
        {
            "part_name": "CU-1",
            "cu_largos_piece": True,
            "outer": [(0, 0), (100, 0), (100, 40), (0, 40)],
            "cu_slice_idx": 0,
            "cu_slice_count": 1,
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "cobre.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            placements,
            title="NESTEOS DE COBRE",
            strict=False,
        )
        layers = _layer_names(out)
        doc = ezdxf.readfile(out)
        msp = doc.modelspace()
        used = {str(e.dxf.layer) for e in msp}
        assert "CUT_CU" not in used, "con_gap STEP: sin CUT_CU"
        assert "CUT_OUTER" in used, "con_gap STEP: contorno en CUT_OUTER"
        assert "BAR_START" in layers, "cobre: falta BAR_START"
        closed = [
            e
            for e in msp
            if str(e.dxf.layer).upper() == "CUT_OUTER"
            and e.dxftype() == "LWPOLYLINE"
            and bool(getattr(e, "closed", False) or e.closed)
        ]
        assert len(closed) >= 1, "STEP: 1 LWPOLYLINE cerrada CUT_OUTER por pieza"


def test_cobre_con_gap_source_dxf_fragmented_strict() -> None:
    """DXF fuente con LINE/ARC sueltos no debe fragmentar CUT_OUTER (STEP)."""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "pieza_fuente.dxf")
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        # Outer fragmentado (como muchos DXF de pieza): 4 LINE en capa 0
        pts = [(0, 0), (100, 0), (100, 40), (0, 40)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            msp.add_line(a, b, dxfattribs={"layer": "0"})
        doc.saveas(src)

        sheet = {
            "length": 6000.0,
            "width": 101.6,
            "modo_largos_cu": True,
            "cu_modo_separacion_barra": "con_gap",
            "export_3d_format": "step",
        }
        placements = [
            {
                "part_name": "CU-SRC",
                "cu_largos_piece": True,
                "prefer_source_dxf": True,
                "ruta": src,
                "outer": [(10, 10), (110, 10), (110, 50), (10, 50)],
                "cu_slice_idx": 0,
                "cu_slice_count": 1,
            },
            {
                "part_name": "CU-SRC-2",
                "cu_largos_piece": True,
                "prefer_source_dxf": True,
                "ruta": src,
                "outer": [(120, 10), (220, 10), (220, 50), (120, 50)],
                "cu_slice_idx": 0,
                "cu_slice_count": 1,
            },
            {
                "part_name": "CU-SRC-3",
                "cu_largos_piece": True,
                "prefer_source_dxf": True,
                "ruta": src,
                "outer": [(230, 10), (330, 10), (330, 50), (230, 50)],
                "cu_slice_idx": 0,
                "cu_slice_count": 1,
            },
        ]
        out = os.path.join(td, "cobre_strict.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            placements,
            title="NESTEOS DE COBRE",
            strict=True,
        )
        msp_out = ezdxf.readfile(out).modelspace()
        closed = [
            e
            for e in msp_out
            if str(e.dxf.layer).upper() == "CUT_OUTER"
            and e.dxftype() == "LWPOLYLINE"
            and bool(getattr(e, "closed", False) or e.closed)
        ]
        loose = [
            e
            for e in msp_out
            if str(e.dxf.layer).upper() == "CUT_OUTER"
            and e.dxftype() in ("LINE", "ARC")
        ]
        assert len(closed) == 3, f"esperadas 3 LWPOLYLINE cerradas, hay {len(closed)}"
        assert not loose, f"CUT_OUTER no debe tener LINE/ARC sueltos ({len(loose)})"


def main() -> int:
    test_laser_robot_mini_nest()
    print("[OK] láser ROBOT LASER + MINI NEST: sin capas cobre fantasma")
    test_cobre_largos_con_gap_step()
    print("[OK] cobre largos (módulo cobre_nest): CUT_OUTER STEP, sin CUT_CU")
    test_cobre_con_gap_source_dxf_fragmented_strict()
    print("[OK] con_gap + DXF fuente fragmentado + strict: 3 LWPOLYLINE CUT_OUTER")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
