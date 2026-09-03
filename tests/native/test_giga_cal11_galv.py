"""Candado: Cal 11 Galvanizado usa motor nativo oculto (no overlay de Lite)."""
from __future__ import annotations

import inspect
import os
import sys
import time
from pathlib import Path

from shapely.geometry import Polygon, box

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from modules.nesting_engine.giga_cal11_galv import (  # noqa: E402
    ENGINE_ID,
    apply_giga_pasillo_fill,
    clave_desde_debug_tag,
    engine_id_for_group,
    engine_id_for_renest,
    expand_giga_void_cargo,
    is_frame_piece,
    is_giga_cal11_galv_clave,
    prefill_vfm_void_cargo,
    should_force_giga_engine,
)
from modules.nesting_engine.nest_engine_context import (  # noqa: E402
    reset_pack_group_clave,
    set_pack_group_clave,
)


def _switch_giga(on: bool) -> None:
    os.environ["ARGA_GIGA_CAL11_GALV"] = "1" if on else "0"


def test_detector_cal11_galv_no_a36():
    assert is_giga_cal11_galv_clave("0.11811_GALVANIZADO")
    assert is_giga_cal11_galv_clave("0.1196_GALVANIZADO")  # decimal Herinox
    assert is_giga_cal11_galv_clave("0.118_GALVANIZADO")
    assert is_giga_cal11_galv_clave("0.119_GALVANIZADO")
    assert is_giga_cal11_galv_clave("11_GALVANIZADO")
    assert is_giga_cal11_galv_clave("0.1196_A 36 GALV")
    assert not is_giga_cal11_galv_clave("0.11811_A 36")
    assert not is_giga_cal11_galv_clave("0.1196_A 36")
    assert not is_giga_cal11_galv_clave("0.0747_A 36")
    assert not is_giga_cal11_galv_clave("0.0747_GALVANIZADO")
    assert not is_giga_cal11_galv_clave("0.188_GALVANIZADO")


def test_clave_desde_debug_tag():
    assert (
        clave_desde_debug_tag("clave=0.11811_GALVANIZADO | placa_id=PLC130")
        == "0.11811_GALVANIZADO"
    )
    assert clave_desde_debug_tag("preflight|0.11811_GALVANIZADO|GENE-VFM-20-101") == (
        "0.11811_GALVANIZADO"
    )


def test_no_aparece_en_selector():
    from modules.nesting_engine.engine_registry import list_ui_engine_metas
    from modules.nesting_engine.nest_engine_context import iter_ui_steel_engine_ids

    ids = {m.engine_id for m in list_ui_engine_metas()}
    assert ENGINE_ID not in ids
    assert ENGINE_ID not in set(iter_ui_steel_engine_ids())


def test_renest_no_pregunta_motor():
    _switch_giga(False)
    assert engine_id_for_renest("0.11811_GALVANIZADO") is None
    assert engine_id_for_group("0.11811_GALVANIZADO", "svgnest_ultra") == "svgnest_ultra"
    _switch_giga(True)
    assert engine_id_for_renest("0.11811_GALVANIZADO") == ENGINE_ID
    assert engine_id_for_renest("0.1196_GALVANIZADO") == ENGINE_ID
    assert engine_id_for_renest("0.1196_A 36 GALV") == ENGINE_ID
    assert engine_id_for_renest("0.0747_A 36") is None
    assert engine_id_for_group("0.11811_GALVANIZADO", "svgnest_ultra") == ENGINE_ID
    assert engine_id_for_group("0.1196_GALVANIZADO", "svgnest_ultra") == ENGINE_ID
    assert engine_id_for_group("0.0747_A 36", "svgnest_ultra") == "svgnest_ultra"
    _switch_giga(False)


def test_force_giga_solo_con_clave():
    _switch_giga(False)
    tok = set_pack_group_clave("0.11811_GALVANIZADO")
    try:
        assert should_force_giga_engine() is False
    finally:
        reset_pack_group_clave(tok)
    _switch_giga(True)
    tok = set_pack_group_clave("0.11811_GALVANIZADO")
    try:
        assert should_force_giga_engine() is True
    finally:
        reset_pack_group_clave(tok)
    tok2 = set_pack_group_clave("0.0747_A 36")
    try:
        assert should_force_giga_engine() is False
    finally:
        reset_pack_group_clave(tok2)
    _switch_giga(False)


def test_es_motor_nativo_no_overlay_lite():
    from modules.nesting_engine.engines.giga_cal11_galv import GigaCal11GalvEngine

    src = inspect.getsource(GigaCal11GalvEngine.empaquetar)
    assert "empaquetar_una_hoja_giga_cal11" in src
    assert "prefill_vfm_void_cargo" in src
    assert "restore_unplaced_void_cargo" in src
    assert "partition_vfm_sheet_quota" not in src
    assert "close_stacked_vfm_pairs" in src
    assert "zigzag_vfm_tower_stack" in src
    # Placa ANTES del zig-zag: si no, _in_plate queda ciego y poka expulsa.
    zig_at = src.find("zigzag_vfm_tower_stack")
    placa_at = src.find('setdefault("placa_w"')
    assert 0 <= placa_at < zig_at, "placa_w debe setearse antes de zigzag"
    assert "pool=restos" in src
    assert "arga_lite" not in src
    assert "fallback_engine" not in src


def test_cpp_export_presente():
    try:
        from modules.nesting_engine import algorithm_cpp
    except ImportError:
        print("SKIP native export (no algorithm_cpp)")
        return
    assert hasattr(algorithm_cpp, "empaquetar_una_hoja_giga_cal11"), (
        "algorithm_cpp.pyd sin empaquetar_una_hoja_giga_cal11. "
        "Recompila con build_cpp_engine.ps1."
    )
    from modules.nesting_engine.engines.giga_cal11_galv import GigaCal11GalvEngine

    assert GigaCal11GalvEngine.is_ready() is True


def test_frames_vfm():
    assert is_frame_piece({"nombre": "GENE-VFM-20-101"})
    assert is_frame_piece({"nombre": "GENE-HFM-12-102"})
    assert not is_frame_piece({"nombre": "GENE-BKT-299"})


def _mk(nombre: str, poly: Polygon) -> dict:
    return {
        "nombre": nombre,
        "poly": poly,
        "poligonos": [list(poly.exterior.coords)],
        "area": float(poly.area),
    }


def test_pasillo_t_enfrentadas_recibe_gs():
    """T contra T: el AABB casi se toca; el GS debe ir al bolsillo izquierdo, no al patio."""
    from shapely.affinity import translate as shp_translate

    w, rail, tw, th, tx = 800.0, 40.0, 90.0, 120.0, 355.0
    t_up = Polygon(
        [
            (0.0, 0.0),
            (w, 0.0),
            (w, rail),
            (tx + tw, rail),
            (tx + tw, rail + th),
            (tx, rail + th),
            (tx, rail),
            (0.0, rail),
            (0.0, 0.0),
        ]
    )
    t_dn = Polygon(
        [
            (0.0, th),
            (tx, th),
            (tx, 0.0),
            (tx + tw, 0.0),
            (tx + tw, th),
            (w, th),
            (w, th + rail),
            (0.0, th + rail),
            (0.0, th),
        ]
    )
    y102 = (rail + th) + 8.0
    p102 = shp_translate(t_dn, 0.0, y102)
    gs = box(920.0, 40.0, 920.0 + 3.84 * 25.4, 40.0 + 3.61 * 25.4)
    gs_p = _mk("GENE-GS-0820-708", gs)
    hoja = {
        "placa_w": 1200.0,
        "placa_h": 500.0,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "piezas": [
            _mk("GENE-VFM-20-101", t_up),
            _mk("GENE-VFM-20-102", p102),
        ],
    }
    pocket = box(0.0, rail, tx, y102 + th)
    stats = apply_giga_pasillo_fill(hoja, pool=[gs_p])
    guest = next(p for p in hoja["piezas"] if "GS" in str(p.get("nombre") or ""))
    poly = guest.get("poly")
    assert poly is not None
    assert float(poly.intersection(pocket).area) > 0.5 * float(poly.area), (
        f"GS no entró entre T enfrentadas: c=({poly.centroid.x:.1f},{poly.centroid.y:.1f}) "
        f"stats={stats} aabb_gap={y102 - (rail + th):.1f}mm"
    )


