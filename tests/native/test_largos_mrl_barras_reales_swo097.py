"""Candado SWO-097 (job 261091, W.O. 121 X1): el pedido MRL debe contar barras reales.

Caso real 2026-09-28: SLC046 (REFUERZO SEG 1..4) = 5×64\" + 6×26\" = 476\". Cabe en
una tira de 480\" del nesteo, así que el pedido era ceil(480/240) = 2 barras de 240\".
Pero una pieza no puede cruzar la unión entre dos barras: solo caben 10 de 11 y
REFUERZO SEG 4 #2 desaparecía del mapa y del pedido. Se necesitan 3 barras.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[2]
for p in (RAIZ, RAIZ / "interface"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from interface.largos_nesting_service import (  # noqa: E402
    _slot_overflow,
    auditar_consumo_plan,
    listar_unidades_mrl_plan,
    vista_barra_para_unidad_mrl,
)
from lista_largos_material_requerido import agregar_filas_desde_plan  # noqa: E402

SLC046 = "SLC046 | SOLERA perfil | A 36 | 3 in x 20 ft"
SLC036 = "SLC036 | SOLERA perfil | A 36 | 1 X 0.5 IN"


def _cortes(*grupos):
    return [{"nombre": n, "largo": l} for n, l, q in grupos for _ in range(q)]


PLAN = {
    "orden_id": "SWO-097",
    "tipo_orden": "SWO",
    "data": {
        SLC036: [
            {
                "source": "STOCK",
                "largo_stock": 480.0,
                "cortes": _cortes(("MARCO BASE 1", 70.0, 2), ("MARCO BASE 2", 35.5, 4), ("MARCO BASE 3", 30.75, 2)),
            }
        ],
        SLC046: [
            {
                "source": "STOCK",
                "largo_stock": 480.0,
                "cortes": _cortes(
                    ("REFUERZO SEG 1", 64.0, 2),
                    ("REFUERZO SEG 3", 64.0, 3),
                    ("REFUERZO SEG 2", 26.0, 4),
                    ("REFUERZO SEG 4", 26.0, 2),
                ),
            }
        ],
    },
}


def _catalogo():
    return (
        patch(
            "catalogo_largos.datos_material_requerido_pedido",
            side_effect=lambda material, cantidad, catalogo=None: {
                "codigo": material.split(" |")[0],
                "largo": 240.0,
                "costo": 1.0 * cantidad,
            },
        ),
        patch("catalogo_largos._cargar_placas_largos_desde_herinox", return_value={}),
    )


def test_pedido_mrl_cuenta_barras_reales_de_240():
    p1, p2 = _catalogo()
    with p1, p2:
        filas = {f["codigo"]: f["cantidad"] for f in agregar_filas_desde_plan(PLAN)}
    assert filas["SLC036"] == 2, filas
    assert filas["SLC046"] == 3, f"SLC046 necesita 3×240\" (476\" no se parten en 2): {filas}"


def test_mapa_muestra_todas_las_piezas_en_barras_que_caben():
    p1, p2 = _catalogo()
    with p1, p2:
        unidades = [u for u in listar_unidades_mrl_plan(PLAN) if u["material"] == SLC046]
        auditoria = auditar_consumo_plan(PLAN)
    assert len(unidades) == 3
    assert all(u.get("nesting_key") for u in unidades), "toda barra pedida debe tener mapa"
    piezas = [c for u in unidades for c in u.get("cortes_slot") or []]
    assert len(piezas) == 11
    assert not any(_slot_overflow(u["cortes_slot"], 240.0) for u in unidades)

    barra = PLAN["data"][SLC046][0]
    vistas = [
        vista_barra_para_unidad_mrl(
            barra,
            240.0,
            u["unit_idx"],
            unit_idx_en_tira=u.get("unit_idx_en_tira"),
            n_slots_tira=u.get("n_slots_tira"),
            cortes_slot=u.get("cortes_slot"),
        )
        for u in unidades
    ]
    etiquetas = [c.get("_etiqueta_pieza") for v in vistas for c in v["cortes"]]
    assert len(etiquetas) == 11
    assert sum(1 for e in etiquetas if e.startswith("REFUERZO SEG 4")) == 2, etiquetas
    assert auditoria["ok"], auditoria


if __name__ == "__main__":
    test_pedido_mrl_cuenta_barras_reales_de_240()
    test_mapa_muestra_todas_las_piezas_en_barras_que_caben()
    print("OK")
