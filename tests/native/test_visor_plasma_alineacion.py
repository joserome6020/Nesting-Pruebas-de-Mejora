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
        elif e.dxftype() == "LWPOLYLINE":
            for x, y, *_ in e.get_points("xy"):
                xs.append(x)
                ys.append(y)
        elif e.dxftype() == "CIRCLE":
            c = e.dxf.center
            r = float(e.dxf.radius)
            xs.extend([c.x - r, c.x + r])
            ys.extend([c.y - r, c.y + r])
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _ring_bbox(ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _crear_dxf_lwpolyline(ruta: Path, W: float, H: float) -> None:
    """Flat pattern con outer LWPOLYLINE (la otra ruta del loader)."""
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for capa, color in (("CUT_OUTER", 1), ("CUT_INNER", 3)):
        if capa not in doc.layers:
            doc.layers.new(capa, dxfattribs={"color": color})
    msp.add_lwpolyline(
        [(0, 0), (W, 0), (W, H), (0, H)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    msp.add_lwpolyline(
        [(1.0, 1.0), (2.2, 1.0), (2.2, 1.6), (1.0, 1.6)],
        close=True,
        dxfattribs={"layer": "CUT_INNER"},
    )
    doc.saveas(str(ruta))


def _centro(bb) -> tuple[float, float]:
    return ((bb[0] + bb[2]) * 0.5, (bb[1] + bb[3]) * 0.5)


def test_ring_alineado_con_msp_para_todas_las_rotaciones() -> None:
    """Bug: al rotar, el contorno rojo aparecía separado de la pieza.

    Se cubren las DOS rutas del loader (LINE+ARC de Inventor y LWPOLYLINE),
    porque el desplazamiento venía de ``rotate_modelspace`` y afectaba a
    ambas por igual.
    """
    from interface.qt.dxf_part_loader import load_dxf_part

    with tempfile.TemporaryDirectory() as td:
        casos = {}
        ruta_iv = Path(td) / "iv_rot.dxf"
        _crear_dxf_inventor(ruta_iv)
        casos["LINE+ARC"] = ruta_iv
        ruta_lw = Path(td) / "lw_rot.dxf"
        _crear_dxf_lwpolyline(ruta_lw, 6.53, 3.57)
        casos["LWPOLYLINE"] = ruta_lw

        for etiqueta, ruta in casos.items():
            for rot in (0, 90, 180, 270):
                model = load_dxf_part(str(ruta), rotacion_vista_deg=rot)
                assert model is not None, f"{etiqueta} rot={rot}: loader None"
                rings = list(model.outer_rings or [])
                assert rings, f"{etiqueta} rot={rot}: outer_rings vacío"
                rb = _ring_bbox(rings[0])
                mb = _msp_outer_bbox(model.msp)
                deltas = [abs(rb[i] - mb[i]) for i in range(4)]
                assert max(deltas) < 0.02, (
                    f"{etiqueta} rot={rot}: ring bbox {rb} != msp bbox {mb} "
                    f"deltas={deltas} — el énfasis rojo saldría separado de la pieza"
                )


def test_rotacion_conserva_el_centro_de_la_pieza() -> None:
    """Check absoluto: rotar 90/180/270 sobre el centro no puede trasladar.

    Comparar sólo anillo-vs-msp es circular (ambos pueden estar mal igual).
    ``rotate_modelspace`` componía la matriz al revés — ezdxf usa vectores
    fila, así que en ``A @ B`` se aplica A primero — y dejaba la msp
    trasladada. La traslación era invisible porque ``fit_view`` reencuadra,
    hasta que se superpuso ``outer_rings`` y aparecieron dos contornos.
    """
    from interface.qt.dxf_part_loader import load_dxf_part

    with tempfile.TemporaryDirectory() as td:
        for etiqueta, crear in (
            ("LINE+ARC", lambda p: _crear_dxf_inventor(p)),
            ("LWPOLYLINE", lambda p: _crear_dxf_lwpolyline(p, 6.53, 3.57)),
        ):
            ruta = Path(td) / f"centro_{etiqueta.replace('+', '_')}.dxf"
            crear(ruta)
            base = load_dxf_part(str(ruta), rotacion_vista_deg=0)
            cx0, cy0 = _centro(_msp_outer_bbox(base.msp))
            for rot in (90, 180, 270):
                model = load_dxf_part(str(ruta), rotacion_vista_deg=rot)
                cx, cy = _centro(_msp_outer_bbox(model.msp))
                desvio = math.hypot(cx - cx0, cy - cy0)
                assert desvio < 0.02, (
                    f"{etiqueta} rot={rot}: la pieza se trasladó {desvio:.3f} in "
                    f"(centro {cx0:.3f},{cy0:.3f} → {cx:.3f},{cy:.3f}); rotar "
                    f"sobre el centro debe conservarlo"
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
    test_rotacion_conserva_el_centro_de_la_pieza()
    test_label_plasma_usa_flag_cosmetico()
    test_set_plasma_overlay_y_emphasize_delegan_al_helper()
    print("OK visor_plasma_alineacion")