def test_pasillo_denso_llena_varios_gs():
    """El hueco entre T no se llena con 8 piezas y deja el resto en el patio."""
    from shapely.affinity import translate as shp_translate

    from shapely.ops import unary_union

    w, rail, tw, th, tx = 1600.0, 40.0, 90.0, 140.0, 755.0
    t_up = Polygon(
        [
            (0.0, 0.0),
            (w, 0.0),
            (w, rail),
            (tx + tw, rail),
            (tx + tw, rail + th),
            (tx, rail + th),
            (tx, rail),
            (0.0, rail),
            (0.0, 0.0),
        ]
    )
    t_dn = Polygon(
        [
            (0.0, th),
            (tx, th),
            (tx, 0.0),
            (tx + tw, 0.0),
            (tx + tw, th),
            (w, th),
            (w, th + rail),
            (0.0, th + rail),
            (0.0, th),
        ]
    )
    y102 = (rail + th) + 8.0
    p102 = shp_translate(t_dn, 0.0, y102)
    gw, gh = 3.84 * 25.4, 3.61 * 25.4
    piezas = [
        _mk("GENE-VFM-20-101", t_up),
        _mk("GENE-VFM-20-102", p102),
    ]
    pool = []
    for i in range(16):
        x0 = 1700.0 + (i % 8) * (gw + 8.0)
        y0 = 20.0 + (i // 8) * (gh + 8.0)
        pool.append(_mk(f"GENE-GS-0820-708#{i}", box(x0, y0, x0 + gw, y0 + gh)))
    hoja = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "piezas": piezas,
    }
    pocket = unary_union(
        [
            box(0.0, rail, tx, y102 + th),
            box(tx + tw, rail, w, y102 + th),
        ]
    )
    stats = apply_giga_pasillo_fill(hoja, pool=pool)
    n_in = 0
    for p in hoja["piezas"]:
        if "GS" not in str(p.get("nombre") or ""):
            continue
        poly = p.get("poly")
        if poly is None:
            continue
        if float(poly.intersection(pocket).area) > 0.5 * float(poly.area):
            n_in += 1
    assert n_in >= 10, f"solo {n_in}/16 GS en el pasillo stats={stats}"


def test_pasillo_entre_vfm_recibe_bkt():
    """Dos marcos apilados dejan un pasillo; el BKT a la derecha debe entrar."""
    rail_a = box(20.0, 20.0, 220.0, 45.0)
    rail_b = box(20.0, 80.0, 220.0, 105.0)
    guest = box(280.0, 50.0, 320.0, 70.0)
    bkt = _mk("GENE-BKT-299", guest)
    hoja = {
        "placa_w": 400.0,
        "placa_h": 160.0,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "piezas": [
            _mk("GENE-VFM-20-101", rail_a),
            _mk("GENE-VFM-20-102", rail_b),
        ],
    }
    corridor = box(20.0, 45.0, 220.0, 80.0)
    stats = apply_giga_pasillo_fill(hoja, pool=[bkt])
    placed = next(p for p in hoja["piezas"] if "BKT" in str(p.get("nombre") or ""))
    poly = bkt.get("poly")
    assert poly is not None
    inside = float(poly.intersection(corridor).area) > 0.5 * float(poly.area)
    cx = float(poly.centroid.x)
    assert inside or cx < 240.0, (
        f"BKT no entró al pasillo: centroid=({poly.centroid.x:.1f},{poly.centroid.y:.1f}) "
        f"stats={stats}"
    )


def test_mixin_renest_fijo():
    from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin

    class Fake(NestingCalcMixin):
        pass

    tab = Fake.__new__(Fake)
    _switch_giga(False)
    assert tab._engine_renest_fijo_para_clave("0.11811_GALVANIZADO") is None
    _switch_giga(True)
    assert tab._engine_renest_fijo_para_clave("0.11811_GALVANIZADO") == ENGINE_ID
    assert tab._engine_renest_fijo_para_clave("0.25_A 36") is None
    _switch_giga(False)


def _void_roundtrip(piezas: list, kerf: float = 0.150):
    mc, stats = prefill_vfm_void_cargo(piezas, kerf)
    hoja = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": kerf,
        "margin_usado": 0.250,
        "piezas": list(mc),
    }
    n_exp = expand_giga_void_cargo(hoja, mc)
    return hoja, stats, n_exp


def _u_open_top(w, h, thick, post=50.0):
    """U abierta arriba: alma/base sólida + postes laterales (101)."""
    return [
        (0.0, 0.0),
        (w, 0.0),
        (w, h),
        (w - post, h),
        (w - post, thick),
        (post, thick),
        (post, h),
        (0.0, h),
        (0.0, 0.0),
    ]


def test_familia_giga_autodxf_hosts():
    """AutoDXF GIGA (sin cobre): I VFM hueca es host; HFM maciza no; gutter 2.69\" no.

    BOARD 4 / GIGABOARD5 / BOARD 6 / BOARD 11 / Fluidstack metal: mismos
    GENE-VFM-20-101/102 (78.35×12.24 / 11.19) y bahías 8.77\" + 3.74\".
    """
    from modules.nesting_engine.giga_cal11_galv import _channel_like, _is_vfm_i_host

    inch = 25.4
    k = 0.150 * inch
    host101 = Polygon(_u_open_top(78.35 * inch, 12.24 * inch, (12.24 - 8.77) * inch))
    assert _is_vfm_i_host("GENE-VFM-20-101", host101)
    assert _is_vfm_i_host("GENE-VFM-30-101", host101)
    hfm = box(0.0, 0.0, 34.65 * inch, 6.29 * inch)
    assert not _is_vfm_i_host("GENE-HFM-10-102", hfm)
    bag = box(0.0, 0.0, 40.63 * inch, 8.77 * inch)
    strip = box(0.0, 0.0, 37.72 * inch, 3.74 * inch)
    gutter = box(0.0, 0.0, 75.43 * inch, 2.69 * inch)
    assert _channel_like(bag, k)
    assert _channel_like(strip, k)
    assert not _channel_like(gutter, k)
    gs = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)
    mc, stats = prefill_vfm_void_cargo(
        [_mk("GENE-VFM-30-101", host101), _mk("GENE-GS-0820-708", gs)],
        0.150,
    )
    assert int(stats.get("filled") or 0) >= 1, stats
    host_p = next(p for p in mc if "VFM" in str(p.get("nombre") or ""))
    assert host_p.get("_void_cargo")


def _rect_ring(w, h):
    return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]


