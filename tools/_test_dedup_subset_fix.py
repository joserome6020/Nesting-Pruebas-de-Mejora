"""Verifica que deduplicar_hojas_grupo no elimine hojas legítimas con piezas repetidas."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.nesting_engine.sheet_integrity import (
    deduplicar_hojas_grupo,
    reconciliar_hojas_grupo,
    sanitizar_hojas_grupo,
    validar_colocacion_completa,
)


def _pieza(nombre: str, x: float, y: float, w: float, h: float) -> dict:
    return {
        "nombre": nombre,
        "poligonos": [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]],
    }


def _hoja(placa_id: str, piezas: list) -> dict:
    return {"placa_id": placa_id, "piezas": piezas, "eficiencia_directa": 50.0}


def test_repeated_name_two_sheets_same_placa():
    """Caso 0.1046: 2x Pared lateral en PLC031 en hojas distintas."""
    pool = [
        {"nombre": "Pared lateral ATC"},
        {"nombre": "Pared lateral ATC"},
        {"nombre": "Pared grande ATC"},
        {"nombre": "Pared interna ATC"},
    ]
    hojas = [
        _hoja(
            "PLC031",
            [
                _pieza("Pared lateral ATC", 0, 0, 100, 50),
                _pieza("Pared grande ATC", 200, 0, 150, 80),
                _pieza("Pared interna ATC", 400, 0, 120, 60),
            ],
        ),
        _hoja(
            "PLC031",
            [
                _pieza("Pared lateral ATC", 0, 300, 100, 50),
            ],
        ),
    ]
    out = deduplicar_hojas_grupo(hojas)
    madres = [h for h in out if not h.get("es_retazo")]
    assert len(madres) == 2, f"esperadas 2 hojas, quedaron {len(madres)}"
    ok, msg = validar_colocacion_completa(pool, out)
    assert ok, msg


def test_name_subset_not_discarded():
    """Hoja con subconjunto de nombres pero instancias distintas (qty>1)."""
    pool = [
        {"nombre": "Pieza A"},
        {"nombre": "Pieza A"},
        {"nombre": "Pieza B"},
    ]
    hojas = [
        _hoja("PLC002", [_pieza("Pieza A", 0, 0, 50, 50), _pieza("Pieza B", 100, 0, 50, 50)]),
        _hoja("PLC002", [_pieza("Pieza A", 0, 200, 50, 50)]),
    ]
    out = sanitizar_hojas_grupo(pool, hojas)
    ok, msg = validar_colocacion_completa(pool, out)
    assert ok, msg


def test_true_spatial_duplicate_still_removed():
    """Duplicado real: misma pieza en la misma posición en dos hojas."""
    pool = [{"nombre": "Solo"}]
    p = _pieza("Solo", 10, 10, 40, 40)
    hojas = [
        _hoja("PLC036", [p]),
        _hoja("PLC036", [p]),
    ]
    out = deduplicar_hojas_grupo(hojas)
    madres = [h for h in out if not h.get("es_retazo")]
    assert len(madres) == 1, f"duplicado espacial debe quedar 1 hoja, quedaron {len(madres)}"


def test_reconciliar_drops_non_consumible():
    """Restos erróneos: segunda hoja repite piezas ya consumidas."""
    pool = [{"nombre": "X"}, {"nombre": "Y"}]
    hojas = [
        _hoja("PLC001", [_pieza("X", 0, 0, 50, 50), _pieza("Y", 100, 0, 50, 50)]),
        _hoja("PLC001", [_pieza("X", 0, 200, 50, 50)]),
    ]
    out = reconciliar_hojas_grupo(pool, hojas)
    madres = [h for h in out if not h.get("es_retazo")]
    assert len(madres) == 1


def test_spatial_subset_same_position_qty2():
    """Caso real 0.1046: 2ª Pared lateral en hoja sola, misma posición que en hoja previa."""
    pool = [
        {"nombre": "Pared lateral ATC"},
        {"nombre": "Pared lateral ATC"},
        {"nombre": "Tapa trasera ATC"},
    ]
    lat = _pieza("Pared lateral ATC", 10, 10, 2494, 915)
    hojas = [
        _hoja("PLC002", [lat, _pieza("Tapa trasera ATC", 500, 10, 2133, 158)]),
        _hoja("PLC002", [lat]),
    ]
    out = sanitizar_hojas_grupo(pool, hojas)
    madres = [h for h in out if not h.get("es_retazo")]
    assert len(madres) == 2, f"esperadas 2 hojas PLC002, quedaron {len(madres)}"
    ok, msg = validar_colocacion_completa(pool, out)
    assert ok, msg


if __name__ == "__main__":
    test_repeated_name_two_sheets_same_placa()
    test_name_subset_not_discarded()
    test_spatial_subset_same_position_qty2()
    test_true_spatial_duplicate_still_removed()
    test_reconciliar_drops_non_consumible()
    print("OK: todos los tests de dedup pasaron")
