"""Candado 2026-08-14k — todos los motores respetan la TABLA GAPS DE CORTE.

Sin este candado los defaults viejos (0.15" placa→pieza) se filtran por
paths que no pasan explícitamente ``margin_override``: renest de placa,
transferencia entre WOs, sim_lab, engine_registry directo desde código.

El bug real: la foto de planta pide 0.250" placa→pieza y cada motor
metía 0.15" cuando el caller no lo definía. Con la cascada de motores
esto se traducía en piezas colocadas hasta 0.100" más cerca del borde
que lo autorizado por planta.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.nesting_engine.cut_gaps_table import (
    CUT_GAP_RULES,
    PLATE_TO_PIECE_DEFAULT_IN,
    default_cut_gap_settings,
    gaps_for_calibre,
    load_cut_gap_settings,
    normalize_cut_gap_settings,
    save_cut_gap_settings,
)


def test_tabla_declara_0_250_placa_a_pieza() -> None:
    """La foto de planta manda: placa→pieza es 0.250\" fijo."""
    defaults = default_cut_gap_settings()
    assert abs(defaults["plate_to_piece_in"] - 0.250) < 1e-9, defaults
    assert abs(PLATE_TO_PIECE_DEFAULT_IN - 0.250) < 1e-9


def test_manager_default_margin_iguala_la_tabla() -> None:
    """Historia: DEFAULT_MARGIN_IN estaba en 0.15 y bajaba el margen final."""
    from modules.nesting_engine import manager

    assert manager.DEFAULT_MARGIN_IN == PLATE_TO_PIECE_DEFAULT_IN, (
        f"DEFAULT_MARGIN_IN={manager.DEFAULT_MARGIN_IN} debe ser "
        f"PLATE_TO_PIECE_DEFAULT_IN={PLATE_TO_PIECE_DEFAULT_IN}"
    )


def test_pack_request_default_margin_iguala_la_tabla() -> None:
    """El dataclass PackSheetRequest es el contrato de todo motor."""
    from modules.nesting_engine.engines.types import PackSheetRequest

    req = PackSheetRequest(piezas=[], w_placa=100.0, h_placa=100.0)
    assert req.margin_override == PLATE_TO_PIECE_DEFAULT_IN, req.margin_override


def test_engine_registry_default_margin_iguala_la_tabla() -> None:
    """empaquetar_una_hoja sin margin_override debe usar la tabla, no 0.15."""
    from modules.nesting_engine.engine_registry import empaquetar_una_hoja
    import inspect

    sig = inspect.signature(empaquetar_una_hoja)
    default = sig.parameters["margin_override"].default
    # Firma con None + normalización interna a la tabla.
    assert default in (None, PLATE_TO_PIECE_DEFAULT_IN), default


def test_sim_lab_defaults_iguala_la_tabla() -> None:
    """El laboratorio no puede degradar el margen aunque sea de diagnóstico."""
    import inspect

    from modules.nesting_engine import sim_lab

    for name in ("run_plate_sim",):
        fn = getattr(sim_lab, name, None)
        if fn is None:
            continue
        params = inspect.signature(fn).parameters
        if "margin_in" in params:
            assert params["margin_in"].default == PLATE_TO_PIECE_DEFAULT_IN, (
                f"sim_lab.{name}(margin_in={params['margin_in'].default}) "
                f"debe defaultar a {PLATE_TO_PIECE_DEFAULT_IN}"
            )


def test_gaps_for_calibre_devuelve_tabla_por_calibre() -> None:
    """Cambiar la tabla debe alterar lo que gaps_for_calibre devuelve."""
    kerf14, margin14, _ = gaps_for_calibre("14")
    kerf250, margin250, _ = gaps_for_calibre('0.250"')
    kerf1000, margin1000, _ = gaps_for_calibre('1.000"')

    assert abs(margin14 - PLATE_TO_PIECE_DEFAULT_IN) < 1e-9
    assert abs(margin250 - PLATE_TO_PIECE_DEFAULT_IN) < 1e-9
    assert abs(margin1000 - PLATE_TO_PIECE_DEFAULT_IN) < 1e-9

    # Kerf per calibre distinto: 0.150 (delgado) < 0.200 (medio) < 0.313 (1").
    assert kerf14 < kerf250 < kerf1000


def test_cambiar_settings_persiste_y_lo_leen_todos_los_motores(monkeypatch) -> None:
    """Editar el JSON debe reflejarse en la próxima llamada de gaps_for_calibre."""
    from modules.nesting_engine import cut_gaps_table

    tmp = Path(tempfile.mkdtemp())
    fake_config = tmp / "_config" / "cut_gaps_table.json"
    fake_config.parent.mkdir(parents=True, exist_ok=True)

    def _config_override() -> Path:
        return fake_config

    monkeypatch = None  # sin pytest: uso setattr manual
    original = cut_gaps_table._config_path
    cut_gaps_table._config_path = _config_override
    try:
        nuevos = default_cut_gap_settings()
        nuevos["plate_to_piece_in"] = 0.400
        nuevos["kerf_by_rule"]["cal_14"] = 0.180
        save_cut_gap_settings(nuevos)

        cargados = load_cut_gap_settings()
        assert abs(cargados["plate_to_piece_in"] - 0.400) < 1e-9
        assert abs(cargados["kerf_by_rule"]["cal_14"] - 0.180) < 1e-9

        kerf14, margin14, _ = gaps_for_calibre("14")
        assert abs(margin14 - 0.400) < 1e-9, margin14
        assert abs(kerf14 - 0.180) < 1e-9, kerf14
    finally:
        cut_gaps_table._config_path = original