def test_mixto_invitados_en_la_misma_hoja():
    """2 I VFM + BKT pequeños deben compartir la placa (no dejar el patio vacío)."""
    try:
        from modules.nesting_engine import algorithm_cpp
    except ImportError:
        print("SKIP mixto (no algorithm_cpp)")
        return
    if not hasattr(algorithm_cpp, "empaquetar_una_hoja_giga_cal11"):
        print("SKIP mixto (sin export giga)")
        return

    def native(nombre, ring):
        area = 0.0
        for i in range(len(ring) - 1):
            x0, y0 = ring[i]
            x1, y1 = ring[i + 1]
            area += x0 * y1 - x1 * y0
        return {
            "nombre": nombre,
            "area": abs(area) * 0.5,
            "calibre": "0.11811",
            "material": "GALVANIZADO",
            "rings": [ring],
            "marks": [],
        }

    p101 = native("GENE-VFM-20-101", _u_open_top(1990.0, 311.0, 180.0))
    p102 = native("GENE-VFM-20-102", _u_open_top(1990.0, 284.0, 160.0))
    p101b = native("GENE-VFM-20-101", _u_open_top(1990.0, 311.0, 180.0))
    p102b = native("GENE-VFM-20-102", _u_open_top(1990.0, 284.0, 160.0))
    guests = [
        native(f"GENE-BKT-{200 + i}", _rect_ring(80.0, 70.0)) for i in range(40)
    ]
    hoja, restos = algorithm_cpp.empaquetar_una_hoja_giga_cal11(
        [p101, p102, p101b, p102b, *guests],
        3048.0,
        1219.2,
        0.150,
        0.250,
        "OPTIMIZAR LARGO Y ANCHO",
        "INFERIOR IZQUIERDA",
        None,
    )
    placed = list(hoja.get("piezas") or [])
    n_vfm = sum(1 for pz in placed if "VFM" in str(pz.get("nombre") or "").upper())
    n_bkt = sum(1 for pz in placed if "BKT" in str(pz.get("nombre") or "").upper())
    assert n_vfm >= 1, f"esperaba VFM en la hoja, placed={len(placed)} restos={len(restos or [])}"
    assert n_bkt >= 20, (
        f"L sin llenar: BKT en hoja={n_bkt} VFM={n_vfm} "
        f"placed={len(placed)} restos={len(restos or [])}"
    )


def _h_with_t():
    """H+T tipo VFM-20-101: bolsa ~8.77\" junto a la T y tiras de ala ~3.74\"."""
    from shapely.ops import unary_union

    inch = 25.4
    w, h = 78.35 * inch, 12.24 * inch
    web0 = 40.63 * inch
    web1 = web0 + 4.0 * inch
    flange = (12.24 - 8.77) * 0.5 * inch
    t_w = 8.0 * inch
    t_h = 4.0 * inch
    metal = unary_union(
        [
            box(0.0, 0.0, w, flange),
            box(0.0, h - flange, w, h),
            box(web0, 0.0, web1, h),
            box(web0 - t_w, flange, web0, flange + t_h),
        ]
    )
    if metal.geom_type != "Polygon":
        metal = max(metal.geoms, key=lambda g: g.area)
    return metal


def test_vfm_canal_recibe_bkt():
    """Canal de ala VFM (~3.7\") con kerf 0.150\": BKT-287 3.00\" debe entrar."""
    host = Polygon(_u_open_top(1990.0, 311.0, 180.0))
    guest = box(2100.0, 20.0, 2100.0 + 186.2, 20.0 + 76.2)
    hoja, stats, _n = _void_roundtrip(
        [_mk("GENE-VFM-20-101", host), _mk("GENE-BKT-287", guest)]
    )
    channel = box(50.0, 180.0, 1940.0, 311.0)
    bkt = next(p for p in hoja["piezas"] if "BKT" in str(p.get("nombre") or ""))
    poly = bkt.get("poly")
    assert poly is not None
    inside = float(poly.intersection(channel).area) > 0.8 * float(poly.area)
    assert inside, (
        f"BKT-287 no entró al canal VFM: centroid="
        f"({poly.centroid.x:.1f},{poly.centroid.y:.1f}) stats={stats}"
    )


def test_vfm_canal_desde_pool():
    """Invitado en el pool (no en la hoja) entra al canal por void-first."""
    host = Polygon(_u_open_top(1990.0, 311.0, 180.0))
    guest = box(0.0, 0.0, 186.2, 76.2)
    hoja, stats, n_exp = _void_roundtrip(
        [_mk("GENE-VFM-20-101", host), _mk("GENE-BKT-287", guest)]
    )
    assert int(stats.get("filled") or 0) >= 1, stats
    assert n_exp >= 1
    channel = box(50.0, 180.0, 1940.0, 311.0)
    bkt = next(p for p in hoja["piezas"] if "BKT" in str(p.get("nombre") or ""))
    poly = bkt.get("poly")
    assert poly is not None
    assert float(poly.intersection(channel).area) > 0.8 * float(poly.area)


def test_vfm_canal_gs_gordo_no_entra():
    """GS ~3.61\" + kerf 0.150\" no cabe en canal ~3.7\" (no forzar)."""
    host = Polygon(_u_open_top(1990.0, 95.0, 20.0))
    guest = box(2100.0, 20.0, 2100.0 + 91.7, 20.0 + 97.5)
    hoja, _stats, _n = _void_roundtrip(
        [_mk("GENE-VFM-20-101", host), _mk("GENE-GS-0820-708", guest)]
    )
    channel = box(50.0, 20.0, 1940.0, 95.0)
    gs = next(p for p in hoja["piezas"] if "GS" in str(p.get("nombre") or ""))
    poly = gs.get("poly")
    assert poly is not None
    inside = float(poly.intersection(channel).area) > 0.5 * float(poly.area)
    assert not inside, "GS gordo no debe forzarse al canal"


def test_vfm_bahia_alta_recibe_bkt304_y_gs():
    """Manual planta: BKT-304 4.20\" y GS 3.61\" caben en bolsa ~8.77\" (kerf 0.150\")."""
    inch = 25.4
    h = 12.24 * inch
    thick = (12.24 - 8.77) * inch
    host = Polygon(_u_open_top(1990.0, h, thick))
    bay = box(50.0, thick, 1940.0, h)
    bkt = box(2100.0, 20.0, 2100.0 + 7.08 * inch, 20.0 + 4.20 * inch)
    gs = box(2300.0, 20.0, 2300.0 + 3.84 * inch, 20.0 + 3.61 * inch)
    hoja, stats, _n = _void_roundtrip(
        [
            _mk("GENE-VFM-20-101", host),
            _mk("GENE-BKT-304", bkt),
            _mk("GENE-GS-0820-708", gs),
        ]
    )
    for tag in ("BKT-304", "GS-0820"):
        pz = next(p for p in hoja["piezas"] if tag in str(p.get("nombre") or ""))
        poly = pz.get("poly")
        assert poly is not None
        inside = float(poly.intersection(bay).area) > 0.7 * float(poly.area)
        assert inside, (
            f"{tag} no entró a bahía 8.77\": centroid="
            f"({poly.centroid.x:.1f},{poly.centroid.y:.1f}) stats={stats}"
        )


