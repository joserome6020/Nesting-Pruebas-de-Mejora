"""Smoke: el mapa comercial de largos no debe omitir piezas al partir 480\"→240\".

Caso real SWO-003 / ANG037: el nesteo arma tiras de 480\" con 13 cortes de 35\".
Al mostrarlas como 2 barras de 240\", el reparto viejo (tira-por-tira, secuencial)
omitía 1 pieza por tira. El pedido MRL seguía bien (ceil(480/240)=2); solo el
mapa/PDF perdía piezas. El reparto global FFD por material las recupera todas.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from largos_nesting_service import (  # noqa: E402
    KERF_LARGOS_IN,
    _repartir_piezas_en_barras_comerciales,
    _slots_comerciales_en_tira,
    _util_comercial_in,
)


def _split_viejo_secuencial_tira(cortes, largo_comercial, n_unidades):
    """Copia del algoritmo que omitía piezas (para demostrar el candado)."""
    import copy

    piezas = list(cortes or [])
    if n_unidades <= 1:
        return [list(piezas)]
    util = _util_comercial_in(largo_comercial)
    buckets = [[] for _ in range(n_unidades)]
    used = [0.0] * n_unidades
    bucket_idx = 0
    for corte in piezas:
        largo = float(corte.get("largo") or 0)
        if largo <= 0:
            continue
        while bucket_idx < n_unidades - 1:
            kerf = KERF_LARGOS_IN if buckets[bucket_idx] else 0.0
            if not buckets[bucket_idx] or used[bucket_idx] + kerf + largo <= util + 0.02:
                break
            bucket_idx += 1
        kerf = KERF_LARGOS_IN if buckets[bucket_idx] else 0.0
        if used[bucket_idx] + kerf + largo > util + 0.02:
            continue  # omitir
        buckets[bucket_idx].append(copy.deepcopy(corte))
        used[bucket_idx] += kerf + largo
        if bucket_idx < n_unidades - 1 and used[bucket_idx] >= util - 0.02:
            bucket_idx += 1
    return buckets


def _barra_cabe(cortes, largo_com=240.0) -> bool:
    util = _util_comercial_in(largo_com)
    used = 0.0
    for i, c in enumerate(cortes):
        largo = float(c.get("largo") or 0)
        kerf = KERF_LARGOS_IN if i else 0.0
        if used + kerf + largo > util + 0.02:
            return False
        used += kerf + largo
    return True


# Demanda ANG037 de SWO-003: 44×35" + 22×26.5" = 66 piezas en 5 tiras de 480".
TIRAS_ANG037 = [
    [{"nombre": "ITEM 3", "largo": 35.0}] * 13,
    [{"nombre": "ITEM 3", "largo": 35.0}] * 13,
    [{"nombre": "ITEM 3", "largo": 35.0}] * 13,
    [{"nombre": "ITEM 3", "largo": 35.0}] * 5
    + [{"nombre": "ITEM 4", "largo": 26.5}] * 11,
    [{"nombre": "ITEM 4", "largo": 26.5}] * 11,
]


def test_slots_480_dan_dos_barras_de_240():
    assert _slots_comerciales_en_tira(480.0, 240.0) == 2
    assert _slots_comerciales_en_tira(240.0, 240.0) == 1


def test_reparto_viejo_por_tira_pierde_piezas():
    """Candado: el algoritmo viejo DEBE fallar (omite piezas)."""
    vistas = 0
    reales = 0
    for cortes in TIRAS_ANG037:
        reales += len(cortes)
        vistas += sum(len(b) for b in _split_viejo_secuencial_tira(cortes, 240.0, 2))
    assert reales == 66
    assert vistas < reales, f"el candado ya no detecta el bug viejo ({vistas}/{reales})"


def test_reparto_global_conserva_todas_las_piezas():
    todos = [c for tira in TIRAS_ANG037 for c in tira]
    n_barras = sum(_slots_comerciales_en_tira(480.0, 240.0) for _ in TIRAS_ANG037)
    assert n_barras == 10

    packs = _repartir_piezas_en_barras_comerciales(todos, 240.0, n_barras)
    assert len(packs) == n_barras
    assert sum(len(p) for p in packs) == 66
    assert all(_barra_cabe(p) for p in packs)


def test_solera_44_piezas_en_5_barras():
    """SLC046 SWO-003: 44×22\" en tiras 21+21+2 → 5 barras de 240\"."""
    tiras = [[{"nombre": "FLAT", "largo": 22.0}] * 21] * 2 + [[{"nombre": "FLAT", "largo": 22.0}] * 2]
    todos = [c for t in tiras for c in t]
    # 2 tiras de 480 + 1 de 240 → 2+2+1 = 5
    n = 2 + 2 + 1
    packs = _repartir_piezas_en_barras_comerciales(todos, 240.0, n)
    assert sum(len(p) for p in packs) == 44
    assert len(packs) == n
    assert all(_barra_cabe(p) for p in packs)


def test_nunca_omite_aunque_falten_barras():
    """Si el pedido queda corto, abre barras extra en vez de borrar piezas."""
    cortes = [{"nombre": "ITEM 3", "largo": 35.0}] * 13
    packs = _repartir_piezas_en_barras_comerciales(cortes, 240.0, 2)
    assert sum(len(p) for p in packs) == 13
    assert len(packs) >= 3  # 2 no alcanzan para 13×35"


if __name__ == "__main__":
    test_slots_480_dan_dos_barras_de_240()
    test_reparto_viejo_por_tira_pierde_piezas()
    test_reparto_global_conserva_todas_las_piezas()
    test_solera_44_piezas_en_5_barras()
    test_nunca_omite_aunque_falten_barras()
    print("SMOKE OK")
