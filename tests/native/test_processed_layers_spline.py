"""Candado 2026-08-26 — AutoDXF Processed Files acepta SPLINE (Inventor flat pattern).

Bug real: ITEM 2 del tanque SW_1PH-0081-0136-0137-0110-TANK exportaba el perfil
como SPLINE en IV_OUTER_PROFILE / IV_INTERIOR_PROFILES. ProcesadorDXF ignoraba
SPLINE → Processed Files vacío o con LWPOLYLINE degeneradas → PARTS omitía el DXF.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _crear_dxf_spline_inventor(ruta: Path) -> None:
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    doc.layers.new("IV_OUTER_PROFILE", dxfattribs={"color": 1})
    doc.layers.new("IV_INTERIOR_PROFILES", dxfattribs={"color": 3})

    layer_out = {"layer": "IV_OUTER_PROFILE"}
    # Rectángulo redondeado aproximado con SPLINE (patrón Inventor ITEM 2).
    outer = [
        (0.0, 0.5),
        (0.5, 0.0),
        (3.5, 0.0),
        (4.0, 0.5),
        (4.0, 1.5),
        (3.5, 2.0),
        (0.5, 2.0),
        (0.0, 1.5),
        (0.0, 0.5),
    ]
    msp.add_spline(outer, dxfattribs=layer_out)

    inner = [
        (1.5, 0.75),
        (2.0, 1.0),
        (2.5, 0.75),
        (2.5, 1.25),
        (2.0, 1.5),
        (1.5, 1.25),
        (1.5, 0.75),
    ]
    msp.add_spline(inner, dxfattribs={"layer": "IV_INTERIOR_PROFILES"})

    # Fragmento LINE que debe unirse al SPLINE outer (como en DXF reales capa 0).
    msp.add_line((4.0, 0.5), (4.0, 1.5), dxfattribs=layer_out)

    doc.saveas(str(ruta))


def test_procesador_dxf_spline_inventor():
    from modules.nesting_engine.geometry_parser import recuperar_geometria_robusta_detalle
    from modules.processed_layers import ProcesadorDXF

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "ITEM 2 - TEST, A 36, QTY 1, Cal 0.105.dxf"
        out_dir = Path(tmp) / "Processed Files"
        out_dir.mkdir()
        dst = out_dir / src.name

        _crear_dxf_spline_inventor(src)

        proc = ProcesadorDXF()
        assert proc.limpiar_archivo(str(src), str(dst)) is True
        assert dst.is_file()

        doc_out = __import__("ezdxf").readfile(str(dst))
        ents = list(doc_out.modelspace())
        assert len(ents) >= 1, "Processed Files no debe quedar vacío con SPLINE"

        poly, _marks, err = recuperar_geometria_robusta_detalle(str(dst))
        assert err is None, err
        assert poly is not None and not poly.is_empty
        assert float(poly.area or 0.0) > 1.0

        minx, miny, maxx, maxy = poly.bounds
        w_in = float(maxx - minx)
        h_in = float(maxy - miny)
        assert w_in > 2.0 and h_in > 1.0, f"contorno degenerado {w_in}x{h_in} in"


def test_procesador_dxf_cobre_omit_marcaje():
    """Switch cu_sin_marcaje: Processed Files sin capa MARK ni stick."""
    import ezdxf
    from modules.nesting_engine.geometry_parser import recuperar_geometria_robusta_detalle
    from modules.processed_layers import ProcesadorDXF

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "GENE-FCU-2-105, CU, QTY 1, Cal 0.25.dxf"
        out_dir = Path(tmp) / "Processed Files"
        out_dir.mkdir()
        dst = out_dir / src.name

        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 1
        msp = doc.modelspace()
        doc.layers.new("IV_OUTER_PROFILE", dxfattribs={"color": 1})
        msp.add_lwpolyline(
            [(0, 0), (10, 0), (10, 5), (0, 5)],
            close=True,
            dxfattribs={"layer": "IV_OUTER_PROFILE"},
        )
        doc.saveas(str(src))

        proc = ProcesadorDXF()
        assert proc.limpiar_archivo(str(src), str(dst), omit_marcaje=True) is True
        doc_out = ezdxf.readfile(str(dst))
        layers = {
            str(getattr(e.dxf, "layer", "") or "").upper()
            for e in doc_out.modelspace()
        }
        assert "MARK" not in layers
        _poly, marks, err = recuperar_geometria_robusta_detalle(str(dst))
        assert err is None
        assert marks is None or marks.is_empty


if __name__ == "__main__":
    test_procesador_dxf_spline_inventor()
    test_procesador_dxf_cobre_omit_marcaje()
    print("OK processed_layers SPLINE")