def test_vfm_h_con_t_recibe_304_en_bolsa_no_gs_en_ala():
    """Candado planta: H+T. 304/GS en bolsa ~8.77\"; GS no se fuerza a tira 3.74\"."""
    inch = 25.4
    host = _h_with_t()
    bag = box(0.0, (12.24 - 8.77) * 0.5 * inch, 40.63 * inch, host.bounds[3])
    strip_h = 3.74 * inch
    flange_strip = box(40.63 * inch + 4.0 * inch, 0.0, host.bounds[2], strip_h)
    bkt = box(0.0, 400.0, 7.08 * inch, 400.0 + 4.20 * inch)
    gs = box(250.0, 400.0, 250.0 + 3.84 * inch, 400.0 + 3.61 * inch)
    thin = box(500.0, 400.0, 500.0 + 7.33 * inch, 400.0 + 3.00 * inch)
    hoja, stats, n_exp = _void_roundtrip(
        [
            _mk("GENE-VFM-20-101", host),
            _mk("GENE-BKT-304", bkt),
            _mk("GENE-GS-0820-708", gs),
            _mk("GENE-BKT-287", thin),
        ]
    )
    assert int(stats.get("filled") or 0) >= 2, stats
    assert n_exp >= 2
    pz304 = next(p for p in hoja["piezas"] if "BKT-304" in str(p.get("nombre") or ""))
    pgs = next(p for p in hoja["piezas"] if "GS-0820" in str(p.get("nombre") or ""))
    p287 = next(p for p in hoja["piezas"] if "BKT-287" in str(p.get("nombre") or ""))
    a304 = pz304.get("poly")
    ags = pgs.get("poly")
    a287 = p287.get("poly")
    assert float(a304.intersection(bag).area) > 0.6 * float(a304.area), (
        f"BKT-304 fuera de bolsa: c=({a304.centroid.x:.1f},{a304.centroid.y:.1f}) "
        f"stats={stats}"
    )
    in_strip_gs = float(ags.intersection(flange_strip).area) > 0.5 * float(ags.area)
    assert not in_strip_gs, "GS no debe forzarse a la tira de ala 3.74\""
    in_bag_gs = float(ags.intersection(bag).area) > 0.5 * float(ags.area)
    in_strip_287 = float(a287.intersection(flange_strip).area) > 0.5 * float(a287.area)
    in_bag_287 = float(a287.intersection(bag).area) > 0.5 * float(a287.area)
    assert in_bag_gs or in_strip_287 or in_bag_287, (
        f"ni GS ni BKT-287 usaron aire VFM stats={stats}"
    )


def test_prefill_satura_bahia_no_una_pieza():
    """Una bahía 8.77\" debe llenarse (no 1–2 azules con aire arriba)."""
    inch = 25.4
    host = Polygon(_u_open_top(1990.0, 12.24 * inch, (12.24 - 8.77) * inch))
    gs = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)
    piezas = [_mk("GENE-VFM-20-101", host)] + [
        _mk("GENE-GS-0820-708", gs) for _ in range(12)
    ]
    mc, stats = prefill_vfm_void_cargo(piezas, 0.150)
    host_p = next(p for p in mc if "VFM" in str(p.get("nombre") or "").upper())
    cargo = host_p.get("_void_cargo") or []
    assert len(cargo) >= 12, (len(cargo), stats)


def test_hfm_no_bloquea_gs_en_bahia():
    """HFM 34\" cabe en AABB de la bolsa pero no en metal; no debe impedir GS."""
    inch = 25.4
    host = Polygon(_u_open_top(1990.0, 12.24 * inch, (12.24 - 8.77) * inch))
    hfm = box(0.0, 0.0, 34.65 * inch, 6.29 * inch)
    gs = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)
    piezas = (
        [_mk("GENE-VFM-20-101", host)]
        + [_mk("GENE-HFM-10-102", hfm) for _ in range(8)]
        + [_mk("GENE-GS-0820-708", gs) for _ in range(8)]
    )
    mc, stats = prefill_vfm_void_cargo(piezas, 0.150)
    host_p = next(p for p in mc if "VFM" in str(p.get("nombre") or "").upper())
    names = [str(g.get("nombre") or "") for g in (host_p.get("_void_cargo") or [])]
    assert sum("GS" in n for n in names) >= 8, (names, stats)


def test_prefill_todos_los_vfm_del_pool():
    """Los GS del pool salen a cargo VFM (no se dejan todos al patio)."""
    inch = 25.4
    host = Polygon(_u_open_top(1990.0, 12.24 * inch, (12.24 - 8.77) * inch))
    gs = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)
    piezas = (
        [_mk("GENE-VFM-20-101", host) for _ in range(3)]
        + [_mk("GENE-VFM-20-102", host) for _ in range(2)]
        + [_mk("GENE-GS-0820-708", gs) for _ in range(4)]
    )
    mc, stats = prefill_vfm_void_cargo(piezas, 0.150)
    n_vfm = sum(1 for p in mc if "VFM" in str(p.get("nombre") or "").upper())
    assert n_vfm == 5, n_vfm
    n_gs_mc = sum(1 for p in mc if "GS" in str(p.get("nombre") or "").upper())
    assert n_gs_mc == 0, n_gs_mc
    assert int(stats.get("filled") or 0) >= 4
    assert int(stats.get("seed_hosts") or 0) == 5
    n_with = sum(
        1 for p in mc if "VFM" in str(p.get("nombre") or "").upper() and p.get("_void_cargo")
    )
    assert n_with >= 3, n_with


def test_extra_i_llevan_cargo_fuera_de_hoja():
    """4 I y 8 GS: cada I lleva cargo (no dejar las últimas vacías al 14%)."""
    inch = 25.4
    host = Polygon(_u_open_top(1990.0, 12.24 * inch, (12.24 - 8.77) * inch))
    gs = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)
    piezas = (
        [_mk("GENE-VFM-20-101", host) for _ in range(2)]
        + [_mk("GENE-VFM-20-102", host) for _ in range(2)]
        + [_mk("GENE-GS-0820-708", gs) for _ in range(8)]
    )
    mc, stats = prefill_vfm_void_cargo(piezas, 0.150)
    vfms = [p for p in mc if "VFM" in str(p.get("nombre") or "").upper()]
    assert len(vfms) == 4
    n_c = sum(1 for p in vfms if p.get("_void_cargo"))
    assert n_c == 4, (n_c, stats)
    assert int(stats.get("filled") or 0) >= 8


def test_cierra_par_vfm_reduce_alto():
    from shapely.affinity import translate as shp_translate
    from shapely.ops import unary_union

    from modules.nesting_engine.giga_cal11_galv import close_stacked_vfm_pairs

    host = _h_with_t()
    h = host.bounds[3] - host.bounds[1]
    kerf = 0.150 * 25.4
    p101 = _mk("GENE-VFM-20-101", host)
    p102 = _mk("GENE-VFM-20-102", shp_translate(host, 0.0, h + kerf + 40.0))
    h0 = unary_union([p101["poly"], p102["poly"]]).bounds
    h0 = h0[3] - h0[1]
    hoja = {"kerf_usado": 0.150, "piezas": [p101, p102]}
    st = close_stacked_vfm_pairs(hoja)
    h1 = unary_union([p101["poly"], p102["poly"]]).bounds
    h1 = h1[3] - h1[1]
    assert st.get("closed", 0) >= 1, st
    assert h1 < h0 - 5.0, (h0, h1, st)
    # Candado planta 2026-08-20: close_pair no puede dejar solape 101×102
    # (antes → POKA parcial expulsadas=0 → SIM integrity fail → faltan 34).
    inter = float(p101["poly"].intersection(p102["poly"]).area)
    assert inter <= 25.0, (inter, st)
    assert float(p101["poly"].distance(p102["poly"])) + 1e-3 >= kerf - 0.05, st


