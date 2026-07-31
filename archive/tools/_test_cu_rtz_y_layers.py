"""
Prueba integración: nesteo láser normal + cobre (con_gap / sin_gap + RTZ).

Ejecutar: python tools/_test_cu_rtz_y_layers.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from shapely.geometry import Polygon

from modules.nest_exporter import export_nest_to_dxf
from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf
from modules.nesting_engine.cu_largos_nesting import (
    empaquetar_largos_cu,
    procesar_grupo_largos_cu,
)
from modules.nesting_engine.cu_rtz_sin_gap import (
    CU_SIN_GAP_LARGO_CORTE_MAX_IN,
    asignar_rtz_cu_sin_gap_ids,
)
from modules.nesting_engine.efficiency_metrics import contar_piezas_grupo, contar_piezas_hoja
from modules.nesting_engine.sheet_numbering import asignar_numeracion_global_hojas
from modules.nesting_engine.sheet_integrity import validar_colocacion_completa

COBRE_ONLY = frozenset({"CUT_CU", "BAR_START", "ARGA_META"})
RTZ_OVERLAY_PREFIXES = ("RETAZO_GUILLOTINA__RTZCU", "TATUAJE__RTZCU")


def _layers(path: str) -> set[str]:
    doc = ezdxf.readfile(path)
    return {str(l.dxf.name) for l in doc.layers}


def _used_layers(path: str) -> set[str]:
    doc = ezdxf.readfile(path)
    out: set[str] = set()
    for e in doc.modelspace():
        layer = getattr(e.dxf, "layer", None)
        if layer is None:
            continue
        out.add(str(layer).strip("'\""))
    return out


def _piece(nombre: str, largo_in: float, ancho_in: float = 4.0) -> dict:
    lx = largo_in * 25.4
    wy = ancho_in * 25.4
    poly = Polygon([(0, 0), (lx, 0), (lx, wy), (0, wy)])
    return {
        "nombre": nombre,
        "poly": poly,
        "marks": None,
        "area": float(poly.area),
        "calibre": "CU",
        "material": "CU",
        "ruta": "",
    }


def _barra_stock(ancho_in: float = 4.0, largo_in: float = 144.0) -> dict:
    w = ancho_in * 25.4
    h = largo_in * 25.4
    return {
        "id": f"CU-{ancho_in}x{largo_in}",
        "w": h,
        "h": w,
        "origen": "EMPRESA",
        "precio": 100.0,
    }


def test_laser_normal_sin_capas_cobre() -> None:
    sheet = {
        "length": 1854.0,
        "width": 914.0,
        "material": "A36",
        "thickness": 0.25,
        "arga_code": "TEST-STEEL",
    }
    placements = [
        {
            "part_name": "PIEZA-A",
            "outer": [(10, 10), (110, 10), (110, 60), (10, 60)],
            "holes": [[(30, 25), (50, 25), (50, 45), (30, 45)]],
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "laser.dxf")
        export_nest_to_dxf(
            out,
            sheet,
            placements,
            title="ROBOT LASER + MINI NEST",
            canal="ROBOT LASER + MINI NEST",
        )
        layers = _layers(out)
        leaked = layers & COBRE_ONLY
        assert not leaked, f"láser: capas cobre en tabla: {sorted(leaked)}"
        used = _used_layers(out)
        assert not (used & COBRE_ONLY), f"láser: geometría en capas cobre: {sorted(used & COBRE_ONLY)}"
        assert "CUT_OUTER" in used and "CUT_INNER" in used


def test_cobre_con_gap() -> None:
    sheet = {
        "length": 6000.0,
        "width": 40.0,
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "con_gap",
        "export_3d_format": "step",
    }
    placements = [
        {
            "part_name": "CU-1",
            "cu_largos_piece": True,
            "outer": [(0, 0), (100, 0), (100, 40), (0, 40)],
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "cobre_gap.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            placements,
            title="CU LARGOS",
            strict=False,
        )
        layers = _layers(out)
        used = _used_layers(out)
        assert "CUT_CU" not in used, "con_gap STEP: sin capa CUT_CU"
        assert "CUT_OUTER" in used, "con_gap STEP: contorno en CUT_OUTER"
        assert "BAR_START" in layers


def test_cobre_sin_gap_rtz_nesting() -> None:
    """Piezas cortas ≤114\" → sin_gap; overflow >114\" → madre sin_gap + RTZCU con gap real."""
    # 10 × 8\" = 80\" < 114\" → permanece sin_gap, sin RTZCU.
    piezas_cortas = [_piece(f"S{i}", 8.0) for i in range(10)]
    hojas_ok, sin_ok = empaquetar_largos_cu(
        piezas_cortas,
        [_barra_stock()],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin_ok
    assert hojas_ok
    assert hojas_ok[0].get("cu_modo_separacion_barra") == "sin_gap"

    # 16 × 8\" = 128\" > 114\": madre sigue sin_gap y genera RTZCU (con gap por defecto).
    piezas = [_piece(f"P{i}", 8.0) for i in range(16)]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        [_barra_stock()],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin, f"piezas sin colocar: {len(sin)}"
    assert hojas, "sin hojas generadas"

    resultados = {"0.25_CU": {"hojas": hojas}}
    asignar_numeracion_global_hojas(resultados, "W.O. TEST", sobrescribir=True)
    n_rtz = asignar_rtz_cu_sin_gap_ids(resultados)

    # La madre conserva sin_gap; el overflow >114\" se separa como RTZCU.
    madres = [h for h in hojas if not h.get("cu_rtz_virtual")]
    virtuales = [h for h in hojas if h.get("cu_rtz_virtual")]
    modos = {str(h.get("cu_modo_separacion_barra") or "") for h in madres}
    assert "sin_gap" in modos, f"madre debe seguir sin_gap, modos={modos}"
    assert n_rtz > 0, "overflow sin_gap >114\" debe crear RTZCU"
    assert virtuales, "sin hojas RTZCU virtuales"
    # RTZCU ya no hereda sin_gap: usa gap por defecto / STEP.
    assert all(
        v.get("cu_modo_separacion_barra") == "con_gap" for v in virtuales
    ), "RTZCU debe quedar con_gap"
    assert all(
        v.get("export_3d_format") == "step" for v in virtuales
    ), "RTZCU debe exportar STEP"

    # Piezas del RTZCU deben quedar re-espaciadas con gap (no pegadas).
    gap_mm = 0.375 * 25.4
    for v in virtuales:
        reales = [
            p
            for p in (v.get("piezas") or [])
            if isinstance(p, dict)
            and str(p.get("nombre") or "").startswith("P")
        ]
        if len(reales) < 2:
            continue
        xs = []
        for p in reales:
            pols = p.get("poligonos") or []
            if not pols or not pols[0]:
                continue
            xs.append(min(float(t[0]) for t in pols[0]))
        xs.sort()
        for a, b in zip(xs, xs[1:]):
            # Entre inicios: largo_pieza(8\") + gap; al menos gap entre fin e inicio.
            assert b - a >= 8.0 * 25.4 + gap_mm - 1.0, (
                f"RTZCU sin gap real entre piezas: dx={b - a:.1f}"
            )

    pool = [{"nombre": f"P{i}"} for i in range(16)]
    ok, msg = validar_colocacion_completa(pool, madres)
    assert ok, f"validación inventario falló: {msg}"


def test_orilla_no_entra_rtzcu_abre_barra() -> None:
    """Orilla rectangular SÍ puede pasar a RTZCU (ya no está prohibida)."""
    piezas = [_piece(f"OR{i}", 8.0, ancho_in=3.5) for i in range(20)]
    stock = [_barra_stock(4.0) for _ in range(8)]
    clave, res = procesar_grupo_largos_cu(
        "0.25_CU",
        piezas,
        stock,
        wo_name="W.O. ORILLA",
        exigir_colocacion_total=True,
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not res.get("error"), res.get("error")
    resultados = {clave: res}
    asignar_numeracion_global_hojas(resultados, "W.O. ORILLA", sobrescribir=True)
    n_rtz = asignar_rtz_cu_sin_gap_ids(resultados)
    assert n_rtz >= 1, "orilla rectangular debe poder crear RTZCU"
    virtuales = [h for h in (res.get("hojas") or []) if h.get("cu_rtz_virtual")]
    assert virtuales, "esperado RTZCU virtual con orillas"
    for v in virtuales:
        orillas = [
            p
            for p in (v.get("piezas") or [])
            if str((p or {}).get("nombre") or "").startswith("OR")
        ]
        assert orillas, "RTZCU debe poder contener orilla rectangular"


def test_relieve_z_no_entra_rtzcu() -> None:
    """Perfil Z/escalón: sin_gap, priorizado, y nunca en RTZCU."""
    lx = 12.0 * 25.4
    wy = 4.0 * 25.4

    def _z(nombre: str):
        poly = Polygon(
            [
                (0, 0),
                (lx, 0),
                (lx, wy * 0.45),
                (lx * 0.5, wy * 0.45),
                (lx * 0.5, wy),
                (0, wy),
            ]
        )
        return {
            "nombre": nombre,
            "poly": poly,
            "marks": None,
            "area": float(poly.area),
            "calibre": "CU",
            "material": "CU",
            "ruta": "",
        }

    piezas = [_z(f"Z{i}") for i in range(12)]
    stock = [_barra_stock(4.0) for _ in range(6)]
    clave, res = procesar_grupo_largos_cu(
        "0.25_CU",
        piezas,
        stock,
        wo_name="W.O. Z",
        exigir_colocacion_total=True,
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not res.get("error"), res.get("error")
    madres = [
        h
        for h in (res.get("hojas") or [])
        if isinstance(h, dict) and not h.get("cu_rtz_virtual") and not h.get("es_retazo")
    ]
    assert len(madres) >= 2, "relieve >114\" debe abrir barras nuevas"
    for h in madres:
        assert h.get("cu_modo_separacion_barra") == "sin_gap"
        for p in h.get("piezas") or []:
            if str(p.get("nombre") or "").startswith("Z"):
                assert p.get("cu_forzar_sin_gap") or p.get("cu_perfil_relieve")
                assert not p.get("cu_zona_rtz")
                pols = p.get("poligonos") or []
                if pols and pols[0]:
                    maxx = max(float(t[0]) for t in pols[0])
                    assert maxx <= CU_SIN_GAP_LARGO_CORTE_MAX_IN * 25.4 + 1.0

    resultados = {clave: res}
    asignar_numeracion_global_hojas(resultados, "W.O. Z", sobrescribir=True)
    asignar_rtz_cu_sin_gap_ids(resultados)
    for v in (res.get("hojas") or []):
        if not v.get("cu_rtz_virtual"):
            continue
        for p in v.get("piezas") or []:
            assert not str((p or {}).get("nombre") or "").startswith("Z")


def test_pieza_175_usa_barra_175_si_existe() -> None:
    """Con SCO028 (1.75\") en catálogo, pieza 1.75\" no debe subir a barra 2\"."""
    from modules.nesting_engine.cu_inventory import es_placa_largo_cu, inventario_barras_largos_cu

    bar_175 = _barra_stock(1.75)
    bar_2 = _barra_stock(2.0)
    assert es_placa_largo_cu(bar_175), "1.75x144 debe ser barra largos CU válida"
    inv = inventario_barras_largos_cu([bar_175, bar_2, _barra_stock(4.0)])
    anchos = sorted({round(min(float(p["w"]), float(p["h"])) / 25.4, 4) for p in inv})
    assert 1.75 in anchos, f"inventario debe incluir 1.75, got {anchos}"

    piezas = [_piece("P175", 12.0, ancho_in=1.75) for _ in range(3)]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        [bar_175, bar_2],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin and hojas
    madres = [h for h in hojas if not h.get("cu_rtz_virtual") and not h.get("es_retazo")]
    for h in madres:
        ancho_bar = float(h.get("placa_h") or 0.0) / 25.4
        assert abs(ancho_bar - 1.75) <= 0.02, (
            f"pieza 1.75 debe ir en barra 1.75, got Ancho={ancho_bar:.3f}\""
        )
        for p in h.get("piezas") or []:
            if str(p.get("nombre") or "").startswith("P175"):
                assert float(p.get("corte_superior_mm") or 0.0) <= 0.5, (
                    "no debe haber corte de orilla si la tira es exacta 1.75"
                )
                assert not p.get("cu_forzar_sin_gap"), (
                    "1.75 en 1.75 no debe forzar sin_gap por orilla"
                )


def test_rtz_derrame_abre_barra_nueva() -> None:
    """Si RTZCU con gap no cabe, derrama a barra nueva y conserva inventario."""
    # 36 × 4\" = 144\" pegadas (sin_gap). ~8 piezas caen past 114\";
    # con gap solo caben ~7 → 1+ derraman a barra nueva.
    piezas = [_piece(f"D{i}", 4.0) for i in range(36)]
    stock = [_barra_stock(4.0) for _ in range(6)]
    clave, res = procesar_grupo_largos_cu(
        "0.25_CU",
        piezas,
        stock,
        wo_name="W.O. DERRAME",
        exigir_colocacion_total=True,
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not res.get("error"), res.get("error")
    madres0 = [
        h
        for h in (res.get("hojas") or [])
        if isinstance(h, dict) and not h.get("es_retazo") and not h.get("cu_rtz_virtual")
    ]
    assert len(madres0) >= 2, f"esperado >=2 barras tras derrame, got {len(madres0)}"

    resultados = {clave: res}
    asignar_numeracion_global_hojas(resultados, "W.O. DERRAME", sobrescribir=True)
    n_rtz = asignar_rtz_cu_sin_gap_ids(resultados)
    assert n_rtz >= 1, "debe existir al menos un RTZCU en la barra madre llena"
    assert not res.get("cu_rtz_derrame_sin_barra"), res.get("cu_rtz_derrame_sin_barra")

    madres = [
        h
        for h in (res.get("hojas") or [])
        if isinstance(h, dict) and not h.get("cu_rtz_virtual")
    ]
    # Ninguna pieza real debe rebasar el largo físico de la solera.
    for h in madres:
        if h.get("es_retazo"):
            continue
        lim = float(h.get("placa_w") or 0.0) + 0.5
        for p in h.get("piezas") or []:
            if not isinstance(p, dict):
                continue
            nom = str(p.get("nombre") or "")
            if not nom.startswith("D"):
                continue
            pols = p.get("poligonos") or []
            if not pols or not pols[0]:
                continue
            maxx = max(float(t[0]) for t in pols[0])
            assert maxx <= lim, f"{nom} maxx={maxx/25.4:.2f}\" > barra {lim/25.4:.2f}\""

    pool = [{"nombre": f"D{i}"} for i in range(36)]
    ok, msg = validar_colocacion_completa(pool, madres)
    assert ok, f"inventario tras derrame: {msg}"


def test_consolidar_cola_barras_derrame() -> None:
    """Dos barras madre con poco avance en X (mismo ancho) se re-empaquetan juntas."""
    stock = [_barra_stock(6.0)]
    # PARTS ya trae avance 3.75" en X sobre tira de 6"; no se autoriza girarla.
    lote_a = [_piece(f"T{i}", 3.75, 6.0) for i in range(20)]
    lote_b = [_piece(f"U{i}", 3.75, 6.0) for i in range(10)]
    h_a, _ = empaquetar_largos_cu(lote_a, stock, separacion_in=0.375, largo_sin_separacion_in=10.0)
    h_b, _ = empaquetar_largos_cu(lote_b, stock, separacion_in=0.375, largo_sin_separacion_in=10.0)
    assert h_a and h_b
    from modules.nesting_engine.cu_largos_nesting import (
        _uso_longitudinal_hoja_cu,
        consolidar_barras_cu_baja_ocupacion,
    )

    hojas = list(h_a) + list(h_b)
    assert len(hojas) == 2
    usos_antes = [_uso_longitudinal_hoja_cu(h) for h in hojas]
    assert all(u < 0.85 for u in usos_antes)

    compactas = consolidar_barras_cu_baja_ocupacion(
        hojas,
        stock,
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    madres = [h for h in compactas if not h.get("cu_rtz_virtual") and not h.get("es_retazo")]
    assert len(madres) == 1, f"esperada 1 barra compacta, got {len(madres)}"
    assert _uso_longitudinal_hoja_cu(madres[0]) > max(usos_antes) - 0.05

    pool = [{"nombre": f"T{i}"} for i in range(20)] + [{"nombre": f"U{i}"} for i in range(10)]
    ok, msg = validar_colocacion_completa(pool, madres)
    assert ok, msg


def test_cobre_respeta_orientacion_parts() -> None:
    """Una pieza 5×2 visible en PARTS conserva 5\" en X; no se optimiza como 2×5."""
    piezas = [_piece(f"O{i}", 5.0, 2.0) for i in range(8)]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        [_barra_stock(6.0)],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin and hojas
    reales = [
        p
        for h in hojas
        for p in (h.get("piezas") or [])
        if str((p or {}).get("nombre") or "").startswith("O")
    ]
    assert len(reales) == 8
    for p in reales:
        pol = (p.get("poligonos") or [[]])[0]
        xs = [float(pt[0]) for pt in pol]
        ys = [float(pt[1]) for pt in pol]
        assert abs((max(xs) - min(xs)) / 25.4 - 5.0) < 0.02
        assert abs((max(ys) - min(ys)) / 25.4 - 2.0) < 0.02
        assert float(p.get("rot_deg") or 0.0) == 0.0


def test_pieza_2p0015_usa_barra_2in() -> None:
    """2.0015\" de DXF no debe saltar a tira 3\" si existe 2\"."""
    from modules.nesting_engine.cu_largos_nesting import _orientar_pieza_para_catalogo, _normalizar_barras

    poly = Polygon(
        [
            (0, 0),
            (5.0 * 25.4, 0),
            (5.0 * 25.4, 2.0015 * 25.4),
            (0, 2.0015 * 25.4),
        ]
    )
    catalogo = _normalizar_barras([_barra_stock(2.0), _barra_stock(3.0), _barra_stock(6.0)])
    oriented = _orientar_pieza_para_catalogo(poly, catalogo)
    assert oriented is not None
    _poly, _lx, _wy, barra, _corte, cal_sup, _rot = oriented
    assert abs(float(barra["ancho_in"]) - 2.0) <= 0.02, barra
    assert not cal_sup

    piezas = [
        {
            "nombre": "OVER2",
            "poly": poly,
            "marks": None,
            "area": float(poly.area),
            "calibre": "CU",
            "material": "CU",
            "ruta": "",
        }
    ]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        [_barra_stock(2.0), _barra_stock(3.0)],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin and hojas
    madres = [h for h in hojas if not h.get("cu_rtz_virtual")]
    assert abs(float(madres[0].get("placa_h") or 0.0) / 25.4 - 2.0) < 0.05


def test_no_sube_4in_a_6in_por_lote_mixto() -> None:
    """Piezas de 4\" no deben ir a solera 6\" solo porque el lote también tiene 6\"."""
    piezas = [_piece(f"W6-{i}", 16.0, 6.0) for i in range(2)] + [
        _piece(f"W4-{i}", 16.0, 4.0) for i in range(4)
    ]
    stock = [_barra_stock(4.0), _barra_stock(6.0)]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        stock,
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin and hojas
    madres = [h for h in hojas if not h.get("cu_rtz_virtual") and not h.get("es_retazo")]
    anchos = sorted({round(float(h.get("placa_h") or 0.0) / 25.4, 2) for h in madres})
    assert 4.0 in anchos, f"faltó tira 4\": {anchos}"
    assert 6.0 in anchos, f"faltó tira 6\": {anchos}"

    for h in madres:
        ancho_bar = float(h.get("placa_h") or 0.0) / 25.4
        for p in h.get("piezas") or []:
            nom = str((p or {}).get("nombre") or "")
            if not nom.startswith("W"):
                continue
            pol = (p.get("poligonos") or [[]])[0]
            ys = [float(pt[1]) for pt in pol]
            wid = (max(ys) - min(ys)) / 25.4
            if nom.startswith("W4-"):
                assert abs(wid - 4.0) < 0.05, nom
                assert abs(ancho_bar - 4.0) < 0.05, f"{nom} en barra {ancho_bar}\""
            if nom.startswith("W6-"):
                assert abs(wid - 6.0) < 0.05, nom
                assert abs(ancho_bar - 6.0) < 0.05, f"{nom} en barra {ancho_bar}\""


def test_reconciliar_no_consume_rtzcu_virtual() -> None:
    """sanitizar/reconciliar no debe comer piezas dos veces por RTZCU virtual."""
    from modules.nesting_engine.sheet_integrity import sanitizar_hojas_grupo

    pool = [{"nombre": f"P{i}"} for i in range(16)]
    piezas = [_piece(f"P{i}", 8.0) for i in range(16)]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        [_barra_stock()],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin and hojas
    resultados = {"0.25_CU": {"hojas": hojas, "piezas_pool": pool, "piezas_pool_engine": True}}
    asignar_numeracion_global_hojas(resultados, "W.O. RECON", sobrescribir=True)
    asignar_rtz_cu_sin_gap_ids(resultados)
    grp = resultados["0.25_CU"]
    n_antes = sum(
        1
        for h in grp["hojas"]
        if isinstance(h, dict) and not h.get("cu_rtz_virtual") and not h.get("es_retazo")
    )
    sanitizar_hojas_grupo(pool, grp["hojas"], clave="0.25_CU")
    n_despues = sum(
        1
        for h in grp["hojas"]
        if isinstance(h, dict) and not h.get("cu_rtz_virtual") and not h.get("es_retazo")
    )
    assert n_despues == n_antes, f"reconciliar eliminó barras: {n_antes} -> {n_despues}"
    ok, msg = validar_colocacion_completa(pool, grp["hojas"])
    assert ok, msg
    assert contar_piezas_grupo(grp) == 16


def test_conteo_no_duplica_rtzcu_virtual() -> None:
    """PIEZAS TOTALES / contar_piezas_grupo no suma piezas del RTZCU virtual."""
    from modules.nesting_engine.efficiency_metrics import contar_piezas_grupo, contar_piezas_hoja

    piezas = [_piece(f"P{i}", 8.0) for i in range(16)]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        [_barra_stock()],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin and hojas
    resultados = {"0.25_CU": {"hojas": hojas}}
    asignar_numeracion_global_hojas(resultados, "W.O. COUNT", sobrescribir=True)
    asignar_rtz_cu_sin_gap_ids(resultados)

    madres = [h for h in hojas if not h.get("cu_rtz_virtual")]
    virtuales = [h for h in hojas if h.get("cu_rtz_virtual")]
    assert virtuales, "debe haber RTZCU virtual para el caso overflow"
    n_madres = sum(contar_piezas_hoja(h) for h in madres)
    n_virt = sum(contar_piezas_hoja(h) for h in virtuales)
    assert n_virt > 0
    n_grupo = contar_piezas_grupo(resultados["0.25_CU"])
    assert n_grupo == 16, f"esperado 16, got {n_grupo} (madres={n_madres} virt={n_virt})"
    assert n_grupo == n_madres, "grupo debe igualar solo madres (sin virtual)"


def test_dxf_madre_excluye_piezas_rtzcu() -> None:
    """DXF placa madre no incluye piezas/cortes de zona RTZCU (van al STEP propio)."""
    from modules.nesting_engine.cu_rtz_sin_gap import pieza_excluida_dxf_madre_cu
    from modules.dxf_export.cobre_nest import _filtrar_placements_cobre, _preparar_sheet_cobre

    piezas = [_piece(f"P{i}", 8.0) for i in range(16)]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        [_barra_stock()],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin and hojas
    resultados = {"0.25_CU": {"hojas": hojas}}
    asignar_numeracion_global_hojas(resultados, "W.O. MADRE", sobrescribir=True)
    asignar_rtz_cu_sin_gap_ids(resultados)

    madre = next(h for h in hojas if not h.get("cu_rtz_virtual") and h.get("cu_rtz_activo"))
    virtual = next(h for h in hojas if h.get("cu_rtz_virtual"))
    n_rtz_flag = sum(
        1
        for p in (madre.get("piezas") or [])
        if isinstance(p, dict) and p.get("cu_zona_rtz") and str(p.get("nombre") or "").startswith("P")
    )
    assert n_rtz_flag > 0
    assert virtual.get("piezas")

    excluidas = [
        p
        for p in (madre.get("piezas") or [])
        if isinstance(p, dict) and pieza_excluida_dxf_madre_cu(p, madre)
    ]
    assert excluidas, "debe excluir piezas/cortes RTZ de la madre"
    assert all(
        pieza_excluida_dxf_madre_cu(p, madre) for p in excluidas
    )

    # Simula placements como el exporter (sin filtro) y aplica filtro cobre.
    placements_brutos = []
    for p in madre.get("piezas") or []:
        nom = str(p.get("nombre") or "")
        if not nom.startswith("P") and not nom.startswith("CU_CORTE__"):
            continue
        pols = p.get("poligonos") or []
        if not pols or not pols[0]:
            continue
        placements_brutos.append(
            {
                "part_name": nom,
                "outer": pols[0],
                "holes": [],
                "cu_largos_piece": nom.startswith("P"),
                "cu_zona_rtz": bool(p.get("cu_zona_rtz")),
            }
        )
    sheet = {
        "length": float(madre.get("placa_w") or 0),
        "width": float(madre.get("placa_h") or 0),
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
        "cu_rtz_activo": True,
        "cu_rtz_inicio_mm": float(madre.get("cu_rtz_inicio_mm") or 0),
        "placa_w": float(madre.get("placa_w") or 0),
    }
    sheet_prep = _preparar_sheet_cobre(sheet)
    assert float(sheet_prep["length"]) <= float(madre.get("cu_rtz_inicio_mm") or 0) + 0.5
    filtrados = _filtrar_placements_cobre(placements_brutos, sheet_prep)
    nombres_f = {str(p.get("part_name") or "") for p in filtrados}
    for p in madre.get("piezas") or []:
        if p.get("cu_zona_rtz") and str(p.get("nombre") or "").startswith("P"):
            assert str(p.get("nombre")) not in nombres_f

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "madre_no_rtz.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet_prep,
            filtrados,
            title="MADRE SIN RTZ",
            strict=False,
        )
        assert os.path.isfile(out)
        used = _used_layers(out)
        assert "CUT_CU" not in used
        assert "BAR_START" in used


def test_export_rtz_cu_dxf() -> None:
    """RTZCU: misma logica STEP que con_gap (horizontal, CUT_OUTER cerrado; no vertical)."""
    inicio = 114.0 * 25.4
    pieza_rtz = {
        "nombre": "P-OVER",
        "poligonos": [
            [
                (0.0, 0.0),
                (8.0 * 25.4, 0.0),
                (8.0 * 25.4, 4.0 * 25.4),
                (0.0, 4.0 * 25.4),
            ]
        ],
        "area": 8.0 * 4.0 * 25.4 * 25.4,
        "cu_sin_separacion": False,
        "cu_modo_separacion_barra": "con_gap",
    }
    madre = {
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "cu_rtz_activo": True,
        "cu_rtz_id": "RTZCU1-H3",
        "cu_rtz_inicio_mm": inicio,
        "cu_rtz_fin_piezas_mm": inicio + 8.0 * 25.4,
        "cu_rtz_largo_mm": 8.0 * 25.4,
        "cu_rtz_largo_in": 8.0,
        "cu_rtz_piezas_hoja": [pieza_rtz],
        "placa_h": 4 * 25.4,
        "sheet_seq": 3,
        "separacion_cu_in": 0.375,
    }
    from modules.nesting_engine.cu_rtz_sin_gap import construir_hoja_rtz_cu_virtual
    from modules.nest_exporter import (
        _sheet_export_sin_gap_vertical,
        _sheet_is_sin_gap,
        _sheet_cu_exporta_cortes_segmentados,
    )

    virtual = construir_hoja_rtz_cu_virtual(madre)
    assert virtual and virtual.get("cu_rtz_virtual")
    assert virtual.get("cu_modo_separacion_barra") == "con_gap"
    assert virtual.get("export_3d_format") == "step"

    sheet = {
        "length": float(virtual["placa_w"]),
        "width": float(virtual["placa_h"]),
        "modo_largos_cu": True,
        # Aunque alguien pase sin_gap, el canal cobre debe forzar STEP/con_gap.
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
        "cu_rtz_virtual": True,
    }
    from modules.dxf_export.cobre_nest import _preparar_sheet_cobre

    sheet_prep = _preparar_sheet_cobre(sheet)
    assert sheet_prep.get("cu_modo_separacion_barra") == "con_gap"
    assert sheet_prep.get("export_3d_format") == "step"
    assert not _sheet_is_sin_gap(sheet_prep)
    assert not _sheet_export_sin_gap_vertical(sheet_prep)
    assert not _sheet_cu_exporta_cortes_segmentados(sheet_prep)

    span = 8.0 * 25.4
    ancho = 4.0 * 25.4
    margin = 8.0
    cx, cy = span / 2.0, ancho / 2.0
    hole_r = 6.0
    placements = [
        {
            "part_name": "P-OVER",
            "cu_largos_piece": True,
            "outer": [
                (margin, margin),
                (span - margin, margin),
                (span - margin, ancho - margin),
                (margin, ancho - margin),
            ],
            "holes": [
                [
                    (cx - hole_r, cy - hole_r),
                    (cx + hole_r, cy - hole_r),
                    (cx + hole_r, cy + hole_r),
                    (cx - hole_r, cy + hole_r),
                ]
            ],
            "cu_bar_w_mm": ancho,
            "cu_bar_l_mm": span,
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "rtz_cu.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            placements,
            title="RTZ CU",
            strict=False,
        )
        assert os.path.isfile(out)
        used = _used_layers(out)
        assert "Plate" not in used, "RTZ cobre: sin capa Plate de acero"
        assert "CUT_OUTER" in used, "RTZCU STEP: CUT_OUTER cerrado por pieza"
        assert "CUT_CU" not in used, "RTZCU STEP: sin CUT_CU"
        assert "BAR_START" in used, "RTZCU: marcador inicio barra (horizontal)"
        doc = ezdxf.readfile(out)
        # Horizontal: bbox width (X) ~ largo pieza, height (Y) ~ ancho barra.
        xs, ys = [], []
        for e in doc.modelspace():
            if e.dxftype() == "LWPOLYLINE" and str(e.dxf.layer) == "CUT_OUTER":
                for pt in e.get_points("xy"):
                    xs.append(float(pt[0]))
                    ys.append(float(pt[1]))
        assert xs and ys
        assert max(xs) - min(xs) > max(ys) - min(ys), (
            "RTZCU no debe exportarse vertical (CyPTube); debe quedar horizontal como con_gap"
        )


def test_cobre_sin_gap_dxf_madre_sin_rtz_ni_cut_cu() -> None:
    piezas = [_piece(f"P{i}", 8.0) for i in range(10)]
    hojas, _ = empaquetar_largos_cu(
        piezas,
        [_barra_stock()],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    h = hojas[0]
    placements = []
    for p in h.get("piezas") or []:
        nom = str(p.get("nombre") or "")
        if p.get("cu_zona_rtz"):
            continue
        if nom.startswith("RETAZO_GUILLOTINA__") or nom.startswith("TATUAJE__"):
            if "RTZCU" in nom.upper():
                continue
        if nom.startswith("CU_CORTE__"):
            continue
        pols = p.get("poligonos") or []
        if not pols or not pols[0]:
            continue
        placements.append(
            {
                "part_name": nom,
                "cu_largos_piece": True,
                "outer": pols[0],
                "holes": pols[1:] if len(pols) > 1 else [],
                "marks": p.get("marcas") or [],
                "cu_slice_idx": int(p.get("cu_slice_idx", 0) or 0),
                "cu_slice_count": int(p.get("cu_slice_count", 1) or 1),
            }
        )

    sheet = {
        "length": float(h.get("placa_w") or 0),
        "width": float(h.get("placa_h") or 0),
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
    }
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "cobre_singap.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            placements,
            title="CU SIN GAP",
            strict=False,
        )
        layers = _layers(out)
        used = _used_layers(out)
        assert "CUT_CU" not in used, "sin_gap: CUT_CU no debe exportarse"
        assert "BAR_START" in used, "sin_gap: marcador inicio barra"
        for bad in RTZ_OVERLAY_PREFIXES:
            assert not any(bad in u for u in used), f"overlay en DXF: {used}"


def test_numeracion_cobre_ignora_rtz_virtual() -> None:
    """RTZCU virtual no debe consumir H global ni renumerarse como W.O.-H*."""
    from modules.nesting_engine.sheet_numbering import (
        asignar_numeracion_global_hojas,
        numeracion_hojas_es_consistente,
    )

    # Hoja madre sin_gap legacy con piezas past 114\" (RTZCU) para probar numeración.
    lim = CU_SIN_GAP_LARGO_CORTE_MAX_IN * 25.4
    piezas_hoja = []
    x = 0.0
    for i in range(8):
        L = 20.0 * 25.4
        piezas_hoja.append(
            {
                "nombre": f"P{i}",
                "poligonos": [[(x, 0), (x + L, 0), (x + L, 4 * 25.4), (x, 4 * 25.4), (x, 0)]],
                "marcas": [],
                "area": L * 4 * 25.4,
                "calibre": "CU",
                "material": "CU",
            }
        )
        x += L
    assert x > lim + 1.0

    hoja = {
        "piezas": piezas_hoja,
        "placa_id": "CU-4x144",
        "placa_w": 144.0 * 25.4,
        "placa_h": 4.0 * 25.4,
        "es_retazo": False,
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "sheet_uid": "uid-madre-1",
    }
    hojas = [hoja]

    wo = "W.O. 7 X6"
    resultados = {"0.25_CU": {"hojas": hojas, "modo_largos_cu": True}}
    asignar_numeracion_global_hojas(resultados, wo, sobrescribir=True)
    asignar_rtz_cu_sin_gap_ids(resultados)

    madres = [h for h in hojas if not h.get("cu_rtz_virtual")]
    virtuales = [h for h in hojas if h.get("cu_rtz_virtual")]
    assert madres, "sin hojas madre"
    assert virtuales, "sin hojas RTZ virtual"
    assert virtuales[0].get("cu_modo_separacion_barra") == "con_gap"

    max_h = max(int(h["sheet_seq"]) for h in madres)
    assert max_h == len(madres), f"madres deben ser H1..H{len(madres)}, max={max_h}"

    for v in virtuales:
        pid = str(v.get("placa_id") or v.get("sheet_code") or "")
        assert pid.startswith("RTZCU"), pid
        assert not pid.startswith(wo), f"RTZ virtual no debe ser W.O.-H: {pid}"

    # Simula refresco UI (procesar_lista_hojas): re-numerar con virtuales ya insertadas.
    asignar_numeracion_global_hojas(resultados, wo, sobrescribir=True)
    asignar_rtz_cu_sin_gap_ids(resultados)

    madres2 = [h for h in hojas if not h.get("cu_rtz_virtual")]
    virtuales2 = [h for h in hojas if h.get("cu_rtz_virtual")]
    max_h2 = max(int(h["sheet_seq"]) for h in madres2)
    assert max_h2 == len(madres2), f"tras refresco UI: max H={max_h2} madres={len(madres2)}"
    for v in virtuales2:
        pid = str(v.get("placa_id") or v.get("sheet_code") or "")
        assert pid.startswith("RTZCU"), pid
    assert numeracion_hojas_es_consistente(resultados, wo)


def test_corte_orilla_y_relieve_fuerzan_sin_gap() -> None:
    """Relieve/Z → sin_gap; orilla rectangular ya NO fuerza sin_gap; exactas largas → con_gap.

    Orilla/exactas SÍ pueden compartir barra con relieve si van a la cola RTZCU.
    """
    angostas = [_piece(f"N175-{i}", 16.0, 1.75) for i in range(4)]
    exactas = [_piece(f"E200-{i}", 16.0, 2.0) for i in range(4)]
    lx = 12.0 * 25.4
    wy = 2.0 * 25.4
    step = Polygon(
        [
            (0, 0),
            (lx, 0),
            (lx, wy * 0.55),
            (lx * 0.55, wy * 0.55),
            (lx * 0.55, wy),
            (0, wy),
        ]
    )
    escalon = {
        "nombre": "STEP-1",
        "poly": step,
        "marks": None,
        "area": float(step.area),
        "calibre": "CU",
        "material": "CU",
        "ruta": "",
    }
    stock = [_barra_stock(2.0)]
    hojas, sin = empaquetar_largos_cu(
        angostas + exactas + [escalon],
        stock,
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin and hojas
    madres = [h for h in hojas if not h.get("cu_rtz_virtual") and not h.get("es_retazo")]

    def _nombres(h):
        return [
            str(p.get("nombre") or "")
            for p in (h.get("piezas") or [])
            if str(p.get("nombre") or "").startswith(("N175-", "E200-", "STEP-"))
        ]

    for h in madres:
        noms = _nombres(h)
        if not noms:
            continue
        modo = str(h.get("cu_modo_separacion_barra") or "")
        has_narrow = any(n.startswith("N175-") for n in noms)
        has_exact = any(n.startswith("E200-") for n in noms)
        has_step = any(n.startswith("STEP-") for n in noms)
        if has_step:
            assert modo == "sin_gap", f"relieve debe sin_gap {noms} modo={modo}"
            for p in h.get("piezas") or []:
                n = str(p.get("nombre") or "")
                if n.startswith(("N175-", "E200-")):
                    assert p.get("cu_zona_rtz"), (
                        f"orilla/exacta en barra Z debe ir a RTZCU: {n}"
                    )
        if has_exact and not has_step:
            assert modo == "con_gap", f"exactas largas → con_gap, modo={modo} {noms}"
        if has_narrow and not has_step:
            assert modo == "con_gap", f"orilla larga → con_gap, modo={modo} {noms}"
            for p in h.get("piezas") or []:
                if str(p.get("nombre") or "").startswith("N175-"):
                    assert not p.get("cu_forzar_sin_gap")
                    assert float(p.get("corte_superior_mm") or 0) > 0.5


def test_z_rellena_cola_con_orilla_rtzcu() -> None:
    """Barras Z subutilizadas deben absorber orillas del mismo ancho como RTZCU."""
    lx = 10.0 * 25.4
    wy = 6.0 * 25.4

    def _z(nombre: str):
        poly = Polygon(
            [
                (0, 0),
                (lx, 0),
                (lx, wy * 0.45),
                (lx * 0.5, wy * 0.45),
                (lx * 0.5, wy),
                (0, wy),
            ]
        )
        return {
            "nombre": nombre,
            "poly": poly,
            "marks": None,
            "area": float(poly.area),
            "calibre": "CU",
            "material": "CU",
            "ruta": "",
        }

    # ~7 Z (70\") + orillas 5.25x3.75 que antes abrían barra propia tipo H257.
    piezas_z = [_z(f"Z{i}") for i in range(7)]
    orillas = [_piece(f"OR{i}", 5.25, 3.75) for i in range(24)]
    stock = [_barra_stock(6.0) for _ in range(12)]
    clave, res = procesar_grupo_largos_cu(
        "0.25_CU",
        piezas_z + orillas,
        stock,
        wo_name="W.O. FILL",
        exigir_colocacion_total=True,
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not res.get("error"), res.get("error")
    resultados = {clave: res}
    asignar_numeracion_global_hojas(resultados, "W.O. FILL", sobrescribir=True)
    asignar_rtz_cu_sin_gap_ids(resultados)

    madres = [
        h
        for h in (res.get("hojas") or [])
        if isinstance(h, dict) and not h.get("cu_rtz_virtual") and not h.get("es_retazo")
    ]
    virtuales = [h for h in (res.get("hojas") or []) if h.get("cu_rtz_virtual")]
    assert virtuales, "debe existir RTZCU con orillas en cola de Z"

    orillas_en_rtz = 0
    for v in virtuales:
        for p in v.get("piezas") or []:
            if str((p or {}).get("nombre") or "").startswith("OR"):
                orillas_en_rtz += 1
    assert orillas_en_rtz >= 8, (
        f"orillas en RTZCU={orillas_en_rtz}; se esperaba rellenar colas Z"
    )

    # No debe quedar una madre solo-orilla con 24 piezas si hubo cola Z libre.
    solo_orilla_llenas = 0
    for h in madres:
        noms = [
            str(p.get("nombre") or "")
            for p in (h.get("piezas") or [])
            if str(p.get("nombre") or "").startswith(("Z", "OR"))
        ]
        if noms and all(n.startswith("OR") for n in noms) and len(noms) >= 20:
            solo_orilla_llenas += 1
    assert solo_orilla_llenas == 0, (
        "no debe quedar barra casi llena solo de orillas si cabían en RTZCU de Z"
    )


def test_orden_barras_cu_menor_a_mayor_ancho() -> None:
    from modules.nesting_engine.cu_largos_nesting import ordenar_hojas_largos_cu_por_ancho

    piezas = (
        [_piece(f"A{i}", 8.0, 1.75) for i in range(2)]
        + [_piece(f"B{i}", 8.0, 6.0) for i in range(2)]
        + [_piece(f"C{i}", 8.0, 3.0) for i in range(2)]
    )
    stock = [
        _barra_stock(1.75),
        _barra_stock(3.0),
        _barra_stock(6.0),
    ]
    clave, res = procesar_grupo_largos_cu(
        "0.25_CU",
        piezas,
        stock,
        wo_name="W.O. SORT",
        exigir_colocacion_total=True,
    )
    assert not res.get("error"), res.get("error")
    madres = [
        h
        for h in (res.get("hojas") or [])
        if isinstance(h, dict) and not h.get("cu_rtz_virtual") and not h.get("es_retazo")
    ]
    anchos = [round(float(h.get("placa_h") or 0.0) / 25.4, 3) for h in madres]
    assert anchos == sorted(anchos), f"ancho UI/nest debe ascender: {anchos}"
    assert anchos[0] <= 1.76 and anchos[-1] >= 5.9

    mezcladas = list(reversed(res.get("hojas") or []))
    ordenadas = ordenar_hojas_largos_cu_por_ancho(mezcladas)
    anchos2 = [
        round(float(h.get("placa_h") or 0.0) / 25.4, 3)
        for h in ordenadas
        if isinstance(h, dict) and not h.get("cu_rtz_virtual") and not h.get("es_retazo")
    ]
    assert anchos2 == sorted(anchos2), anchos2


def main() -> int:
    test_laser_normal_sin_capas_cobre()
    print("[OK] 1/5 Nesteo láser normal: sin capas cobre fantasma")

    test_cobre_con_gap()
    print("[OK] 2/5 Cobre con_gap: CUT_OUTER cerrado + BAR_START (sin CUT_CU ni divisorias)")

    test_cobre_sin_gap_rtz_nesting()
    print("[OK] 3/7 Cobre: <=114\" sin_gap; overflow >114\" madre sin_gap + RTZCU con_gap")

    test_rtz_derrame_abre_barra_nueva()
    print("[OK] 4/8 RTZCU con gap: derrame abre barra nueva y conserva inventario")

    test_consolidar_cola_barras_derrame()
    print("[OK] 5/11 Cola CU: barras baja ocupacion se compactan")

    test_cobre_respeta_orientacion_parts()
    print("[OK] 6/12 Orientacion CU: 5x2 en PARTS permanece 5x2 en nesting")

    test_pieza_2p0015_usa_barra_2in()
    print("[OK] 7/12 Tolerancia ancho: 2.0015\" usa tira 2\" (no 3\")")

    test_no_sube_4in_a_6in_por_lote_mixto()
    print("[OK] 7b/13 Ancho CU: 4\" no sube a 6\" en lote mixto")

    test_corte_orilla_y_relieve_fuerzan_sin_gap()
    print("[OK] 7c/14 Relieve/Z => sin_gap; orilla en cola Z => RTZCU")

    test_z_rellena_cola_con_orilla_rtzcu()
    print("[OK] 7d/15 Cola Z se rellena con orillas RTZCU")

    test_orden_barras_cu_menor_a_mayor_ancho()
    print("[OK] 7e/16 Barras CU ordenadas 1.75→6 por ancho")

    test_orilla_no_entra_rtzcu_abre_barra()
    print("[OK] 7d/15 Orilla rectangular SÍ puede crear RTZCU")

    test_relieve_z_no_entra_rtzcu()
    print("[OK] 7d2/15 Relieve/Z: prioriza ≤114\" y no entra a RTZCU")

    test_pieza_175_usa_barra_175_si_existe()
    print("[OK] 7e/16 Pieza 1.75\" usa barra 1.75\" (no sube a 2\")")

    test_reconciliar_no_consume_rtzcu_virtual()
    print("[OK] 8/12 Reconciliar: RTZCU virtual no consume piezas del pool")

    test_conteo_no_duplica_rtzcu_virtual()
    print("[OK] 9/13 Conteo: RTZCU virtual no duplica PIEZAS TOTALES")

    test_dxf_madre_excluye_piezas_rtzcu()
    print("[OK] 10/13 DXF madre: excluye piezas/cortes zona RTZCU")

    test_export_rtz_cu_dxf()
    print("[OK] 11/13 DXF RTZCU: con_gap/STEP horizontal (CUT_OUTER cerrado, no vertical)")

    test_cobre_sin_gap_dxf_madre_sin_rtz_ni_cut_cu()
    print("[OK] 12/13 DXF madre sin_gap: BAR_START sí; CUT_CU y overlays RTZ no")

    test_numeracion_cobre_ignora_rtz_virtual()
    print("[OK] 13/13 Numeración cobre: RTZCU no consume H global (refresco UI)")

    print("\n=== TODAS LAS PRUEBAS PASARON ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
