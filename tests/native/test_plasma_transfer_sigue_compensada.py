"""Candado 2026-08-19u/v — mudar pieza plasma no dobla el stock en la tabla.

Caso planta: nest ya usa DXF compensado → L/W = 77.37×21.68.
Al Mudar, la tabla no debe pintar 77.50×21.81 (otro +0.125").
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

import ezdxf  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from modules.nesting_engine.manager import MotorNesting  # noqa: E402
from modules.plasma_compensator import (  # noqa: E402
    asegurar_dxf_plasma_compensado,
    compute_plasma_offset_mm,
)


def _dims_tabla_in(pieza: dict, hoja: dict | None = None) -> tuple[float, float]:
    """Misma regla que interface/qt/nesting_graphics.py para L/W de la tabla."""
    hoja = hoja or {}
    pols = pieza.get("poligonos") or []
    xs = [float(t[0]) for t in pols[0]]
    ys = [float(t[1]) for t in pols[0]]
    dx_mm = max(xs) - min(xs)
    dy_mm = max(ys) - min(ys)
    compensada = bool(pieza.get("plasma_compensada_manual"))
    off_pieza = float(
        pieza.get("plasma_offset_mm_manual")
        or hoja.get("plasma_offset_mm_manual")
        or 0.0
    )
    if compensada and off_pieza > 0.0 and not bool(
        pieza.get("plasma_fuente_ya_compensada") or pieza.get("ruta_plasma")
    ):
        dx_mm += 2.0 * off_pieza
        dy_mm += 2.0 * off_pieza
    return max(dx_mm, dy_mm) / 25.4, min(dx_mm, dy_mm) / 25.4


def test_mudar_pieza_ya_compensada_no_dobla_tabla() -> None:
    """Polígono del nest YA creció 0.0625\"/lado; Mudar no suma otro 0.125\"."""
    off_mm = compute_plasma_offset_mm(0.375)
    d_in = off_mm / 25.4
    # Medidas planta: base 77.25×21.56 → compensado 77.37×21.68
    base_l, base_w = 77.25, 21.56
    comp_l, comp_w = base_l + 2.0 * d_in, base_w + 2.0 * d_in

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "P63.dxf"
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 1
        doc.modelspace().add_lwpolyline(
            [(0.0, 0.0), (base_l, 0.0), (base_l, base_w), (0.0, base_w)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        doc.saveas(src)
        out, err = asegurar_dxf_plasma_compensado(src, off_mm)
        assert out and not err, err

        # Nest real: polígonos ya del DXF compensado (mm).
        l_mm, w_mm = comp_l * 25.4, comp_w * 25.4
        pieza = {
            "nombre": "62178-1248-P63",
            "poligonos": [[[0.0, 0.0], [l_mm, 0.0], [l_mm, w_mm], [0.0, w_mm]]],
            "marcas": [],
            "area": l_mm * w_mm,
            "calibre": "0.375",
            "material": "A 36",
            "ruta": str(src),
            "ruta_plasma": str(out),
            "plasma_compensada_manual": True,
            "plasma_offset_mm_manual": float(off_mm),
            "plasma_fuente_ya_compensada": True,
        }
        motor = MotorNesting.__new__(MotorNesting)
        pack = motor._as_pack_piece_visual(pieza)
        assert pack is not None
        assert pack.get("plasma_fuente_ya_compensada") is True

        var = {"poly": pack["poly"], "marks": pack.get("marks")}
        colocada = motor._pieza_colocada_incremental(pack, var, 10.0, 5.0)
        assert colocada is not None
        # Simula el sellado post-incremental de _intentar_colocacion_incremental.
        motor._heredar_identidad_pieza(colocada, pieza)
        colocada["plasma_compensada_manual"] = True
        colocada["plasma_fuente_ya_compensada"] = True
        colocada["ruta_plasma"] = str(out)
        colocada["plasma_offset_mm_manual"] = float(off_mm)

        L, W = _dims_tabla_in(colocada)
        assert abs(L - comp_l) < 0.03, (L, comp_l, "dobló el offset al mudar")
        assert abs(W - comp_w) < 0.03, (W, comp_w)
        assert abs(L - (comp_l + 2.0 * d_in)) > 0.05, "no debe quedar en 77.50"


def test_tabla_no_suma_si_hay_ruta_plasma() -> None:
    """Defensa UI: con ruta_plasma la tabla no vuelve a sumar 2×offset."""
    src = (
        RAIZ / "interface" / "qt" / "nesting_graphics.py"
    ).read_text(encoding="utf-8")
    assert (
        'p.get("plasma_fuente_ya_compensada") or p.get("ruta_plasma")' in src
    )


def test_packer_transfer_reinyecta_plasma() -> None:
    mgr = (RAIZ / "modules" / "nesting_engine" / "manager.py").read_text(
        encoding="utf-8"
    )
    assert "enriquecer_piezas_hoja_con_fuentes(nh, batch)" in mgr
    assert 'pz["plasma_fuente_ya_compensada"] = True' in mgr


if __name__ == "__main__":
    test_mudar_pieza_ya_compensada_no_dobla_tabla()
    test_tabla_no_suma_si_hay_ruta_plasma()
    test_packer_transfer_reinyecta_plasma()
    print("OK plasma_transfer_sigue_compensada")