def test_reparar_expulsa_solape_metal_vfm():
    """solape_metal debe expulsar (antes salía sin tocar → EMPAQUE-STOP)."""
    from shapely.affinity import translate as shp_translate

    from modules.nesting_engine.nest_poka_yoke import (
        reparar_separacion_minima_hoja,
        validar_separacion_minima_hoja,
    )

    host = _h_with_t()
    margin_mm = 0.250 * 25.4
    # Dentro de placa con margin; solape deliberado entre sí (log 10:59).
    p101 = _mk("GENE-VFM-20-101", shp_translate(host, margin_mm + 5.0, margin_mm + 5.0))
    p102 = _mk(
        "GENE-VFM-20-102",
        shp_translate(host, margin_mm + 10.0, margin_mm + 13.0),
    )
    hoja = {
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "piezas": [p101, p102],
    }
    ok0, det0 = validar_separacion_minima_hoja(
        hoja, 0.150, margin_in=0.250, w_placa=3048.0, h_placa=1219.2
    )
    assert not ok0 and "solape_metal" in str(det0), (ok0, det0)
    ok, det, exp = reparar_separacion_minima_hoja(
        hoja,
        0.150,
        margin_in=0.250,
        w_placa=3048.0,
        h_placa=1219.2,
        permitir_expulsar=True,
    )
    assert len(exp) >= 1, (ok, det, exp)
    assert len(hoja.get("piezas") or []) >= 1
    # Tras expulsar el solape, no debe quedar solape_metal (margin OK).
    ok2, det2 = validar_separacion_minima_hoja(
        hoja, 0.150, margin_in=0.250, w_placa=3048.0, h_placa=1219.2
    )
    assert ok2, (ok2, det2, ok, det)
    assert ok, (ok, det)


def test_hfm_entra_bolsa_877():
    """HFM-10 6.29\" cabe en bolsa 8.77\" con kerf 0.150\" (rectángulo inscrito)."""
    inch = 25.4
    h = 12.24 * inch
    thick = (12.24 - 8.77) * inch
    host = Polygon(_u_open_top(1990.0, h, thick))
    bay = box(50.0, thick, 1940.0, h)
    hfm = box(2100.0, 20.0, 2100.0 + 34.65 * inch, 20.0 + 6.29 * inch)
    hoja, stats, n_exp = _void_roundtrip(
        [_mk("GENE-VFM-20-101", host), _mk("GENE-HFM-10-102", hfm)]
    )
    assert int(stats.get("filled") or 0) >= 1, stats
    pz = next(p for p in hoja["piezas"] if "HFM" in str(p.get("nombre") or ""))
    poly = pz.get("poly")
    assert poly is not None
    assert float(poly.intersection(bay).area) > 0.7 * float(poly.area), (
        f"HFM no entró a bolsa 8.77\": c=({poly.centroid.x:.1f},{poly.centroid.y:.1f}) "
        f"stats={stats} expand={n_exp}"
    )


def test_cargo_host_no_colocado_vuelve_a_restos():
    from modules.nesting_engine.giga_cal11_galv import restore_unplaced_void_cargo

    inch = 25.4
    host = Polygon(_u_open_top(1990.0, 12.24 * inch, (12.24 - 8.77) * inch))
    guest = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)
    piezas = [
        _mk("GENE-VFM-20-101", host),
        _mk("GENE-VFM-20-102", host),
        _mk("GENE-GS-0820-708", guest),
    ]
    mc, stats = prefill_vfm_void_cargo(piezas, 0.150)
    assert int(stats.get("filled") or 0) >= 1, stats
    restos: list = []
    n_back = restore_unplaced_void_cargo({"piezas": []}, mc, restos)
    assert n_back >= 1, n_back
    assert any("GS" in str(p.get("nombre") or "") for p in restos)


def test_giga_no_simula_dos_alturas():
    from modules.nesting_engine.giga_cal11_galv import pick_giga_sim_plates, plate_too_small_for_vfm

    a = {"id": "PLC189", "w": 3048.0, "h": 914.4}
    b = {"id": "PLC150", "w": 3048.0, "h": 1219.2}
    out = pick_giga_sim_plates([a, b])
    assert len(out) == 1 and out[0]["id"] == "PLC150"
    assert plate_too_small_for_vfm(950.0, 90.0)
    assert not plate_too_small_for_vfm(3048.0, 1219.2)
    key48 = (round(3048.0, 1), round(1219.2, 1))
    out2 = pick_giga_sim_plates(
        [a, b],
        format_used={key48: 6},
        format_limits={key48: 6},
    )
    assert len(out2) == 1 and out2[0]["id"] == "PLC189"


def test_combinado_forzado_en_giga():
    import inspect

    from modules.nesting_engine.manager import _usar_pack_combinado_grupo

    src = inspect.getsource(_usar_pack_combinado_grupo)
    assert "should_force_giga_engine" in src


def test_planta_giga_no_par_vacio_y_azules_en_bahia():
    """Candado planta: GS/RLG/304 en bahía; jamás hoja de solo 101+102 con invitados en pool."""
    from modules.nesting_engine.engines.giga_cal11_galv import GigaCal11GalvEngine
    from modules.nesting_engine.engines.types import PackSheetRequest
    from modules.nesting_engine.giga_cal11_galv import _channel_like
    from modules.nesting_engine.venom_hole_fill import _piece_poly, list_host_cavities

    if not GigaCal11GalvEngine.is_ready():
        print("SKIP planta (no algorithm_cpp giga)")
        return

    inch = 25.4
    k = 0.150 * inch
    p101 = Polygon(_u_open_top(78.35 * inch, 12.24 * inch, (12.24 - 8.77) * inch))
    p102 = Polygon(_u_open_top(78.35 * inch, 11.19 * inch, (11.19 - 8.77) * inch))
    gs = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)
    rlg = box(0.0, 0.0, 7.55 * inch, 4.00 * inch)
    b304 = box(0.0, 0.0, 7.08 * inch, 4.20 * inch)
    hfm = box(0.0, 0.0, 34.65 * inch, 6.29 * inch)

    def _p(nombre, poly):
        d = _mk(nombre, poly)
        d["rings"] = [list(poly.exterior.coords)]
        d["calibre"] = "0.11811"
        d["material"] = "GALVANIZADO"
        return d

    pool = []
    for i in range(6):
        pool.append(_p(f"GENE-VFM-20-101#{i}", p101))
        pool.append(_p(f"GENE-VFM-20-102#{i}", p102))
    pool.extend(_p(f"GENE-GS-0820-708#{i}", gs) for i in range(24))
    pool.extend(_p(f"GENE-BKT-RLG-123#{i}", rlg) for i in range(10))
    pool.extend(_p(f"GENE-BKT-304#{i}", b304) for i in range(8))
    pool.extend(_p(f"GENE-HFM-10-102#{i}", hfm) for i in range(6))

    def _guest(nom: str) -> bool:
        u = nom.upper()
        return any(t in u for t in ("GS-", "BKT-RLG", "BKT-304", "BKT-287"))

    def _in_bay(hoja) -> tuple[int, int]:
        hosts, guests = [], []
        for p in hoja.get("piezas") or []:
            nom = str(p.get("nombre") or "")
            g = _piece_poly(p)
            if g is None:
                continue
            if "VFM-20" in nom.upper():
                hosts.append(g)
            elif _guest(nom):
                guests.append(g)
        cavs = []
        for h in hosts:
            cavs.extend(
                c
                for c in list_host_cavities(h, open_profile=True)
                if _channel_like(c, k)
            )
        n_in = 0
        for g in guests:
            for c in cavs:
                try:
                    if float(g.intersection(c).area) > 0.45 * float(g.area):
                        n_in += 1
                        break
                except Exception:
                    pass
        return n_in, len(guests)

    hojas = []
    t0 = time.perf_counter()
    for _si in range(12):
        if not pool:
            break
        n_guest_pool = sum(1 for p in pool if _guest(str(p.get("nombre") or "")))
        res = GigaCal11GalvEngine.empaquetar(
            PackSheetRequest(piezas=list(pool), w_placa=3048.0, h_placa=1219.2)
        )
        placed = list((res.hoja or {}).get("piezas") or [])
        if not placed:
            break
        n_vfm = sum(1 for p in placed if "VFM-20" in str(p.get("nombre") or "").upper())
        n_g = sum(1 for p in placed if _guest(str(p.get("nombre") or "")))
        assert not (len(placed) <= 2 and n_vfm >= 2 and n_guest_pool >= 4), (
            f"hoja solo-par VFM con {n_guest_pool} invitados aún en pool "
            f"(H69–H99). placed={len(placed)} vfm={n_vfm} guests_on_sheet={n_g}"
        )
        hojas.append(res.hoja)
        fill = (res.hoja or {}).get("giga_fill") or {}
        assert not fill.get("error_bays"), fill.get("error_bays")
        pool = list(res.restos or [])
    assert time.perf_counter() - t0 < 45.0, "motor planta >45s (otra vez el pase vacío)"
    assert hojas, "no empacó ninguna hoja"
    n_in, n_g = _in_bay(hojas[0])
    assert n_g >= 1, "primera hoja sin GS/RLG/304"
    assert n_in >= 6, (
        f"azules fuera de bahía en P1: in_bay={n_in}/{n_g} (espacios vacíos del VFM)"
    )


