"""Candado: split CyPTube vertical Corte/Marcaje + JSON A_mm=ancho+0.2."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ezdxf  # noqa: E402

from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf  # noqa: E402
from modules.dxf_export.cyptube_vertical import (  # noqa: E402
    PARAM_A_OFFSET_MM,
    PARAM_B_MM_DEFAULT,
    cyptube_param_A_mm,
    escribir_cyptube_verticales_json,
    split_cyptube_vertical_dxf,
)
from modules.nest_exporter import _assert_dxf_autocad_safe_on_disk  # noqa: E402


def _bar_placement(*, especial: bool = True):
    w_mm = 6.0 * 25.4
    l_mm = 40.0 * 25.4
    outer = [(0.0, 0.0), (l_mm, 0.0), (l_mm, w_mm), (0.0, w_mm)]
    mark = [(50.0, 10.0), (200.0, 10.0)]
    hole = [(100.0, 20.0), (120.0, 20.0), (120.0, 40.0), (100.0, 40.0)]
    return {
        "part_name": "GENE-ROU-S-102",
        "outer": outer,
        "holes": [hole],
        "marks": [mark],
        "cu_largos_piece": True,
        "cu_bar_w_mm": w_mm,
        "cu_bar_l_mm": l_mm,
        "cu_especial_vertical": especial,
        "orig_minx": 0.0,
        "orig_miny": 0.0,
        "shift_x": 0.0,
        "shift_y": 0.0,
        "rot_deg": 0.0,
        "rot_origin_cx": 0.0,
        "rot_origin_cy": 0.0,
    }, w_mm, l_mm


def test_cyptube_param_A_es_ancho_mas_0_2() -> None:
    assert math.isclose(cyptube_param_A_mm(152.4), 152.6, abs_tol=1e-9)
    assert math.isclose(PARAM_A_OFFSET_MM, 0.2)
    assert math.isclose(PARAM_B_MM_DEFAULT, 6.0)


def test_split_vertical_corte_marcaje_y_json() -> None:
    placement, w_mm, _l = _bar_placement(especial=True)
    sheet = {
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
        "length": 144.0 * 25.4,
        "width": w_mm,
        "Length": 144.0 * 25.4,
        "Width": w_mm,
    }
    with tempfile.TemporaryDirectory() as tmp:
        combined = os.path.join(tmp, "NESTING_0.25_W.O. 65 X3-H47.dxf")
        export_cobre_hoja_to_dxf(
            combined,
            sheet,
            [placement],
            title="AMADA/VERTICAL | TEST",
            draw_holes=False,
            draw_marks=True,
            strict=False,
        )
        assert os.path.isfile(combined)
        paths, rec = split_cyptube_vertical_dxf(
            combined,
            bar_width_mm=w_mm,
            canal="AMADA/VERTICAL",
            sheet_code="W.O. 65 X3-H47",
            cu_especial_vertical=True,
            thickness_in=0.25,
            remove_combined=True,
        )
        assert not os.path.isfile(combined), "combinado debe eliminarse"
        assert paths.corte.endswith("_Corte.dxf")
        assert paths.marcaje.endswith("_Marcaje.dxf")
        assert os.path.isfile(paths.corte) and os.path.isfile(paths.marcaje)

        doc_c = ezdxf.readfile(paths.corte)
        layers_c = {
            str(getattr(e.dxf, "layer", "") or "").upper() for e in doc_c.modelspace()
        }
        assert any(L.startswith("CUT_") for L in layers_c), "Corte sin geometría de corte"
        assert "MARK" not in layers_c, "Corte no debe llevar MARK"

        doc_m = ezdxf.readfile(paths.marcaje)
        layers_m = {
            str(getattr(e.dxf, "layer", "") or "").upper() for e in doc_m.modelspace()
        }
        assert "MARK" in layers_m, "Marcaje debe tener MARK"
        # Contorno CypTube: origen (BAR_START) y/o fin (CUT_OUTER) a ancho de barra.
        ref_lines = [
            e
            for e in doc_m.modelspace()
            if e.dxftype() == "LINE"
            and str(getattr(e.dxf, "layer", "") or "").upper()
            in {"CUT_OUTER", "BAR_START"}
        ]
        assert ref_lines, "Marcaje debe incluir guillotina origen y/o fin"
        # No debe arrastrar barrenos ni contornos de pieza (CUT_INNER).
        assert "CUT_INNER" not in layers_m, "Marcaje no debe llevar barrenos"

        _assert_dxf_autocad_safe_on_disk(paths.corte)
        _assert_dxf_autocad_safe_on_disk(paths.marcaje)

        assert math.isclose(rec.A_mm, w_mm + 0.2, abs_tol=1e-6)
        assert math.isclose(rec.B_mm, 6.0, abs_tol=1e-9)

        nesteos = os.path.join(tmp, "NESTEOS DE COBRE")
        json_path = escribir_cyptube_verticales_json(nesteos, [rec])
        assert json_path and os.path.isfile(json_path)
        data = json.loads(open(json_path, encoding="utf-8").read())
        assert data["version"] == 1
        assert len(data["barras"]) == 1
        assert len(data["archivos"]) == 2
        assert {a["rol"] for a in data["archivos"]} == {"corte", "marcaje"}
        assert math.isclose(data["archivos"][0]["A_mm"], w_mm + 0.2, abs_tol=1e-6)


def test_pqart_debe_registrar_corte_y_marcaje() -> None:
    """Réplica del contrato _validar_lote_exportado tras split CyPTube."""
    from modules.nesting_engine.exporter import _registrar_exportacion_pqart_hoja

    hoja: dict = {"sheet_uid": "u1", "sheet_code": "H100"}
    corte = r"C:\tmp\NESTING_0.25_H100_Corte.dxf"
    marcaje = r"C:\tmp\NESTING_0.25_H100_Marcaje.dxf"
    _registrar_exportacion_pqart_hoja(hoja, ruta_dxf=corte, tipo_corte="CamaLaser")
    _registrar_exportacion_pqart_hoja(hoja, ruta_dxf=marcaje, tipo_corte="CamaLaser")
    exportados = {os.path.normcase(os.path.normpath(p)) for p in (corte, marcaje)}
    pqart = {
        os.path.normcase(os.path.normpath(str(item.get("ruta") or "")))
        for item in hoja["pqart_exports"]
    }
    assert exportados == pqart, "Corte y Marcaje deben quedar en pqart_exports"


if __name__ == "__main__":
    test_cyptube_param_A_es_ancho_mas_0_2()
    test_split_vertical_corte_marcaje_y_json()
    test_pqart_debe_registrar_corte_y_marcaje()
    print("[OK] CyPTube vertical Corte/Marcaje + JSON")
