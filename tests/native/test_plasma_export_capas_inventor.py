"""Candado 2026-08-14o — el export plasma reconoce las capas nativas de Inventor.

Bug de producción: al exportar el nest, las piezas de chapa reventaban con

    <PIEZA>_PLASMA: plasma: sin contorno exportable desde el nest

``_plasma_desfase_clase`` comparaba la capa por **igualdad exacta** contra
``{"CUT_OUTER", "OUTER", "CORTE_EXTERNO", "IV_OUTER"}``. Inventor exporta los
flat patterns de chapa con capas ``IV_OUTER_PROFILE`` e
``IV_INTERIOR_PROFILES``, así que ninguna clasificaba: ``by_clase["outer"]``
quedaba vacío, ``stats["outer"] == 0`` y el export moría — aunque el nest sí
reconocía la pieza, porque ``geometry_parser._clasificar_capa`` compara por
subcadena. Dos caminos calculando lo mismo distinto (AGENTS.md regla 9).

El fix alinea el criterio con el del nest (subcadena) manteniendo fuera la
capa por defecto ``"0"`` y las capas de marcado, que no deben cortarse.
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

ESCALA = 25.4
OFF_MM = 0.3175  # 0.0125"


def _crear_flat_pattern_inventor(ruta: Path, W: float, H: float, r_hole: float) -> None:
    """Flat pattern estilo Inventor: outer LINE+ARC y un agujero interior."""
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for capa in (
        "IV_OUTER_PROFILE",
        "IV_INTERIOR_PROFILES",
        "IV_BEND",
        "IV_ARC_CENTERS",
    ):
        if capa not in doc.layers:
            doc.layers.new(capa)

    r = 0.25
    lo = {"layer": "IV_OUTER_PROFILE"}
    msp.add_line((r, 0.0), (W - r, 0.0), dxfattribs=lo)
    msp.add_line((W, r), (W, H - r), dxfattribs=lo)
    msp.add_line((W - r, H), (r, H), dxfattribs=lo)
    msp.add_line((0.0, H - r), (0.0, r), dxfattribs=lo)
    msp.add_arc((W - r, r), r, 270.0, 360.0, dxfattribs=lo)
    msp.add_arc((W - r, H - r), r, 0.0, 90.0, dxfattribs=lo)
    msp.add_arc((r, H - r), r, 90.0, 180.0, dxfattribs=lo)
    msp.add_arc((r, r), r, 180.0, 270.0, dxfattribs=lo)

    msp.add_circle((W * 0.5, H * 0.5), r_hole, dxfattribs={"layer": "IV_INTERIOR_PROFILES"})
    # Ruido que NO debe cortarse.
    msp.add_line((0.5, H * 0.5), (W - 0.5, H * 0.5), dxfattribs={"layer": "IV_BEND"})
    doc.saveas(str(ruta))


def _bbox(ents, capa_sub: str):
    xs: list[float] = []
    ys: list[float] = []
    for e in ents:
        if capa_sub not in str(getattr(e.dxf, "layer", "")).upper():
            continue
        t = e.dxftype()
        if t == "LINE":
            xs += [e.dxf.start.x, e.dxf.end.x]
            ys += [e.dxf.start.y, e.dxf.end.y]
        elif t == "ARC":
            c, rr = e.dxf.center, float(e.dxf.radius)
            for a in (e.dxf.start_angle, e.dxf.end_angle):
                xs.append(c.x + rr * math.cos(math.radians(a)))
                ys.append(c.y + rr * math.sin(math.radians(a)))
        elif t == "CIRCLE":
            c, rr = e.dxf.center, float(e.dxf.radius)
            xs += [c.x - rr, c.x + rr]
            ys += [c.y - rr, c.y + rr]
        elif t == "LWPOLYLINE":
            for x, y, *_ in e.get_points("xy"):
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def test_clasificacion_de_capas_inventor() -> None:
    """El match exacto dejaba fuera IV_OUTER_PROFILE / IV_INTERIOR_PROFILES."""
    from modules.plasma_dxf_export import _plasma_desfase_clase

    assert _plasma_desfase_clase("IV_OUTER_PROFILE") == "outer"
    assert _plasma_desfase_clase("IV_INTERIOR_PROFILES") == "inner"
    # Nombres históricos siguen funcionando.
    assert _plasma_desfase_clase("CUT_OUTER") == "outer"
    assert _plasma_desfase_clase("CUT_INNER") == "inner"
    assert _plasma_desfase_clase("OUTER") == "outer"
    assert _plasma_desfase_clase("CORTE_EXTERNO") == "outer"
    assert _plasma_desfase_clase("CORTE_INTERNO") == "inner"


def test_capas_que_no_deben_cortarse_siguen_fuera() -> None:
    """El fix amplía el match; no debe tragarse marcas, dobleces ni la capa 0."""
    from modules.plasma_dxf_export import _plasma_desfase_clase

    for capa in (
        "0",
        "",
        "IV_BEND",
        "IV_TANGENT",
        "IV_ARC_CENTERS",
        "Plate",
        "MARK",
        "MARKING",
        "ETCH",
        "GRABADO",
        "CUT_OUTER_MARK",  # marcado gana sobre corte
    ):
        assert _plasma_desfase_clase(capa) is None, capa


def test_export_plasma_no_falla_con_capas_inventor() -> None:
    """Repro directo: stats['outer'] era 0 → 'plasma sin contorno exportable'."""
    import ezdxf  # type: ignore

    from modules.plasma_dxf_export import export_compensated_plasma_from_source

    W, H, r_hole = 9.0, 3.0, 0.5
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "flat_sm.dxf"
        _crear_flat_pattern_inventor(src, W, H, r_hole)

        doc = ezdxf.new("R2018")
        msp = doc.modelspace()
        p = {
            "part_name": "SM_FLAT_PLASMA",
            "ruta": str(src),
            "plasma_offset_mm": OFF_MM,
            "plasma_export": True,
            "x": 0.0,
            "y": 0.0,
            "rot_deg": 0,
        }
        stats = export_compensated_plasma_from_source(msp, doc, p)

        assert int(stats.get("outer", 0)) > 0, (
            f"sin contorno exterior exportado: {stats} — este es exactamente el "
            f"fallo 'plasma: sin contorno exportable desde el nest'"
        )
        assert int(stats.get("inner", 0)) > 0, f"agujero interior perdido: {stats}"

        ents = list(msp)
        capas = {str(e.dxf.layer).upper() for e in ents}
        assert capas <= {"CUT_OUTER", "CUT_INNER"}, (
            f"el export debe normalizar a CUT_OUTER/CUT_INNER, salió {capas}"
        )

        # El desfase exterior crece 2*offset (todo en mm tras el placement).
        ob = _bbox(ents, "CUT_OUTER")
        assert ob is not None
        ancho = ob[2] - ob[0]
        alto = ob[3] - ob[1]
        esperado_w = W * ESCALA + 2 * OFF_MM
        esperado_h = H * ESCALA + 2 * OFF_MM
        assert abs(ancho - esperado_w) < 0.05, f"{ancho} vs {esperado_w}"
        assert abs(alto - esperado_h) < 0.05, f"{alto} vs {esperado_h}"

        # El agujero se desfasa hacia ADENTRO: encoge 2*offset.
        ib = _bbox(ents, "CUT_INNER")
        assert ib is not None
        d_hole = ib[2] - ib[0]
        esperado_hole = 2 * r_hole * ESCALA - 2 * OFF_MM
        assert abs(d_hole - esperado_hole) < 0.05, f"{d_hole} vs {esperado_hole}"


def test_bend_no_se_exporta_como_corte() -> None:
    """IV_BEND es doblez, no corte: no debe aparecer en CUT_*."""
    import ezdxf  # type: ignore

    from modules.plasma_dxf_export import export_compensated_plasma_from_source

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "flat_sm.dxf"
        _crear_flat_pattern_inventor(src, 9.0, 3.0, 0.5)
        doc = ezdxf.new("R2018")
        msp = doc.modelspace()
        export_compensated_plasma_from_source(
            msp,
            doc,
            {
                "part_name": "SM_FLAT_PLASMA",
                "ruta": str(src),
                "plasma_offset_mm": OFF_MM,
                "plasma_export": True,
                "x": 0.0,
                "y": 0.0,
                "rot_deg": 0,
            },
        )
        # La línea de doblez cruza el centro a media altura; si se hubiera
        # colado como corte habría una LINE horizontal suelta en CUT_*.
        inner = [e for e in msp if str(e.dxf.layer).upper() == "CUT_INNER"]
        assert inner, "el agujero debe seguir exportándose"
        assert all(e.dxftype() != "LINE" for e in inner), (
            "IV_BEND se coló en CUT_INNER como línea de corte"
        )


if __name__ == "__main__":
    test_clasificacion_de_capas_inventor()
    test_capas_que_no_deben_cortarse_siguen_fuera()
    test_export_plasma_no_falla_con_capas_inventor()
    test_bend_no_se_exporta_como_corte()
    print("OK plasma_export_capas_inventor")