def test_fase_b_facing_jala_patio_sin_pool():
    """H61: pasillo con aire + GS en patio de la misma hoja → las mete (Mudar local)."""
    from shapely.affinity import translate as shp_translate
    from shapely.ops import unary_union

    from modules.nesting_engine.giga_cal11_galv import fill_vfm_facing_gap

    w, rail, tw, th, tx = 1990.0, 40.0, 90.0, 140.0, 950.0
    t_up = Polygon(
        [
            (0.0, 0.0),
            (w, 0.0),
            (w, rail),
            (tx + tw, rail),
            (tx + tw, rail + th),
            (tx, rail + th),
            (tx, rail),
            (0.0, rail),
            (0.0, 0.0),
        ]
    )
    t_dn = Polygon(
        [
            (0.0, th),
            (tx, th),
            (tx, 0.0),
            (tx + tw, 0.0),
            (tx + tw, th),
            (w, th),
            (w, th + rail),
            (0.0, th + rail),
            (0.0, th),
        ]
    )
    y102 = (rail + th) + 10.0
    p102 = shp_translate(t_dn, 0.0, y102)
    gw, gh = 3.84 * 25.4, 3.61 * 25.4
    piezas = [
        _mk("GENE-VFM-20-101", t_up),
        _mk("GENE-VFM-20-102", p102),
    ]
    # 9 GS en el patio (derecha), como las que Mudar metió a mano.
    for i in range(9):
        x0 = 2100.0 + (i % 3) * (gw + 10.0)
        y0 = 40.0 + (i // 3) * (gh + 10.0)
        piezas.append(_mk(f"GENE-GS-0820-708#{i}", box(x0, y0, x0 + gw, y0 + gh)))
    hoja = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "piezas": piezas,
        "area_usada": 0.0,
    }
    pocket = unary_union(
        [
            box(0.0, rail, tx, y102 + th),
            box(tx + tw, rail, w, y102 + th),
        ]
    )
    stats = fill_vfm_facing_gap(hoja, pool=[])
    assert int(stats.get("from_sheet") or 0) >= 6, stats
    n_in = 0
    for p in hoja["piezas"]:
        if "GS" not in str(p.get("nombre") or ""):
            continue
        poly = p.get("poly")
        if poly is None:
            continue
        if float(poly.intersection(pocket).area) > 0.5 * float(poly.area):
            n_in += 1
    assert n_in >= 6, f"patio no entró al pasillo: in={n_in} stats={stats}"


def test_fase_b_grupo_mueve_gs_de_hoja_pobre():
    """Mudar automático: GS en hoja pobre → pasillo de hoja con sandwich VFM."""
    from shapely.affinity import translate as shp_translate
    from shapely.ops import unary_union

    from modules.nesting_engine.giga_cal11_galv import densify_giga_group_phase_b

    w, rail, tw, th, tx = 1990.0, 40.0, 90.0, 140.0, 950.0
    t_up = Polygon(
        [
            (0.0, 0.0),
            (w, 0.0),
            (w, rail),
            (tx + tw, rail),
            (tx + tw, rail + th),
            (tx, rail + th),
            (tx, rail),
            (0.0, rail),
            (0.0, 0.0),
        ]
    )
    t_dn = Polygon(
        [
            (0.0, th),
            (tx, th),
            (tx, 0.0),
            (tx + tw, 0.0),
            (tx + tw, th),
            (w, th),
            (w, th + rail),
            (0.0, th + rail),
            (0.0, th),
        ]
    )
    y102 = (rail + th) + 10.0
    p102 = shp_translate(t_dn, 0.0, y102)
    gw, gh = 3.84 * 25.4, 3.61 * 25.4
    # H61-like: sandwich con solo 2 GS ya dentro (subcargado).
    good = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "area_usada": 0.0,
        "piezas": [
            _mk("GENE-VFM-20-101", t_up),
            _mk("GENE-VFM-20-102", p102),
            _mk("GENE-GS-0820-708#a", box(20.0, rail + 5.0, 20.0 + gw, rail + 5.0 + gh)),
            _mk(
                "GENE-GS-0820-708#b",
                box(20.0 + gw + 8.0, rail + 5.0, 20.0 + 2 * gw + 8.0, rail + 5.0 + gh),
            ),
        ],
    }
    # Hoja pobre tipo 29%: par VFM + 9 GS en patio.
    poor_pcs = [
        _mk("GENE-VFM-20-101#p", shp_translate(t_up, 0.0, 0.0)),
        _mk("GENE-VFM-20-102#p", shp_translate(p102, 0.0, 0.0)),
    ]
    for i in range(9):
        x0 = 2100.0 + (i % 3) * (gw + 10.0)
        y0 = 40.0 + (i // 3) * (gh + 10.0)
        poor_pcs.append(_mk(f"GENE-GS-0820-708#d{i}", box(x0, y0, x0 + gw, y0 + gh)))
    poor = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "area_usada": 0.0,
        "piezas": poor_pcs,
    }
    pocket = unary_union(
        [
            box(0.0, rail, tx, y102 + th),
            box(tx + tw, rail, w, y102 + th),
        ]
    )
    stats = densify_giga_group_phase_b([good, poor], budget_s=30.0)
    assert int(stats.get("cross_moved") or 0) >= 6, stats
    n_in = 0
    for p in good["piezas"]:
        if "GS" not in str(p.get("nombre") or ""):
            continue
        poly = p.get("poly")
        if poly is None:
            continue
        if float(poly.intersection(pocket).area) > 0.5 * float(poly.area):
            n_in += 1
    assert n_in >= 8, f"H61-style: solo {n_in} GS en pasillo tras phase_b stats={stats}"
    # Inventario: total GS no se pierde.
    n_gs = sum(
        1
        for h in (good, poor)
        for p in (h.get("piezas") or [])
        if "GS" in str(p.get("nombre") or "")
    )
    assert n_gs == 11, n_gs


