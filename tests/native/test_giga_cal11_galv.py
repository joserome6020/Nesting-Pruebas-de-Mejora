"""Candado: Cal 11 Galvanizado usa motor nativo oculto (no overlay de Lite)."""
from __future__ import annotations

import inspect
import os
import sys
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
    is_frame_piece,
    is_giga_cal11_galv_clave,
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
    assert is_giga_cal11_galv_clave("0.118_GALVANIZADO")
    assert is_giga_cal11_galv_clave("11_GALVANIZADO")
    assert not is_giga_cal11_galv_clave("0.11811_A 36")
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
    assert engine_id_for_renest("0.0747_A 36") is None
    assert engine_id_for_group("0.11811_GALVANIZADO", "svgnest_ultra") == ENGINE_ID
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


def test_pasillo_entre_vfm_recibe_bkt():
    """Dos marcos apilados dejan un pasillo; el BKT a la derecha debe entrar."""
    rail_a = box(20.0, 20.0, 220.0, 45.0)
    rail_b = box(20.0, 80.0, 220.0, 105.0)
    guest = box(280.0, 50.0, 320.0, 70.0)
    hoja = {
        "placa_w": 400.0,
        "placa_h": 160.0,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "piezas": [
            _mk("GENE-VFM-20-101", rail_a),
            _mk("GENE-VFM-20-102", rail_b),
            _mk("GENE-BKT-299", guest),
        ],
    }
    corridor = box(20.0, 45.0, 220.0, 80.0)
    stats = apply_giga_pasillo_fill(hoja)
    bkt = next(p for p in hoja["piezas"] if "BKT" in str(p.get("nombre") or ""))
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


def test_vfm_canal_recibe_bkt():
    """Canal de ala VFM (~3.7\") con kerf 0.150\": BKT-287 3.00\" debe entrar."""
    host = Polygon(_u_open_top(1990.0, 311.0, 180.0))
    # 7.33" × 3.00" ≈ BKT-287 planta
    guest = box(2100.0, 20.0, 2100.0 + 186.2, 20.0 + 76.2)
    hoja = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "piezas": [
            _mk("GENE-VFM-20-101", host),
            _mk("GENE-BKT-287", guest),
        ],
    }
    channel = box(50.0, 180.0, 1940.0, 311.0)
    stats = apply_giga_pasillo_fill(hoja)
    bkt = next(p for p in hoja["piezas"] if "BKT" in str(p.get("nombre") or ""))
    poly = bkt.get("poly")
    assert poly is not None
    inside = float(poly.intersection(channel).area) > 0.8 * float(poly.area)
    assert inside, (
        f"BKT-287 no entró al canal VFM: centroid="
        f"({poly.centroid.x:.1f},{poly.centroid.y:.1f}) stats={stats}"
    )


def test_vfm_canal_desde_pool():
    """Invitado que quedó en restos (otra placa) se jala al canal si cabe."""
    host = Polygon(_u_open_top(1990.0, 311.0, 180.0))
    guest = box(0.0, 0.0, 186.2, 76.2)
    hoja = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "area_usada": float(host.area),
        "piezas": [_mk("GENE-VFM-20-101", host)],
    }
    pool = [_mk("GENE-BKT-287", guest)]
    channel = box(50.0, 180.0, 1940.0, 311.0)
    stats = apply_giga_pasillo_fill(hoja, pool=pool)
    assert stats.get("channel_pool", 0) >= 1, stats
    assert not pool, "el BKT debía salir de restos"
    bkt = next(p for p in hoja["piezas"] if "BKT" in str(p.get("nombre") or ""))
    poly = bkt.get("poly")
    assert poly is not None
    assert float(poly.intersection(channel).area) > 0.8 * float(poly.area)


def test_vfm_canal_gs_gordo_no_entra():
    """GS ~3.61\" + kerf 0.150\" no cabe en canal ~3.7\" (no forzar)."""
    host = Polygon(_u_open_top(1990.0, 95.0, 20.0))
    # Canal corto ≈ 75 mm; GS 3.61" = 91.7 mm no cabe.
    guest = box(2100.0, 20.0, 2100.0 + 91.7, 20.0 + 97.5)
    hoja = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "piezas": [
            _mk("GENE-VFM-20-101", host),
            _mk("GENE-GS-0820-708", guest),
        ],
    }
    channel = box(50.0, 20.0, 1940.0, 95.0)
    apply_giga_pasillo_fill(hoja)
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
    hoja = {
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "kerf_usado": 0.150,
        "margin_usado": 0.250,
        "piezas": [
            _mk("GENE-VFM-20-101", host),
            _mk("GENE-BKT-304", bkt),
            _mk("GENE-GS-0820-708", gs),
        ],
    }
    stats = apply_giga_pasillo_fill(hoja)
    for tag in ("BKT-304", "GS-0820"):
        pz = next(p for p in hoja["piezas"] if tag in str(p.get("nombre") or ""))
        poly = pz.get("poly")
        assert poly is not None
        inside = float(poly.intersection(bay).area) > 0.7 * float(poly.area)
        assert inside, (
            f"{tag} no entró a bahía 8.77\": centroid="
            f"({poly.centroid.x:.1f},{poly.centroid.y:.1f}) stats={stats}"
        )


def test_combinado_forzado_en_giga():
    import inspect

    from modules.nesting_engine.manager import _usar_pack_combinado_grupo

    src = inspect.getsource(_usar_pack_combinado_grupo)
    assert "should_force_giga_engine" in src


if __name__ == "__main__":
    test_detector_cal11_galv_no_a36()
    test_clave_desde_debug_tag()
    test_no_aparece_en_selector()
    test_renest_no_pregunta_motor()
    test_force_giga_solo_con_clave()
    test_es_motor_nativo_no_overlay_lite()
    test_cpp_export_presente()
    test_frames_vfm()
    test_pasillo_entre_vfm_recibe_bkt()
    test_mixin_renest_fijo()
    test_mixto_invitados_en_la_misma_hoja()
    test_vfm_canal_recibe_bkt()
    test_vfm_canal_desde_pool()
    test_vfm_canal_gs_gordo_no_entra()
    test_vfm_bahia_alta_recibe_bkt304_y_gs()
    test_combinado_forzado_en_giga()
    print("GIGA_CAL11_GALV PASS")
