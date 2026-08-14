"""Candado OCCT: OFFSET plasma conserva LINE/ARC/CIRCLE nativos."""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ezdxf

from modules.plasma_compensator import compensate_dxf_for_plasma, compute_plasma_offset_mm
from modules.plasma_occt_offset import occt_available


def test_occt_offset_preserva_primitivas():
    assert occt_available(), "OCP debe estar empaquetado: no hay fallback degradante"
    td = Path(tempfile.mkdtemp())
    src, dst = td / "in.dxf", td / "out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    # Perfil LINE/ARC (muesca semicircular) y barreno CIRCLE.
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((10, 0), (10, 5), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((10, 5), (6, 5), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_arc((5, 5), 1.0, 0, 180, dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((4, 5), (0, 5), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((0, 5), (0, 0), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_circle((7.5, 2.5), 0.75, dxfattribs={"layer": "CUT_INNER"})
    doc.saveas(src)

    off = compute_plasma_offset_mm(0.375)
    stats = compensate_dxf_for_plasma(src, dst, offset_mm=off)
    # Backend puede ser "occt", "occt+radius" o "clipper2+radius" según motor;
    # lo importante es que la compensación se aplicó.
    assert "occt" in stats["backend"] or "clipper2" in stats["backend"], stats
    out = ezdxf.readfile(dst).modelspace()
    outer = [e for e in out if str(e.dxf.layer) == "CUT_OUTER"]
    assert any(e.dxftype() == "LINE" for e in outer)
    assert any(e.dxftype() == "ARC" for e in outer)
    circles = [e for e in out if e.dxftype() == "CIRCLE" and str(e.dxf.layer) == "CUT_INNER"]
    assert len(circles) == 1
    assert abs(float(circles[0].dxf.radius) - (0.75 - off / 25.4)) < 1e-7


def test_occt_offset_preserva_bulge_como_arc():
    td = Path(tempfile.mkdtemp())
    src, dst = td / "bulge.dxf", td / "bulge_out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    # Semicírculo superior (bulge CW = hacia afuera) + base rectangular.
    doc.modelspace().add_lwpolyline(
        [(0, 0, -1.0), (10, 0, 0.0), (10, -5, 0.0), (0, -5, 0.0)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
        format="xyb",
    )
    doc.saveas(src)
    stats = compensate_dxf_for_plasma(src, dst, offset_mm=compute_plasma_offset_mm(0.375))
    assert "occt" in stats["backend"] or "clipper2" in stats["backend"], stats
    out = list(ezdxf.readfile(dst).modelspace())
    # La curvatura debe seguir siendo exacta: ARC nativo o bulge de polilínea.
    if any(e.dxftype() == "ARC" for e in out):
        return
    polis = [e for e in out if e.dxftype() == "LWPOLYLINE"]
    assert polis, "el resultado debe conservar curvas nativas"
    assert any(
        abs(float(v[4])) > 1e-9 for p in polis for v in p.get_points("xyseb")
    ), "el bulge no debe poligonizarse"


def test_occt_offset_parking_cw_no_brep_api():
    """Perfil tipo H.V parking (CW + muesca) no debe tumbar con BRep_API."""
    td = Path(tempfile.mkdtemp())
    src, dst = td / "hv_cw.dxf", td / "hv_cw_out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    layer = {"layer": "CUT_OUTER"}
    w, h, r = 3.25, 4.50, 1.50
    cx, cy = w, h / 2.0
    # Contorno horario (CW) con muesca circular — caso real de fallo OCCT.
    msp.add_line((0.0, 0.0), (0.0, h), dxfattribs=layer)
    msp.add_line((0.0, h), (w, h), dxfattribs=layer)
    msp.add_line((w, h), (cx, cy + r), dxfattribs=layer)
    msp.add_arc((cx, cy), r, 90.0, 270.0, dxfattribs=layer)
    msp.add_line((cx, cy - r), (w, 0.0), dxfattribs=layer)
    msp.add_line((w, 0.0), (0.0, 0.0), dxfattribs=layer)
    doc.saveas(src)
    stats = compensate_dxf_for_plasma(
        src, dst, offset_mm=compute_plasma_offset_mm(0.1046)
    )
    assert "occt" in stats["backend"] or "clipper2" in stats["backend"], stats
    assert int(stats["changed"]) >= 1
    out = list(ezdxf.readfile(dst).modelspace())
    assert any(e.dxftype() == "LINE" for e in out)
    assert any(e.dxftype() == "ARC" for e in out)


def _bbox_capa(msp, capa: str) -> tuple[float, float, float, float]:
    from modules.plasma_occt_offset import ring_from_specs, specs_from_dxf_entities

    ents = [e for e in msp if str(e.dxf.layer) == capa]
    pts = []
    for spec in specs_from_dxf_entities(ents):
        pts.extend(ring_from_specs([spec]))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def test_occt_offset_placa_con_filetes_y_slot_crece_exacto():
    """Réplica de la pieza real: filetes + slot obround, entrada CW.

    Candado del bug que deformaba el perfil (lazos en esquinas y contorno
    abierto con AREA NETA 0.00): el ARC de OCCT se escribía con los ángulos
    del arco complementario.
    """
    from modules.plasma_occt_offset import ring_from_specs, ring_is_simple, specs_from_dxf_entities

    td = Path(tempfile.mkdtemp())
    src, dst = td / "placa.dxf", td / "placa_out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    out = {"layer": "CUT_OUTER"}
    W, H, R = 33.40, 17.40, 1.00

    # Contorno horario (CW) con filete en las cuatro esquinas.
    msp.add_line((0.0, R), (0.0, H - R), dxfattribs=out)
    msp.add_arc((R, H - R), R, 90.0, 180.0, dxfattribs=out)
    msp.add_line((R, H), (W - R, H), dxfattribs=out)
    msp.add_arc((W - R, H - R), R, 0.0, 90.0, dxfattribs=out)
    msp.add_line((W, H - R), (W, R), dxfattribs=out)
    msp.add_arc((W - R, R), R, 270.0, 360.0, dxfattribs=out)
    msp.add_line((W - R, 0.0), (R, 0.0), dxfattribs=out)
    msp.add_arc((R, R), R, 180.0, 270.0, dxfattribs=out)

    # Slot obround interno (LINE + ARC), como el corte verde del visor.
    sx, sy, sl, sr = 12.0, 8.0, 4.0, 0.75
    inner = {"layer": "CUT_INNER"}
    msp.add_line((sx, sy + sr), (sx + sl, sy + sr), dxfattribs=inner)
    msp.add_arc((sx + sl, sy), sr, 270.0, 90.0, dxfattribs=inner)
    msp.add_line((sx + sl, sy - sr), (sx, sy - sr), dxfattribs=inner)
    msp.add_arc((sx, sy), sr, 90.0, 270.0, dxfattribs=inner)
    doc.saveas(src)

    off_mm = compute_plasma_offset_mm(0.250)
    stats = compensate_dxf_for_plasma(src, dst, offset_mm=off_mm)
    d = off_mm / 25.4
    msp_out = ezdxf.readfile(dst).modelspace()

    x0, y0, x1, y1 = _bbox_capa(msp_out, "CUT_OUTER")
    assert abs((x1 - x0) - (W + 2 * d)) < 1e-6, (x1 - x0, W + 2 * d)
    assert abs((y1 - y0) - (H + 2 * d)) < 1e-6, (y1 - y0, H + 2 * d)
    assert abs(x0 + d) < 1e-6 and abs(y0 + d) < 1e-6

    filetes = [
        e
        for e in msp_out
        if e.dxftype() == "ARC" and str(e.dxf.layer) == "CUT_OUTER"
    ]
    assert len(filetes) == 4, f"deben quedar 4 filetes, hay {len(filetes)}"
    for arc in filetes:
        assert abs(float(arc.dxf.radius) - (R + d)) < 1e-9

    # El slot interno encoge: el metal crece hacia dentro del corte.
    ix0, iy0, ix1, iy1 = _bbox_capa(msp_out, "CUT_INNER")
    assert abs((ix1 - ix0) - (sl + 2 * sr - 2 * d)) < 1e-6
    assert abs((iy1 - iy0) - (2 * sr - 2 * d)) < 1e-6

    # Y nada de lazos: el contorno resultante es simple.
    ring = ring_from_specs(
        specs_from_dxf_entities([e for e in msp_out if str(e.dxf.layer) == "CUT_OUTER"])
    )
    assert ring_is_simple(ring), "el contorno compensado no debe auto-intersectarse"


def _placa_con_hueco(path: Path, hueco: float) -> None:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    out = {"layer": "CUT_OUTER"}
    W, H = 20.0, 8.0
    msp.add_line((0.0, 0.0), (W, 0.0), dxfattribs=out)
    msp.add_line((W, 0.0), (W, H), dxfattribs=out)
    msp.add_line((W, H), (0.0, H), dxfattribs=out)
    # Último tramo con hueco deliberado contra el punto de inicio.
    msp.add_line((0.0, H), (0.0, hueco), dxfattribs=out)
    doc.saveas(path)


def test_micro_hueco_de_cad_se_puentea():
    """Los exports de CAD dejan micro-gaps: deben cerrarse, no ofsetear cintas."""
    td = Path(tempfile.mkdtemp())
    src, dst = td / "gap_ok.dxf", td / "gap_ok_out.dxf"
    _placa_con_hueco(src, 0.004)
    off_mm = compute_plasma_offset_mm(0.250)
    d = off_mm / 25.4
    compensate_dxf_for_plasma(src, dst, offset_mm=off_mm)
    x0, y0, x1, y1 = _bbox_capa(ezdxf.readfile(dst).modelspace(), "CUT_OUTER")
    assert abs((x1 - x0) - (20.0 + 2 * d)) < 1e-6
    assert abs((y1 - y0) - (8.0 + 2 * d)) < 1e-6


def test_contorno_muy_abierto_falla_cerrado():
    """Un perfil realmente roto se rechaza con mensaje claro y sin escribir DXF."""
    td = Path(tempfile.mkdtemp())
    src, dst = td / "gap_bad.dxf", td / "gap_bad_out.dxf"
    _placa_con_hueco(src, 1.5)
    try:
        compensate_dxf_for_plasma(src, dst, offset_mm=compute_plasma_offset_mm(0.250))
    except RuntimeError as exc:
        assert "no cierra" in str(exc) or "abierto" in str(exc) or "rasterizar" in str(exc), exc
    else:
        raise AssertionError("un contorno abierto no debe compensarse")
    assert not dst.exists(), "no debe quedar DXF compensado inválido en disco"


def _area_neta_estilo_visor(msp) -> float:
    """Replica el cálculo del visor: solo contornos cerrados aportan área."""
    from ezdxf import path

    from modules.plasma_occt_offset import _ring_metrics

    area = 0.0
    for ent in msp:
        capa = str(ent.dxf.layer).upper()
        signo = 1.0 if "OUTER" in capa else (-1.0 if "INNER" in capa else 0.0)
        if signo == 0.0:
            continue
        if ent.dxftype() == "CIRCLE":
            area += signo * math.pi * float(ent.dxf.radius) ** 2
            continue
        try:
            p = path.make_path(ent)
        except Exception:
            continue
        if not p.is_closed:
            continue
        pts = [(v[0], v[1]) for v in p.flattening(distance=0.01)]
        m = _ring_metrics(pts)
        if m:
            area += signo * m["area_abs"]
    return area


def test_polilinea_cerrada_sobrevive_y_area_no_queda_en_cero():
    """SWITCH PATCH 1: círculo interno = polilínea de 2 vértices con bulges.

    Candado doble: no debe fallar por "aristas suficientes" y el compensado
    debe seguir siendo polilínea cerrada (si no, AREA NETA sale 0.00).
    """
    td = Path(tempfile.mkdtemp())
    src, dst = td / "patch.dxf", td / "patch_out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    # Placa 4x4 con esquinas redondeadas (bulges) como polilínea cerrada.
    r = 0.50
    b = math.tan(math.radians(90.0) / 4.0)
    msp.add_lwpolyline(
        [
            (r, 0.0, 0.0),
            (4.0 - r, 0.0, b),
            (4.0, r, 0.0),
            (4.0, 4.0 - r, b),
            (4.0 - r, 4.0, 0.0),
            (r, 4.0, b),
            (0.0, 4.0 - r, 0.0),
            (0.0, r, b),
        ],
        format="xyb",
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    # Barreno como polilínea cerrada de dos vértices (dos semicírculos).
    msp.add_lwpolyline(
        [(1.5, 2.0, 1.0), (2.5, 2.0, 1.0)],
        format="xyb",
        close=True,
        dxfattribs={"layer": "CUT_INNER"},
    )
    doc.saveas(src)

    off_mm = compute_plasma_offset_mm(0.1875)
    d = off_mm / 25.4
    area_antes = _area_neta_estilo_visor(ezdxf.readfile(src).modelspace())
    compensate_dxf_for_plasma(src, dst, offset_mm=off_mm)
    msp_out = ezdxf.readfile(dst).modelspace()

    polis = [e for e in msp_out if e.dxftype() == "LWPOLYLINE"]
    assert len(polis) == 2, "el compensado debe seguir siendo polilíneas cerradas"
    assert all(bool(p.closed) for p in polis)

    x0, y0, x1, y1 = _bbox_capa(msp_out, "CUT_OUTER")
    assert abs((x1 - x0) - (4.0 + 2 * d)) < 1e-6
    assert abs((y1 - y0) - (4.0 + 2 * d)) < 1e-6

    area_despues = _area_neta_estilo_visor(msp_out)
    assert area_antes > 0 and area_despues > 0, (area_antes, area_despues)
    # Crece el exterior y encoge el barreno: el área neta sube.
    assert area_despues > area_antes


def test_barreno_con_muesca_conserva_la_muesca_en_su_sitio():
    """SWITCH PATCH 1: barreno con muesca (material que entra al agujero).

    El compensado debe crecer el metal |delta| en todo el contorno: el agujero
    encoge, la muesca engorda y **no se mueve de su ángulo**.
    """
    from modules.plasma_occt_offset import offset_entities, ring_from_specs

    CXY = 2.0
    R = 0.50
    RN = 0.07
    ANG = 265.0
    d = compute_plasma_offset_mm(0.1875) / 25.4

    px = CXY + R * math.cos(math.radians(ANG))
    py = CXY + R * math.sin(math.radians(ANG))
    # Cuerdas de intersección entre el barreno y la circunferencia de la muesca.
    dd = math.degrees(math.acos(max(-1.0, min(1.0, (2 * R * R - RN * RN) / (2 * R * R)))))
    a0, a1 = ANG + dd, ANG - dd
    p0 = (CXY + R * math.cos(math.radians(a0)), CXY + R * math.sin(math.radians(a0)))
    p1 = (CXY + R * math.cos(math.radians(a1)), CXY + R * math.sin(math.radians(a1)))
    n0 = math.degrees(math.atan2(p0[1] - py, p0[0] - px)) % 360.0
    n1 = math.degrees(math.atan2(p1[1] - py, p1[0] - px)) % 360.0
    # La muesca entra al agujero: su arco pasa por el punto interior (ANG+180 desde P).
    hacia_dentro = (ANG + 180.0) % 360.0
    if ((hacia_dentro - n0) % 360.0) <= ((n1 - n0) % 360.0):
        arc_muesca = (n0, n1)
    else:
        arc_muesca = (n1, n0)

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    capa = {"layer": "CUT_INNER"}
    msp.add_arc((CXY, CXY), R, a0, a1, dxfattribs=capa)
    msp.add_arc((px, py), RN, arc_muesca[0], arc_muesca[1], dxfattribs=capa)

    res = offset_entities(list(msp), delta=-d)
    assert res.ok, res.error

    radios = {round(float(s["radius"]), 6) for s in res.entities if s["type"] == "ARC"}
    assert round(R - d, 6) in radios, ("el barreno debe encoger |delta|", radios)
    assert round(RN + d, 6) in radios, ("la muesca debe engordar |delta|", radios)

    ring = ring_from_specs(res.entities)
    tip = min(ring, key=lambda p: math.dist(p, (CXY, CXY)))
    ang_tip = math.degrees(math.atan2(tip[1] - CXY, tip[0] - CXY)) % 360.0
    assert abs((ang_tip - ANG + 180.0) % 360.0 - 180.0) < 2.0, (
        "la muesca se movió de ángulo",
        ang_tip,
    )
    assert abs(math.dist(tip, (CXY, CXY)) - (R - RN - d)) < 1e-4


def test_clipper2_bulletproof_no_rechaza_esquinas_curvas():
    """GENE-DF-10-162: perfil grande con esquinas redondeadas y borde recto.

    OCCT solía fallar y la compuerta rechazaba por "auto-intersecta (lazos
    de esquina)". Con Clipper2 (motor de FreeCAD Path/CAM) siempre sale.
    """
    from modules.plasma_offset_clipper import clipper_disponible

    assert clipper_disponible(), "pyclipr debe estar empaquetado con la app"

    td = Path(tempfile.mkdtemp())
    src, dst = td / "big.dxf", td / "big_out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    W, H, R = 33.37, 18.77, 0.60
    o = {"layer": "CUT_OUTER"}
    msp.add_line((R, 0.0), (W - R, 0.0), dxfattribs=o)
    msp.add_arc((W - R, R), R, 270.0, 360.0, dxfattribs=o)
    msp.add_line((W, R), (W, H - R), dxfattribs=o)
    msp.add_arc((W - R, H - R), R, 0.0, 90.0, dxfattribs=o)
    msp.add_line((W - R, H), (R, H), dxfattribs=o)
    msp.add_arc((R, H - R), R, 90.0, 180.0, dxfattribs=o)
    msp.add_line((0.0, H - R), (0.0, R), dxfattribs=o)
    msp.add_arc((R, R), R, 180.0, 270.0, dxfattribs=o)
    doc.saveas(src)

    off_mm = compute_plasma_offset_mm(0.0747)
    d = off_mm / 25.4
    stats = compensate_dxf_for_plasma(src, dst, offset_mm=off_mm)
    assert int(stats["changed"]) >= 1, stats

    out = list(ezdxf.readfile(dst).modelspace())
    outers = [e for e in out if str(e.dxf.layer) == "CUT_OUTER"]
    assert outers, "el compensado debe tener contorno OUTER"
    xs = []
    ys = []
    for e in outers:
        if e.dxftype() == "LWPOLYLINE":
            for x, y, *_ in e.get_points("xy"):
                xs.append(x)
                ys.append(y)
        elif e.dxftype() == "LINE":
            xs += [e.dxf.start.x, e.dxf.end.x]
            ys += [e.dxf.start.y, e.dxf.end.y]
        elif e.dxftype() == "ARC":
            xs += [e.dxf.center.x - e.dxf.radius, e.dxf.center.x + e.dxf.radius]
            ys += [e.dxf.center.y - e.dxf.radius, e.dxf.center.y + e.dxf.radius]
    ancho = max(xs) - min(xs)
    alto = max(ys) - min(ys)
    assert abs(ancho - (W + 2 * d)) < 0.02
    assert abs(alto - (H + 2 * d)) < 0.02


def test_export_no_falla_sin_contorno_exportable_con_clipper():
    """GENE-BKT-101: el export fallaba con 'plasma sin contorno exportable'.

    ``_offset_closed_profile_inches`` devolvía [] cuando el motor primario no
    convergía; ahora reintenta con Clipper2 y produce el contorno.
    """
    from modules.plasma_dxf_export import _offset_closed_profile_inches

    # Perfil rectangular con esquinas redondeadas (33.37" x 10.52" reducido).
    W, H, R = 12.0, 4.0, 0.30
    pts: list[tuple[float, float]] = []
    esquinas = [(W - R, R, 270.0), (W - R, H - R, 0.0), (R, H - R, 90.0), (R, R, 180.0)]
    for cx, cy, a0 in esquinas:
        for k in range(21):
            ang = math.radians(a0 + 90.0 * (k / 20))
            pts.append((cx + R * math.cos(ang), cy + R * math.sin(ang)))
    pts.append(pts[0])

    rings = _offset_closed_profile_inches(pts, 0.00206, rectilinear=False)
    assert rings, "Clipper2 fallback debe producir al menos un ring"
    ring = max(rings, key=lambda r: (max(x for x, _ in r) - min(x for x, _ in r)))
    ancho = max(x for x, _ in ring) - min(x for x, _ in ring)
    alto = max(y for _, y in ring) - min(y for _, y in ring)
    assert abs(ancho - (W + 2 * 0.00206)) < 0.02, ancho
    assert abs(alto - (H + 2 * 0.00206)) < 0.02, alto


def test_offset_deforme_es_rechazado():
    """La compuerta debe tumbar un resultado con lazos/contorno abierto."""
    from modules.plasma_occt_offset import ring_is_simple, validate_offset_ring

    cuadro = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0), (0.0, 0.0)]
    lazo = [(0.0, 0.0), (10.0, 0.0), (0.0, 5.0), (10.0, 5.0), (0.0, 0.0)]
    assert not ring_is_simple(lazo)
    assert validate_offset_ring(cuadro, lazo, 0.0125)
    abierto = [(-0.0125, -0.0125), (10.0125, -0.0125), (10.0125, 5.0125), (3.0, 5.0125)]
    assert validate_offset_ring(cuadro, abierto, 0.0125)

    # Offset real: lados desplazados + arcos de unión de radio delta en esquinas.
    d = 0.0125
    bueno: list[tuple[float, float]] = []
    esquinas = [(10.0, 0.0, 270.0), (10.0, 5.0, 0.0), (0.0, 5.0, 90.0), (0.0, 0.0, 180.0)]
    for cx, cy, ang0 in esquinas:
        for k in range(31):
            ang = math.radians(ang0 + 90.0 * (k / 30))
            bueno.append((cx + d * math.cos(ang), cy + d * math.sin(ang)))
    bueno.append(bueno[0])
    assert validate_offset_ring(cuadro, bueno, d) == "", validate_offset_ring(cuadro, bueno, d)

    # Una esquina en punta (mitra) no es un offset: el vértice queda a d*raiz(2).
    mitra = [
        (-d, -d),
        (10.0 + d, -d),
        (10.0 + d, 5.0 + d),
        (-d, 5.0 + d),
        (-d, -d),
    ]
    assert validate_offset_ring(cuadro, mitra, d)


if __name__ == "__main__":
    test_occt_offset_preserva_primitivas()
    test_occt_offset_preserva_bulge_como_arc()
    test_occt_offset_parking_cw_no_brep_api()
    test_occt_offset_placa_con_filetes_y_slot_crece_exacto()
    test_micro_hueco_de_cad_se_puentea()
    test_contorno_muy_abierto_falla_cerrado()
    test_polilinea_cerrada_sobrevive_y_area_no_queda_en_cero()
    test_barreno_con_muesca_conserva_la_muesca_en_su_sitio()
    test_offset_deforme_es_rechazado()
    print("SMOKE OK")