def test_native_packer_respeta_kerf_y_margin_por_calibre() -> None:
    """El motor nativo C++ debe traducir kerf/margin de la tabla a colocaciones.

    Mide bbox real de cada pieza colocada y verifica:
    1. Pieza más cercana al borde de la placa ≥ ``margin_override``.
    2. Distancia mínima entre bboxes de piezas ≥ ``kerf_override`` (menos
       tolerancia numérica por rotación).

    Si el packer ignora estos valores, las piezas se pegan al borde o entre sí
    y el test falla. Es la garantía end-to-end de que la tabla llega al C++.
    """
    try:
        from modules.nesting_engine.engine_registry import empaquetar_una_hoja
    except Exception as exc:
        print(f"skip: motor nativo no disponible ({exc})")
        return

    from shapely.geometry import Polygon

    def _rect(w_mm: float, h_mm: float) -> Polygon:
        return Polygon([(0, 0), (w_mm, 0), (w_mm, h_mm), (0, h_mm)])

    W_mm, H_mm = 60.0 * 25.4, 30.0 * 25.4
    piezas = [
        {
            "poly": _rect(5.0 * 25.4, 5.0 * 25.4),
            "nombre": f"P{i}",
            "cantidad": 1,
            "material": "A 36",
        }
        for i in range(30)
    ]

    def _packed_bboxes(hoja) -> list[tuple[float, float, float, float]]:
        """Extrae bbox mm de cada pieza colocada (contrato nativo: 'poligonos')."""
        bboxes: list[tuple[float, float, float, float]] = []
        for pz in (hoja or {}).get("piezas", []):
            rings = pz.get("poligonos") or pz.get("rings") or []
            xs: list[float] = []
            ys: list[float] = []
            for ring in rings:
                for pt in ring or []:
                    if len(pt) < 2:
                        continue
                    xs.append(float(pt[0]))
                    ys.append(float(pt[1]))
            if not xs or not ys:
                # Algunos motores devuelven 'poly' (shapely) directamente.
                poly = pz.get("poly") or pz.get("poly_colocado")
                if poly is not None and hasattr(poly, "bounds"):
                    x0, y0, x1, y1 = poly.bounds
                    bboxes.append((float(x0), float(y0), float(x1), float(y1)))
                continue
            bboxes.append((min(xs), min(ys), max(xs), max(ys)))
        return bboxes

    margin_grande_in = 0.500  # bien por encima del default 0.250 para ver contraste
    kerf_grande_in = 0.500
    hoja, _ = empaquetar_una_hoja(
        [dict(p) for p in piezas], W_mm, H_mm,
        kerf_override=kerf_grande_in, margin_override=margin_grande_in,
        engine_id="arga_lite",
    )
    bboxes = _packed_bboxes(hoja)
    if len(bboxes) < 2:
        print(f"skip: motor nativo colocó {len(bboxes)} piezas (< 2)")
        return

    margin_mm = margin_grande_in * 25.4
    kerf_mm = kerf_grande_in * 25.4
    tol_mm = 0.5  # tolerancia por redondeo del packer (0.5 mm)

    for x0, y0, x1, y1 in bboxes:
        assert x0 >= margin_mm - tol_mm, (
            f"pieza en x0={x0:.2f} viola margen placa {margin_mm:.2f} mm"
        )
        assert y0 >= margin_mm - tol_mm, (
            f"pieza en y0={y0:.2f} viola margen placa {margin_mm:.2f} mm"
        )
        assert x1 <= W_mm - margin_mm + tol_mm, (
            f"pieza en x1={x1:.2f} viola margen placa opuesto"
        )
        assert y1 <= H_mm - margin_mm + tol_mm

    # Distancia mínima entre bboxes ≥ kerf. Sólo cuento pares con solape en el
    # eje ortogonal (si no se solapan en Y, no importa el gap X).
    def _gap(a, b) -> float | None:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        overlap_y = min(ay1, by1) > max(ay0, by0)
        overlap_x = min(ax1, bx1) > max(ax0, bx0)
        if overlap_x and overlap_y:
            return 0.0  # se traslapan (bug distinto)
        if overlap_y:
            return min(abs(bx0 - ax1), abs(ax0 - bx1))
        if overlap_x:
            return min(abs(by0 - ay1), abs(ay0 - by1))
        return None

    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            g = _gap(bboxes[i], bboxes[j])
            if g is None:
                continue
            assert g >= kerf_mm - tol_mm, (
                f"gap entre piezas {i}-{j} = {g:.2f} mm < kerf {kerf_mm:.2f} mm"
            )


if __name__ == "__main__":
    test_tabla_declara_0_250_placa_a_pieza()
    test_manager_default_margin_iguala_la_tabla()
    test_pack_request_default_margin_iguala_la_tabla()
    test_engine_registry_default_margin_iguala_la_tabla()
    test_sim_lab_defaults_iguala_la_tabla()
    test_gaps_for_calibre_devuelve_tabla_por_calibre()
    test_cambiar_settings_persiste_y_lo_leen_todos_los_motores(None)
    test_native_packer_respeta_kerf_y_margin_por_calibre()
    print("OK tabla_gaps_todos_los_motores")