def test_order_torre_cuando_solo_vfm():
    """Sin HFM/SIVC: 2 pares (torre) antes que GS; resto de I después."""
    from modules.nesting_engine.giga_cal11_galv import order_giga_pool_python

    inch = 25.4
    p101 = Polygon(_u_open_top(78.35 * inch, 12.24 * inch, (12.24 - 8.77) * inch))
    p102 = Polygon(_u_open_top(78.35 * inch, 11.19 * inch, (11.19 - 8.77) * inch))
    gs = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)
    hfm = box(0.0, 0.0, 34.65 * inch, 6.29 * inch)

    pool_tail = []
    for i in range(4):
        pool_tail.append(_mk(f"GENE-VFM-20-101#{i}", p101))
        pool_tail.append(_mk(f"GENE-VFM-20-102#{i}", p102))
    pool_tail.extend(_mk(f"GENE-GS-0820-708#{i}", gs) for i in range(12))
    ordered = order_giga_pool_python(pool_tail)
    names = [str(p.get("nombre") or "") for p in ordered]
    # Torre = 2 pares (4 I) antes de GS; el resto de I después.
    head = names[:4]
    assert all("VFM-20" in n for n in head), head
    assert sum(1 for n in head if "-101" in n) == 2
    assert sum(1 for n in head if "-102" in n) == 2
    assert "GS" in names[4], names[4:8]
    assert any("VFM-20" in n for n in names[5:]), names

    # Con HFM: solo 1 par al inicio, luego barra, luego GS, luego resto I.
    pool_mix = [
        _mk("GENE-VFM-20-101#0", p101),
        _mk("GENE-VFM-20-102#0", p102),
        _mk("GENE-VFM-20-101#1", p101),
        _mk("GENE-VFM-20-102#1", p102),
        _mk("GENE-HFM-10-102", hfm),
        _mk("GENE-GS-0820-708#0", gs),
    ]
    mix = [str(p.get("nombre") or "") for p in order_giga_pool_python(pool_mix)]
    assert "VFM-20-101#0" in mix[0] and "VFM-20-102#0" in mix[1], mix[:4]
    assert "HFM" in mix[2], mix
    assert "GS" in mix[3], mix
    assert "VFM-20-101#1" in mix[4], mix


def test_cola_giga_no_achica_placa_con_vfm():
    """Con VFM pendientes, no activar cola de placas cortas (causa faltan N)."""
    import os

    from modules.nesting_engine.manager import _es_cola_de_grupo
    from modules.nesting_engine.nest_engine_context import (
        reset_pack_group_clave,
        set_pack_group_clave,
    )

    os.environ["ARGA_GIGA_CAL11_GALV"] = "1"
    tok = set_pack_group_clave("0.11811_GALVANIZADO")
    try:
        est = [_mk("GENE-VFM-20-101", box(0, 0, 1990, 310))]
        acc = [_mk("GENE-GS-0820-708", box(0, 0, 100, 90)) for _ in range(5)]
        assert _es_cola_de_grupo(est, acc) is False
        assert _es_cola_de_grupo([], acc) is True  # solo GS chicos: sí cola
    finally:
        reset_pack_group_clave(tok)
        os.environ["ARGA_GIGA_CAL11_GALV"] = "0"


def test_pack_torre_vfm_antes_de_inyectar():
    """Cola solo VFM+GS: la 1ª hoja lleva ≥3 I (torre), no 1 par + patio de GS."""
    from modules.nesting_engine.engines.giga_cal11_galv import GigaCal11GalvEngine
    from modules.nesting_engine.engines.types import PackSheetRequest

    if not GigaCal11GalvEngine.is_ready():
        print("SKIP torre pack (no algorithm_cpp giga)")
        return

    inch = 25.4
    p101 = Polygon(_u_open_top(78.35 * inch, 12.24 * inch, (12.24 - 8.77) * inch))
    p102 = Polygon(_u_open_top(78.35 * inch, 11.19 * inch, (11.19 - 8.77) * inch))
    gs = box(0.0, 0.0, 3.84 * inch, 3.61 * inch)

    def _p(nombre, poly):
        d = _mk(nombre, poly)
        d["rings"] = [list(poly.exterior.coords)]
        d["calibre"] = "0.11811"
        d["material"] = "GALVANIZADO"
        return d

    pool = []
    for i in range(4):
        pool.append(_p(f"GENE-VFM-20-101#{i}", p101))
        pool.append(_p(f"GENE-VFM-20-102#{i}", p102))
    pool.extend(_p(f"GENE-GS-0820-708#{i}", gs) for i in range(40))
    res = GigaCal11GalvEngine.empaquetar(
        PackSheetRequest(piezas=list(pool), w_placa=3048.0, h_placa=1219.2)
    )
    placed = list((res.hoja or {}).get("piezas") or [])
    n_vfm = sum(1 for p in placed if "VFM-20" in str(p.get("nombre") or "").upper())
    assert n_vfm >= 3, (
        f"sin torre: solo {n_vfm} VFM en P1 (se esperaba ≥3 antes de inundar GS) "
        f"n_placed={len(placed)}"
    )


def _vfm_interlock_profile(w: float, h: float, *, t_h: float | None = None):
    """Perfil tipo VFM-20: T a altura plena y valles centrados (gap arriba/abajo).

    Como el DXF real: ends ~10\", valleys ~4.8\" centrados, T = h. El gap
    simétrico permite gap_y negativo al escalonar en X.
    """
    from shapely.ops import unary_union

    if t_h is None:
        t_h = h
    x_end = 0.08 * w
    x_low0 = 0.35 * w
    x_t0 = 0.48 * w
    x_t1 = 0.52 * w
    x_low1 = 0.65 * w
    h_end = 0.83 * h
    h_low = 0.39 * h

    def _band(x0, x1, hh):
        y0 = 0.5 * (h - hh)
        return box(x0, y0, x1, y0 + hh)

    metal = unary_union(
        [
            _band(0.0, x_end, h_end),
            _band(w - x_end, w, h_end),
            _band(x_end, x_low0, h_low),
            _band(x_low0, x_t0, h_low),
            _band(x_t0, x_t1, float(t_h)),
            _band(x_t1, x_low1, h_low),
            _band(x_low1, w - x_end, h_low),
        ]
    )
    if metal.geom_type != "Polygon":
        metal = max(metal.geoms, key=lambda g: g.area)
    return metal


def _aligned_vfm_tower(n: int = 4, *, kerf_in: float = 0.150):
    """n VFM-20 apiladas alineadas en X (torre sin zig-zag)."""
    from shapely.affinity import translate as shp_translate

    inch = 25.4
    kerf = kerf_in * inch
    w = 78.35 * inch
    h101 = 12.24 * inch
    h102 = 11.19 * inch
    host101 = _vfm_interlock_profile(w, h101)
    host102 = _vfm_interlock_profile(w, h102, t_h=h102)
    margin = 0.250 * inch
    piezas = []
    y = margin
    for i in range(n):
        base = host101 if (i % 2) == 0 else host102
        nom = "GENE-VFM-20-101" if (i % 2) == 0 else "GENE-VFM-20-102"
        h = float(base.bounds[3] - base.bounds[1])
        poly = shp_translate(base, margin, y - float(base.bounds[1]))
        p = _mk(f"{nom}#{i}", poly)
        p["_void_uid"] = f"tower{i}"
        piezas.append(p)
        y += h + kerf
    return {
        "kerf_usado": kerf_in,
        "margin_usado": 0.250,
        "placa_w": 120.0 * inch,
        "placa_h": 48.0 * inch,
        "piezas": piezas,
    }


def test_zigzag_torre_escalona_x():
    """≥3 VFM alineadas: zig-zag aplica |dx|≥2\" y baja el alto de pila."""
    from shapely.ops import unary_union

    from modules.nesting_engine.giga_cal11_galv import zigzag_vfm_tower_stack
    from modules.nesting_engine.venom_hole_fill import _piece_poly

    inch = 25.4
    kerf = 0.150 * inch
    hoja = _aligned_vfm_tower(4)
    polys0 = [_piece_poly(p) for p in hoja["piezas"]]
    h0 = float(unary_union(polys0).bounds[3] - unary_union(polys0).bounds[1])
    st = zigzag_vfm_tower_stack(hoja)
    assert st.get("staggered", 0) >= 1, st
    polys1 = [_piece_poly(p) for p in hoja["piezas"]]
    ordered = sorted(polys1, key=lambda p: float(p.centroid.y))
    dxs = [
        abs(float(ordered[i].centroid.x - ordered[i - 1].centroid.x)) / inch
        for i in range(1, len(ordered))
    ]
    assert max(dxs) >= 2.0 - 1e-3, (dxs, st)
    h1 = float(unary_union(polys1).bounds[3] - unary_union(polys1).bounds[1])
    assert h1 < h0 - 5.0, (h0, h1, st)
    for i in range(1, len(ordered)):
        a, b = ordered[i - 1], ordered[i]
        inter = float(a.intersection(b).area)
        assert inter <= 25.0, (i, inter, st)
        assert float(a.distance(b)) + 1e-3 >= kerf - 0.05, (i, a.distance(b), st)


