#!/usr/bin/env python
"""Candado: muda cross-WO no descuenta 2× del pool ni borra placas del donante.

Caso 2026-08-07: 5/16 con 70 piezas; se muda 1 Top_Cover_1 → el donante quedaba
en 66 (placa de 3 desaparecida) y PIEZAS TOTALES ignoraba todo el calibre (130 =
resto). Causa: `_quitar_piezas_de_pool` restaba por nombre exacto y otra vez por
base; al refrescar UI, `sanitizar_hojas_grupo` eliminaba un bloque y el grupo
marcaba error → el label saltaba el calibre.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))


def test_quitar_pool_solo_una_por_pieza_movida():
    from modules.nesting_engine.manager import MotorNesting

    motor = MotorNesting.__new__(MotorNesting)
    grupo = {
        "piezas_pool_engine": True,
        "piezas_pool": [
            {"nombre": "Top_Cover_1"},
            {"nombre": "Top_Cover_1"},
            {"nombre": "Top_Cover_1"},
            {"nombre": "Top_Cover_1"},
            {"nombre": "Other"},
        ],
    }
    motor._quitar_piezas_de_pool(grupo, [{"nombre": "Top_Cover_1"}])
    nombres = [p["nombre"] for p in grupo["piezas_pool"]]
    assert nombres.count("Top_Cover_1") == 3, nombres
    assert nombres.count("Other") == 1


def test_quitar_pool_fallback_base_sin_doble():
    from modules.nesting_engine.manager import MotorNesting

    motor = MotorNesting.__new__(MotorNesting)
    grupo = {
        "piezas_pool_engine": True,
        "piezas_pool": [
            {"nombre": "W.O. 1 X10__Top_Cover_1"},
            {"nombre": "W.O. 1 X10__Top_Cover_1"},
            {"nombre": "Other"},
        ],
    }
    # Pieza mudada con nombre corto; debe matchear por base una sola vez.
    motor._quitar_piezas_de_pool(grupo, [{"nombre": "Top_Cover_1"}])
    nombres = [p["nombre"] for p in grupo["piezas_pool"]]
    assert len([n for n in nombres if "Top_Cover_1" in n]) == 1, nombres
    assert "Other" in nombres


def test_reconciliar_no_borra_placa_si_pool_cuadra():
    from modules.nesting_engine.manager import MotorNesting
    from modules.nesting_engine.sheet_integrity import sanitizar_hojas_grupo

    motor = MotorNesting.__new__(MotorNesting)

    def hoja(n_top, extras=0):
        piezas = []
        for i in range(n_top):
            piezas.append({"nombre": "Top_Cover_1", "poligonos": [[[0, 0], [1, 0], [1, 1]]]})
        for i in range(extras):
            piezas.append({"nombre": f"O{i}", "poligonos": [[[0, 0], [1, 0], [1, 1]]]})
        return {
            "placa_id": "PLC063",
            "es_retazo": False,
            "piezas": piezas,
        }

    # 4 Top_Cover en pool/placas; se muda 1 → pool 3, placas aún con 4 hasta quitar de hoja.
    pool = [{"nombre": "Top_Cover_1"} for _ in range(4)] + [{"nombre": "O0"}]
    h_a = hoja(1, extras=1)  # 1 Top + O0
    h_b = hoja(3, extras=0)  # 3 Top
    grupo = {
        "piezas_pool_engine": True,
        "piezas_pool": list(pool),
        "hojas": [h_a, h_b],
    }
    motor._quitar_piezas_de_pool(grupo, [{"nombre": "Top_Cover_1"}])
    # Simula renest origen: quita 1 de h_b
    h_b["piezas"] = [p for p in h_b["piezas"][1:]]
    hojas = sanitizar_hojas_grupo(grupo["piezas_pool"], grupo["hojas"], clave="0.313_A 36")
    assert len([h for h in hojas if not h.get("es_retazo")]) == 2, (
        f"no debe borrarse placa; quedaron {len(hojas)}: {hojas}"
    )
    tops = sum(
        1
        for h in hojas
        for p in (h.get("piezas") or [])
        if p.get("nombre") == "Top_Cover_1"
    )
    assert tops == 3, tops


def test_label_cuenta_grupo_con_error_inventario():
    """PIEZAS TOTALES no debe ignorar un calibre solo por marcar error de inventario."""
    from modules.nesting_engine.efficiency_metrics import contar_piezas_grupo

    info = {
        "error": "Inventario incompleto: faltan 1…",
        "inventario_incompleto": True,
        "hojas": [
            {
                "placa_id": "PLC063",
                "piezas": [{"nombre": "A"}, {"nombre": "B"}, {"nombre": "C"}],
            }
        ],
    }
    assert contar_piezas_grupo(info) == 3

    # Misma lógica que el label corregido
    res = {
        "0.188_A 36": {"hojas": [{"piezas": [{"nombre": "X"}] * 10}]},
        "0.313_A 36": info,
        "0.375_A 36": {"hojas": [{"piezas": [{"nombre": "Y"}] * 70}]},
    }
    total = sum(contar_piezas_grupo(g) for g in res.values() if isinstance(g, dict))
    assert total == 10 + 3 + 70


def main() -> int:
    test_quitar_pool_solo_una_por_pieza_movida()
    test_quitar_pool_fallback_base_sin_doble()
    test_reconciliar_no_borra_placa_si_pool_cuadra()
    test_label_cuenta_grupo_con_error_inventario()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
