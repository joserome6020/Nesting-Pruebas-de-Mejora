"""Candado 2026-08-14n — el énfasis plasma queda alineado y el label no infla la vista.

Dos bugs regresivos que se destaparon al empezar a poblar ``outer_rings`` para
piezas Inventor (LINE+ARC):

1. **Doble rotación**: ``_agregar_shapes_desde_line_arc`` sampleaba las
   entidades DESPUÉS de que ``rotate_modelspace`` ya las había rotado en la
   msp, y luego aplicaba ``rotar_punto`` otra vez. Resultado: la pieza se
   pintaba a 90° (render_modelspace) y el anillo a 180° (emphasize) — el
   OUTER rojo aparecía desplazado y espejado respecto a la pieza.

2. **Label de escena en tamaño puntos**: ``set_plasma_overlay`` y
   ``emphasize_plasma_outers`` creaban un ``QGraphicsSimpleTextItem`` con
   ``font.setPointSize(10)`` sin la bandera ``ItemIgnoresTransformations``.
   Los 10 pt se interpretaban en unidades de escena (pulgadas), así que
   "COMPENSADA +0.0125\"" quedaba de ~10 in de alto → ``fit_view`` hacía
   zoom-out extremo y la pieza salía diminuta.

Este candado sintetiza el DXF Inventor (LINE+ARC en IV_OUTER_PROFILE) y:
  * carga con rotaciones 0/90/180/270 y verifica que el bbox del anillo
    coincide con el bbox real de las entidades msp
  * inspecta el código de ``_agregar_label_plasma`` en cad_graphics_view.py
    para asegurar que el flag cosmético está puesto.
"""
from __future__ import annotations

import ast
import math
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _crear_dxf_inventor(ruta: Path) -> None:
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    if "IV_OUTER_PROFILE" not in doc.layers:
        doc.layers.new("IV_OUTER_PROFILE", dxfattribs={"color": 7})
    r = 0.25
    W, H = 4.0, 2.0
    lo = {"layer": "IV_OUTER_PROFILE"}
    msp.add_line((r, 0.0), (W - r, 0.0), dxfattribs=lo)
    msp.add_line((W, r), (W, H - r), dxfattribs=lo)
    msp.add_line((W - r, H), (r, H), dxfattribs=lo)
    msp.add_line((0.0, H - r), (0.0, r), dxfattribs=lo)
    msp.add_arc(center=(W - r, r), radius=r, start_angle=270.0, end_angle=360.0, dxfattribs=lo)
    msp.add_arc(center=(W - r, H - r), radius=r, start_angle=0.0, end_angle=90.0, dxfattribs=lo)
    msp.add_arc(center=(r, H - r), radius=r, start_angle=90.0, end_angle=180.0, dxfattribs=lo)
    msp.add_arc(center=(r, r), radius=r, start_angle=180.0, end_angle=270.0, dxfattribs=lo)
    doc.saveas(str(ruta))


def _msp_outer_bbox(msp) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for e in msp:
        if "OUTER" not in str(e.dxf.layer).upper():
            continue
        if e.dxftype() == "LINE":
            xs.extend([e.dxf.start.x, e.dxf.end.x])
            ys.extend([e.dxf.start.y, e.dxf.end.y])
        elif e.dxftype() == "ARC":
            c = e.dxf.center
            r = float(e.dxf.radius)
            sa = math.radians(float(e.dxf.start_angle))
            ea = math.radians(float(e.dxf.end_angle))
            for a in (sa, ea):
                xs.append(c.x + r * math.cos(a))
                ys.append(c.y + r * math.sin(a))
    return min(xs), min(ys), max(xs), max(ys)


def _ring_bbox(ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def test_ring_alineado_con_msp_para_todas_las_rotaciones() -> None:
    """Bug: al rotar el visor el rojo aparecía desplazado (doble rotación)."""
    from interface.qt.dxf_part_loader import load_dxf_part

    with tempfile.TemporaryDirectory() as td:
        ruta = Path(td) / "iv_rot.dxf"
        _crear_dxf_inventor(ruta)
        for rot in (0, 90, 180, 270):
            model = load_dxf_part(str(ruta), rotacion_vista_deg=rot)
            assert model is not None, f"loader None en rot={rot}"
            rings = list(model.outer_rings or [])
            assert rings, f"outer_rings vacío en rot={rot}"
            rb = _ring_bbox(rings[0])
            mb = _msp_outer_bbox(model.msp)
            deltas = [
                abs(rb[0] - mb[0]),
                abs(rb[1] - mb[1]),
                abs(rb[2] - mb[2]),
                abs(rb[3] - mb[3]),
            ]
            assert max(deltas) < 0.02, (
                f"rot={rot}: ring bbox {rb} != msp bbox {mb} "
                f"deltas={deltas} (>0.02 in ⇒ doble rotación regresada)"
            )


def test_label_plasma_usa_flag_cosmetico() -> None:
    """Bug: label 10pt en unidades de escena tapaba media pantalla."""
    fuente = (RAIZ / "interface" / "qt" / "cad_graphics_view.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(fuente)
    metodo: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_agregar_label_plasma":
            metodo = node
            break
    assert metodo is not None, (
        "_agregar_label_plasma no está definido; sin él el label vuelve a "
        "salir gigantesco en la escena."
    )
    cuerpo = ast.unparse(metodo)
    assert "ItemIgnoresTransformations" in cuerpo, (
        "_agregar_label_plasma debe activar ItemIgnoresTransformations para "
        "que el texto se pinte en píxeles y no en pulgadas."
    )
    # Ninguno de los sitios antiguos debe seguir armando el texto a mano.
    contadores = sum(
        1
        for l in fuente.splitlines()
        if "QGraphicsSimpleTextItem" in l and "PLASMA" in l.upper() or (
            "QGraphicsSimpleTextItem(str(label))" in l
        )
    )
    assert contadores == 0, (
        "quedan QGraphicsSimpleTextItem(str(label)) sueltos: deben ir todos "
        "por _agregar_label_plasma (una sola fuente de verdad)."
    )


def test_set_plasma_overlay_y_emphasize_delegan_al_helper() -> None:
    fuente = (RAIZ / "interface" / "qt" / "cad_graphics_view.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(fuente)
    metodos = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in ("set_plasma_overlay", "emphasize_plasma_outers")
    }
    for nombre, nodo in metodos.items():
        src = ast.unparse(nodo)
        assert "_agregar_label_plasma" in src, (
            f"{nombre} debe llamar a _agregar_label_plasma en lugar de "
            f"construir el texto en línea."
        )


if __name__ == "__main__":
    test_ring_alineado_con_msp_para_todas_las_rotaciones()
    test_label_plasma_usa_flag_cosmetico()
    test_set_plasma_overlay_y_emphasize_delegan_al_helper()
    print("OK visor_plasma_alineacion")