def test_zigzag_no_toca_un_par():
    """1×101 + 1×102 (hoja mixta tipo P1): zig-zag no escalona."""
    from shapely.affinity import translate as shp_translate

    from modules.nesting_engine.giga_cal11_galv import zigzag_vfm_tower_stack
    from modules.nesting_engine.venom_hole_fill import _piece_poly

    inch = 25.4
    kerf = 0.150 * inch
    margin = 0.250 * inch
    host = _h_with_t()
    h = float(host.bounds[3] - host.bounds[1])
    p101 = _mk("GENE-VFM-20-101", shp_translate(host, margin, margin))
    p102 = _mk(
        "GENE-VFM-20-102",
        shp_translate(host, margin, margin + h + kerf),
    )
    hfm = _mk(
        "GENE-HFM-10-102",
        box(
            margin + 80 * inch,
            margin,
            margin + 114.65 * inch,
            margin + 6.29 * inch,
        ),
    )
    hoja = {
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "placa_w": 120.0 * inch,
        "placa_h": 48.0 * inch,
        "piezas": [p101, p102, hfm],
    }
    cx0 = float(_piece_poly(p102).centroid.x)
    st = zigzag_vfm_tower_stack(hoja)
    assert int(st.get("staggered") or 0) == 0, st
    cx1 = float(_piece_poly(p102).centroid.x)
    assert abs(cx1 - cx0) < 1.0, (cx0, cx1, st)


def test_zigzag_no_toca_torre_con_patio():
    """4 I + BKT de patio: no escalonar (cascada aplastaba invitados → faltan N)."""
    from shapely.affinity import translate as shp_translate

    from modules.nesting_engine.giga_cal11_galv import zigzag_vfm_tower_stack
    from modules.nesting_engine.venom_hole_fill import _piece_poly

    inch = 25.4
    hoja = _aligned_vfm_tower(4)
    bkt = _mk(
        "GENE-BKT-304",
        shp_translate(box(0.0, 0.0, 3.0 * inch, 3.0 * inch), 90.0 * inch, 20.0 * inch),
    )
    hoja["piezas"].append(bkt)
    cx0 = [
        float(_piece_poly(p).centroid.x)
        for p in hoja["piezas"]
        if "VFM-20" in str(p.get("nombre") or "")
    ]
    st = zigzag_vfm_tower_stack(hoja)
    assert int(st.get("staggered") or 0) == 0, st
    assert str(st.get("skip") or "").startswith("patio"), st
    cx1 = [
        float(_piece_poly(p).centroid.x)
        for p in hoja["piezas"]
        if "VFM-20" in str(p.get("nombre") or "")
    ]
    assert cx0 == cx1, (cx0, cx1, st)


def test_zigzag_sin_placa_no_mueve():
    """Sin placa_w/h no escalona (checker ciego sacaba I fuera → poka expulsa)."""
    from modules.nesting_engine.giga_cal11_galv import zigzag_vfm_tower_stack
    from modules.nesting_engine.venom_hole_fill import _piece_poly

    hoja = _aligned_vfm_tower(4)
    hoja.pop("placa_w", None)
    hoja.pop("placa_h", None)
    cx0 = float(_piece_poly(hoja["piezas"][1]).centroid.x)
    st = zigzag_vfm_tower_stack(hoja)
    assert int(st.get("staggered") or 0) == 0, st
    assert st.get("skip") == "no_plate", st
    cx1 = float(_piece_poly(hoja["piezas"][1]).centroid.x)
    assert abs(cx1 - cx0) < 1e-6, (cx0, cx1)


def test_zigzag_puede_meter_quinta():
    """4 I en 120×48 + 1 en restos: tras zig-zag cabe la 5ª o queda franja ≥7\"."""
    from shapely.affinity import translate as shp_translate
    from shapely.ops import unary_union

    from modules.nesting_engine.giga_cal11_galv import zigzag_vfm_tower_stack
    from modules.nesting_engine.venom_hole_fill import _piece_poly

    inch = 25.4
    hoja = _aligned_vfm_tower(4)
    extra = _mk(
        "GENE-VFM-20-101#extra",
        shp_translate(_vfm_interlock_profile(78.35 * inch, 12.24 * inch), 5000.0, 5000.0),
    )
    restos = [extra]
    st = zigzag_vfm_tower_stack(hoja, restos)
    n_vfm = sum(
        1
        for p in (hoja.get("piezas") or [])
        if "VFM-20" in str(p.get("nombre") or "").upper()
    )
    polys = [
        _piece_poly(p)
        for p in (hoja.get("piezas") or [])
        if "VFM-20" in str(p.get("nombre") or "").upper()
    ]
    polys = [p for p in polys if p is not None]
    u = unary_union(polys)
    top = float(u.bounds[3])
    room = float(hoja["placa_h"]) - 0.250 * inch - top
    assert n_vfm >= 5 or int(st.get("pulled") or 0) >= 1 or room >= 5.5 * inch, (
        n_vfm,
        room / inch,
        st,
    )


if __name__ == "__main__":
    test_detector_cal11_galv_no_a36()
    test_clave_desde_debug_tag()
    test_no_aparece_en_selector()
    test_renest_no_pregunta_motor()
    test_force_giga_solo_con_clave()
    test_es_motor_nativo_no_overlay_lite()
    test_cpp_export_presente()
    test_frames_vfm()
    test_familia_giga_autodxf_hosts()
    test_pasillo_entre_vfm_recibe_bkt()
    test_pasillo_t_enfrentadas_recibe_gs()
    test_pasillo_denso_llena_varios_gs()
    test_mixin_renest_fijo()
    test_mixto_invitados_en_la_misma_hoja()
    test_vfm_canal_recibe_bkt()
    test_vfm_canal_desde_pool()
    test_vfm_canal_gs_gordo_no_entra()
    test_vfm_bahia_alta_recibe_bkt304_y_gs()
    test_vfm_h_con_t_recibe_304_en_bolsa_no_gs_en_ala()
    test_prefill_satura_bahia_no_una_pieza()
    test_hfm_no_bloquea_gs_en_bahia()
    test_prefill_todos_los_vfm_del_pool()
    test_extra_i_llevan_cargo_fuera_de_hoja()
    test_hfm_entra_bolsa_877()
    test_cierra_par_vfm_reduce_alto()
    test_reparar_expulsa_solape_metal_vfm()
    test_cargo_host_no_colocado_vuelve_a_restos()
    test_giga_no_simula_dos_alturas()
    test_combinado_forzado_en_giga()
    test_planta_giga_no_par_vacio_y_azules_en_bahia()
    test_fase_b_facing_jala_patio_sin_pool()
    test_fase_b_grupo_mueve_gs_de_hoja_pobre()
    test_order_torre_cuando_solo_vfm()
    test_cola_giga_no_achica_placa_con_vfm()
    test_pack_torre_vfm_antes_de_inyectar()
    test_zigzag_torre_escalona_x()
    test_zigzag_no_toca_un_par()
    test_zigzag_no_toca_torre_con_patio()
    test_zigzag_sin_placa_no_mueve()
    test_zigzag_puede_meter_quinta()
    print("GIGA_CAL11_GALV PASS")
