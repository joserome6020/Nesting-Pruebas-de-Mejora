"""
Candado: triángulos / perfiles con lados inclinados no se rechazan por AABB.

La SOLERA JACKING PAD (Cal 0.373) fallaba en PARTS con:
  PLASMA: el contorno compensado no mide lo esperado (… vs …); se rechaza el DXF.

Causa: la compuerta exigía AABB_out == AABB_src + 2·offset, fórmula exacta
solo para rectángulos alineados con join redondo. Con inglete y lados
oblicuos el bbox crece anisotrópico y tumba piezas buenas.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def test_triangulo_tipo_solera_no_se_rechaza_por_bbox() -> None:
    import ezdxf  # type: ignore

    from modules.plasma_compensator import (
        compensate_dxf_for_plasma,
        compute_plasma_offset_mm,
    )

    off_mm = compute_plasma_offset_mm(0.373)
    assert abs(off_mm - 0.0125 * 25.4) < 1e-9

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src, dst = root / "solera.dxf", root / "out.dxf"
        doc = ezdxf.new("R2018")
        # Mismas cotas redondeadas que muestra PARTS (~3.54 x 1.64).
        doc.modelspace().add_lwpolyline(
            [(0.0, 0.0), (3.54, 0.0), (1.77, 1.64)],
            dxfattribs={"layer": "CUT_OUTER", "closed": True},
        )
        doc.saveas(src)

        stats = compensate_dxf_for_plasma(src, dst, offset_mm=off_mm)
        assert int(stats.get("changed") or 0) == 1, stats
        assert dst.is_file()


def test_bbox_anisotropico_se_acepta_y_el_exceso_absurdo_no() -> None:
    from modules.plasma_occt_offset import validate_offset_bbox_growth

    off = 0.0125
    src = (3.54, 1.64)
    # Crecimiento típico de inglete en triángulo (~+0.0045 extra).
    ok = (src[0] + 2.0 * off + 0.0045, src[1] + 2.0 * off + 0.0045)
    assert validate_offset_bbox_growth(src, ok, off) == ""

    # Déficit leve por proyección de normales (caso del mensaje de planta).
    leve = (src[0] + 2.0 * off + 0.0047, src[1] + 2.0 * off - 0.0020)
    assert validate_offset_bbox_growth(src, leve, off) == ""

    # Lazo absurdo: creció 1" por lado.
    malo = (src[0] + 2.0, src[1] + 2.0)
    assert validate_offset_bbox_growth(src, malo, off)


if __name__ == "__main__":
    test_triangulo_tipo_solera_no_se_rechaza_por_bbox()
    test_bbox_anisotropico_se_acepta_y_el_exceso_absurdo_no()
    print("OK plasma_bbox_triangulo_inglete")
