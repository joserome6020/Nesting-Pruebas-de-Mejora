"""
Candados de dos fallas de producción del corte plasma, reportadas sobre
`GENE-OP-10-117` (Cal 0.0747): el DXF de `Plasma Compensated` se veía correcto
con su arco nativo, pero el DXF exportado del nesteo traía ese arco convertido
en una decena de segmentos rectos, y las esquinas rectas salían redondeadas.

1) Fuente compensada nunca inyectada
   `export_plasma_placement` sí tenía la rama que clona `Plasma Compensated`
   1:1, pero exigía las llaves `plasma_fuente_ya_compensada` y `ruta_plasma`
   en la pieza. `nesting_engine.exporter` sólo mandaba `ruta` y
   `compensated_plasma_source`, así que la rama jamás corría en la app: un
   `.arganest` reabierto caía a recalcular el offset del original y escribía
   el anillo muestreado. El test anterior pasaba porque ponía las llaves a
   mano; aquí se parte de una pieza tal como llega del nest, sin ellas.

2) Puntas redondeadas por OCCT
   `BRepOffsetAPI_MakeOffset` ignora `GeomAbs_Intersection` y redondea toda
   esquina convexa con radio |offset|. Una escuadra debe salir en escuadra:
   sólo se conservan las curvas que ya existían en el DXF origen.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

OFFSET_MM = 0.3175  # Cal 0.0747 -> 0.0125" por lado


def _dxf_origen(destino: Path) -> None:
    """Rectángulo 10x6 con tres esquinas vivas y una esquina de radio 1 real."""
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2018")
    doc.modelspace().add_lwpolyline(
        [
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0, 0.0, 0.0),
            (10.0, 5.0, 0.0, 0.0, math.tan(math.radians(90.0) / 4.0)),
            (9.0, 6.0, 0.0, 0.0, 0.0),
            (0.0, 6.0, 0.0, 0.0, 0.0),
        ],
        format="xyseb",
        dxfattribs={"layer": "CUT_OUTER", "closed": True},
    )
    doc.saveas(destino)


def _vertices_outer(ruta: Path) -> list[tuple[float, float, float]]:
    import ezdxf  # type: ignore

    doc = ezdxf.readfile(ruta)
    for e in doc.modelspace():
        if str(e.dxf.layer or "").upper() != "CUT_OUTER":
            continue
        if e.dxftype() == "LWPOLYLINE":
            return [(float(v[0]), float(v[1]), float(v[4])) for v in e.get_points("xyseb")]
    return []


def test_offset_conserva_esquinas_vivas_y_solo_arcos_del_origen() -> None:
    from modules.plasma_compensator import compensate_dxf_for_plasma

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src, dst = root / "src.dxf", root / "out.dxf"
        _dxf_origen(src)

        stats = compensate_dxf_for_plasma(src, dst, offset_mm=OFFSET_MM)
        assert int(stats.get("changed") or 0) == 1, f"no compensó: {stats}"

        vertices = _vertices_outer(dst)
        assert vertices, "el compensado no dejó CUT_OUTER como polilínea"
        con_bulge = [v for v in vertices if abs(v[2]) > 1e-9]
        # El origen sólo tiene una curva; cualquier bulge extra es un radio
        # inventado por el offset sobre una punta que debe quedar viva.
        assert len(con_bulge) == 1, (
            f"el offset redondeó esquinas rectas: {len(con_bulge)} bulges "
            f"en {len(vertices)} vértices -> {vertices}"
        )

        off_in = OFFSET_MM / 25.4
        esquinas = {(round(v[0], 4), round(v[1], 4)) for v in vertices}
        for esperada in (
            (-off_in, -off_in),
            (10.0 + off_in, -off_in),
            (-off_in, 6.0 + off_in),
        ):
            clave = (round(esperada[0], 4), round(esperada[1], 4))
            assert clave in esquinas, (
                f"falta la punta a inglete {clave}; el offset la redondeó: {vertices}"
            )


def test_pieza_del_nest_sin_ruta_plasma_resuelve_el_compensado() -> None:
    """Sin este puente el export recalculaba el offset y facetaba los arcos."""
    from modules.plasma_dxf_export import resolver_fuente_plasma

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "GENE-OP-10-117, A 36, QTY 6, Cal 0.0747.dxf"
        _dxf_origen(src)

        # Pieza tal como llega de un `.arganest` reabierto: sabe que está
        # compensada y con cuánto, pero no trae la ruta del compensado.
        pz = {"ruta": str(src), "plasma_compensada_manual": True}
        res = resolver_fuente_plasma(pz, compensada=True, offset_mm=OFFSET_MM)

        assert res["plasma_fuente_ya_compensada"] is True
        assert "Plasma Compensated" in res["ruta"]
        assert res["ruta"] == res["ruta_plasma"]
        assert Path(res["ruta"]).is_file()
        # Volver a desfasar el compensado duplicaría el crecimiento.
        assert res["plasma_offset_mm"] == 0.0


def test_export_del_nest_clona_el_compensado_con_arco_nativo() -> None:
    import ezdxf  # type: ignore

    from modules.plasma_dxf_export import export_plasma_placement, resolver_fuente_plasma

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "pieza.dxf"
        _dxf_origen(src)

        res = resolver_fuente_plasma(
            {"ruta": str(src)}, compensada=True, offset_mm=OFFSET_MM
        )
        assert res["plasma_fuente_ya_compensada"], "no se resolvió Plasma Compensated"

        vertices = _vertices_outer(Path(res["ruta"]))
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        # El nest colocó exactamente la geometría compensada: el contorno del
        # placement es su bbox, así la validación no espera un segundo offset.
        outer = [
            (min(xs), min(ys)),
            (max(xs), min(ys)),
            (max(xs), max(ys)),
            (min(xs), max(ys)),
        ]

        out = ezdxf.new("R2018")
        p = dict(res)
        p.update(
            {
                "part_name": "GENE-OP-10-117_PLASMA",
                "compensated": True,
                "plasma_export": True,
                "outer": outer,
                "rot_deg": 0.0,
                "shift_x": 0.0,
                "shift_y": 0.0,
            }
        )
        assert export_plasma_placement(
            out.modelspace(), out, p, draw_marks=False
        ), f"export falló: {p.get('_plasma_validation_error')}"

        cortes = [
            e
            for e in out.modelspace()
            if str(e.dxf.layer or "").upper() == "CUT_OUTER"
        ]
        tipos = [e.dxftype() for e in cortes]
        assert "ARC" in tipos, (
            f"el arco de Plasma Compensated salió faceteado: {tipos}"
        )
        # Cuatro lados rectos: más LINEs significa un arco muestreado.
        assert tipos.count("LINE") <= 5, f"contorno faceteado: {tipos}"


def test_el_armador_de_placas_no_reimplementa_la_resolucion() -> None:
    """Los dos armadores de placas deben pasar por el mismo puente."""
    fuente = (RAIZ / "modules" / "nesting_engine" / "exporter.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert fuente.count("resolver_fuente_plasma(") >= 2, (
        "un armador de placas volvió a decidir la fuente plasma por su cuenta"
    )
    assert '"ruta": plasma_ruta' not in fuente, (
        "quedó la lógica inline que ignoraba Plasma Compensated"
    )


if __name__ == "__main__":
    test_offset_conserva_esquinas_vivas_y_solo_arcos_del_origen()
    test_pieza_del_nest_sin_ruta_plasma_resuelve_el_compensado()
    test_export_del_nest_clona_el_compensado_con_arco_nativo()
    test_el_armador_de_placas_no_reimplementa_la_resolucion()
    print("OK plasma_inyecta_compensado_y_esquinas_vivas")
