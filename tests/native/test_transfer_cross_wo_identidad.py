#!/usr/bin/env python
"""Candado: transfer cross-WO no pierde la pieza tras desacoplar (deepcopy).

Caso real 2026-08-07: al mudar Top_Cover_1 de WO a WO el motor devolvía
pieza_no_encontrada / «No se pudo identificar la pieza en la placa actual»
aunque el destino (PLC059) era óptimo. Causa: al clonar las WOs gemelas se
rompía id() de hojas/piezas y el lookup caía en la primera placa_id ambigua.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))


def _pieza(nombre, idx, *, x=10.0, y=20.0):
    return {
        "nombre": nombre,
        "debug_id": f"0.313_A 36::{nombre}::rep{idx}",
        "shift_x": x + idx,
        "shift_y": y,
        "rot_deg": 0.0,
        "poligonos": [[[0, 0], [100, 0], [100, 50], [0, 50]]],
        "material": "A 36",
        "calibre": 0.313,
    }


def _hoja(placa_id, seq, piezas, uid_suffix):
    return {
        "placa_id": placa_id,
        "placa_w": 6096.0,
        "placa_h": 1219.2,
        "sheet_seq": seq,
        "sheet_uid": f"0.313_A 36::{placa_id}::{uid_suffix}",
        "es_retazo": False,
        "_nest_list_idx": seq - 1,
        "piezas": piezas,
    }


def test_resolver_candidatos_tras_deepcopy():
    from modules.nesting_engine.manager import MotorNesting

    motor = MotorNesting.__new__(MotorNesting)
    hoja = _hoja(
        "PLC060",
        1,
        [
            _pieza("Placa Segmento 1", 1),
            _pieza("Inspection_Plate", 2),
            _pieza("Placa Segmento 2", 3),
            _pieza("Top_Cover_1", 4, x=50.0, y=60.0),
        ],
        0,
    )
    sel = hoja["piezas"][3]
    assert motor._resolver_candidatos_transferencia(hoja, [sel], indices=[3])

    hoja2 = copy.deepcopy(hoja)
    # Simula selección del visor contra objetos VIEJOS + hoja NUEVA (post-desacople).
    cands = motor._resolver_candidatos_transferencia(hoja2, [sel], indices=[3])
    assert len(cands) == 1, cands
    assert cands[0] is hoja2["piezas"][3]
    assert cands[0] is not sel
    assert cands[0]["nombre"] == "Top_Cover_1"


def test_hoja_origen_no_toma_primera_placa_id():
    """Lookup cross-WO debe preferir índice / sheet_uid, no la 1.ª PLC059."""
    from interface.qt.tabs._mixin_transfer import TransferMixin

    class Fake(TransferMixin):
        def __init__(self):
            self.app = type("A", (), {})()

    fx = Fake()
    clave = "0.313_A 36"
    hojas = [
        _hoja("PLC059", 1, [_pieza("Other", 1)], 0),
        _hoja(
            "PLC059",
            2,
            [
                _pieza("A", 1),
                _pieza("B", 2),
                _pieza("C", 3),
                _pieza("Top_Cover_1", 4),
            ],
            1,
        ),
        _hoja("PLC060", 3, [_pieza("Z", 1)], 2),
    ]
    fx.app.resultados_multilote = [
        {"lote_k": 1, "data": {clave: {"hojas": hojas}}},
        {"lote_k": 11, "data": {clave: {"hojas": copy.deepcopy(hojas)}}},
    ]
    origen_old = hojas[1]
    sel = origen_old["piezas"][3]

    # Sin índice y con placa_id ambigua: solo debe resolver si uid/seq coinciden
    viva = fx._hoja_en_orden_multilote(0, clave, hoja_idx=None, hoja_ref=origen_old)
    assert viva is origen_old

    # Tras deepcopy del lote 0, el objeto viejo ya no está; el índice debe bastar.
    fx.app.resultados_multilote[0]["data"] = copy.deepcopy(
        fx.app.resultados_multilote[0]["data"]
    )
    viva2 = fx._hoja_en_orden_multilote(0, clave, hoja_idx=1, hoja_ref=origen_old)
    assert viva2 is not None
    assert viva2 is not origen_old
    assert viva2["sheet_seq"] == 2
    assert viva2["piezas"][3]["nombre"] == "Top_Cover_1"

    from modules.nesting_engine.manager import MotorNesting

    motor = MotorNesting.__new__(MotorNesting)
    cands = motor._resolver_candidatos_transferencia(viva2, [sel], indices=[3])
    assert len(cands) == 1
    assert cands[0]["nombre"] == "Top_Cover_1"
    assert cands[0] is viva2["piezas"][3]


def test_preparar_cross_wo_mantiene_candidatos():
    from interface.qt.tabs._mixin_transfer import TransferMixin
    from modules.nesting_engine.manager import MotorNesting

    class Fake(TransferMixin):
        def __init__(self):
            self.app = type("A", (), {})()

    fx = Fake()
    clave = "0.313_A 36"
    hojas = [
        _hoja("PLC059", 1, [_pieza("Other", 1)], 0),
        _hoja(
            "PLC060",
            2,
            [
                _pieza("A", 1),
                _pieza("B", 2),
                _pieza("C", 3),
                _pieza("Top_Cover_1", 4),
            ],
            1,
        ),
    ]
    dest_hojas = [
        _hoja("PLC059", 1, [_pieza("DestPad", 1)], 0),
    ]
    data0 = {clave: {"hojas": hojas, "piezas_pool": [{"nombre": "Top_Cover_1"}]}}
    data1 = {clave: {"hojas": dest_hojas, "piezas_pool": []}}
    fx.app.resultados_multilote = [
        {"lote_k": 1, "data": data0},
        {"lote_k": 11, "data": data1},
    ]
    fx.app.resultados_nesting = data0
    origen = hojas[1]
    sel = origen["piezas"][3]
    entry = {"hoja": dest_hojas[0], "hoja_idx": 0, "lote_idx": 1}

    hoja_o, hoja_d, res_d = fx._preparar_transferencia_cross_wo(
        clave, 0, 1, origen, entry, hoja_origen_idx=1
    )
    assert hoja_o is not origen
    assert hoja_o["piezas"][3]["nombre"] == "Top_Cover_1"
    motor = MotorNesting.__new__(MotorNesting)
    cands = motor._resolver_candidatos_transferencia(hoja_o, [sel], indices=[3])
    assert len(cands) == 1, "tras desacople debe resolver Top_Cover_1"
    assert res_d is fx.app.resultados_multilote[1]["data"]


def main() -> int:
    test_resolver_candidatos_tras_deepcopy()
    test_hoja_origen_no_toma_primera_placa_id()
    test_preparar_cross_wo_mantiene_candidatos()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
