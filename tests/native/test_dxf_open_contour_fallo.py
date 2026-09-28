"""Candado: DXF con contorno abierto (AREA NETA 0) → omitido / no LISTO.

Caso real 2026-09-24: pieza S1-3MT0210CA005 en PARTS con AREA NETA 0.000 in²
seguía en LISTO y DXF NESTEO 21/21 porque el parser inventaba polígonos desde
LINE sueltas (buffer) y la UI no pintaba FALLO sobre omitidos.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _dxf_outer_abierto(ruta: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1  # inches
    msp = doc.modelspace()
    # Fragmentos OUTER sin ciclo cerrado (mismo síntoma: span grande, área neta 0).
    msp.add_line((0.0, 0.0), (77.0, 0.0), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((77.0, 12.7), (0.0, 12.7), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((10.0, 1.0), (10.0, 3.0), dxfattribs={"layer": "CUT_OUTER"})
    doc.saveas(str(ruta))


def _dxf_outer_cerrado(ruta: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    doc.saveas(str(ruta))


def test_contorno_abierto_no_geometria_nest():
    from modules.nesting_engine.geometry_parser import recuperar_geometria_robusta_detalle

    with tempfile.TemporaryDirectory(prefix="arga_open_dxf_") as tmp:
        ruta = Path(tmp) / "OPEN_OUTER.dxf"
        _dxf_outer_abierto(ruta)
        poly, _marks, err = recuperar_geometria_robusta_detalle(str(ruta))
        assert poly is None, f"no debe inventar shell desde LINE abiertas: {poly}"
        assert err, "debe devolver motivo de fallo"


def test_contorno_abierto_auditoria_omite():
    from modules.nesting_engine.dxf_nesting_audit import auditar_lista_partes
    from interface.qt.dxf_part_loader import load_dxf_part

    with tempfile.TemporaryDirectory(prefix="arga_open_audit_") as tmp:
        ruta = Path(tmp) / "S1-3MT0210CA005.dxf"
        _dxf_outer_abierto(ruta)
        model = load_dxf_part(str(ruta))
        assert model is not None
        assert float(model.area_neta or 0.0) <= 1e-6, model.area_neta

        audit = auditar_lista_partes(
            [("S1-3MT0210CA005", "A 36", "1", "0.1046", "LISTO", str(ruta))]
        )
        assert audit["ok"] == 0
        assert len(audit["omitidos"]) == 1
        err = str(audit["omitidos"][0].get("error") or "").lower()
        assert "abierto" in err or "corrupto" in err or "contorno" in err, err


def test_contorno_cerrado_sigue_ok():
    from modules.nesting_engine.dxf_nesting_audit import auditar_lista_partes

    with tempfile.TemporaryDirectory(prefix="arga_closed_audit_") as tmp:
        ruta = Path(tmp) / "OK_RECT.dxf"
        _dxf_outer_cerrado(ruta)
        audit = auditar_lista_partes(
            [("OK_RECT", "A 36", "1", "0.25", "LISTO", str(ruta))]
        )
        assert audit["ok"] == 1, audit
        assert not audit["omitidos"]


if __name__ == "__main__":
    test_contorno_abierto_no_geometria_nest()
    test_contorno_abierto_auditoria_omite()
    test_contorno_cerrado_sigue_ok()
    print("OK test_dxf_open_contour_fallo")
