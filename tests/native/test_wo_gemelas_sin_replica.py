#!/usr/bin/env python
"""Candado: WOs independientes; no hay réplica automática a gemelas.

Caso 2026-08-07: tras mudar Top_Cover_1 WO1→WO2, el donante perdía placas.
La réplica `_replicar_lote_activo_a_gemelos` / aliasing de `data` entre lotes
podía reescribir el nest de una WO con el de otra. Se elimina la copia.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))


def _grupo(n_hojas, tag):
    hojas = []
    for i in range(n_hojas):
        hojas.append(
            {
                "placa_id": "PLC063",
                "sheet_seq": i + 1,
                "piezas": [{"nombre": f"{tag}_P{i}", "poligonos": [[[0, 0], [1, 0], [1, 1]]]}],
            }
        )
    return {"hojas": hojas, "piezas_pool": [{"nombre": f"{tag}_P{i}"} for i in range(n_hojas)]}


def test_replicar_es_noop():
    from interface.qt.tabs._mixin_plate_mgmt import PlateManagementMixin

    class Fake(PlateManagementMixin):
        def __init__(self):
            self.app = type("A", (), {})()
            self.lote_actual_idx = 0

        def _data_tiene_transferencias_cross_wo(self, data):
            return False

        def _clonar_datos_partes_edicion(self, datos):
            return copy.deepcopy(datos)

    fx = Fake()
    g0 = _grupo(14, "WO0")
    g1 = _grupo(2, "WO1")
    fx.app.resultados_multilote = [
        {"lote_k": 10, "data": {"0.313_A 36": g0}},
        {"lote_k": 10, "data": {"0.313_A 36": g1}},  # mismo lote_k = “gemela”
    ]
    fx.app.resultados_nesting = fx.app.resultados_multilote[0]["data"]
    snap1 = copy.deepcopy(fx.app.resultados_multilote[1]["data"])
    fx._replicar_lote_activo_a_gemelos()
    assert fx.app.resultados_multilote[1]["data"] == snap1, (
        "réplica a gemelas debe estar desactivada"
    )
    assert len(fx.app.resultados_multilote[1]["data"]["0.313_A 36"]["hojas"]) == 2


def test_persistir_y_cargar_desacoplan():
    from interface.qt.tabs._mixin_plate_mgmt import PlateManagementMixin

    class Fake(PlateManagementMixin):
        def __init__(self):
            self.app = type("A", (), {})()
            self.lote_actual_idx = 0

    fx = Fake()
    shared = {"0.313_A 36": _grupo(5, "S")}
    fx.app.resultados_multilote = [
        {"lote_k": 10, "data": shared},
        {"lote_k": 11, "data": shared},  # alias peligroso
    ]
    fx.app.resultados_nesting = shared
    fx._persistir_lote_saliente(0, nuevo_idx=1)
    # Tras persistir, el slot 0 no debe ser el mismo objeto que resultará al cargar 1
    fx._cargar_resultados_lote_idx(1)
    assert fx.app.resultados_nesting is not fx.app.resultados_multilote[0]["data"]
    fx.app.resultados_nesting["0.313_A 36"]["hojas"].pop()
    assert len(fx.app.resultados_multilote[0]["data"]["0.313_A 36"]["hojas"]) == 5, (
        "mutar WO activa no debe vaciar placas de la otra WO"
    )


def test_transfer_meta_no_replica_ni_borra_otras_placas_donante():
    """Muda 1 pieza: el donante solo pierde esa pieza; el resto de hojas intactas."""
    from modules.nesting_engine.manager import MotorNesting

    motor = MotorNesting.__new__(MotorNesting)
    pieza_a = {
        "nombre": "Top_Cover_1",
        "debug_id": "0.313_A 36::Top_Cover_1::rep1",
        "shift_x": 10.0,
        "shift_y": 10.0,
        "rot_deg": 0.0,
        "poligonos": [[[0, 0], [100, 0], [100, 50], [0, 50]]],
        "area": 5000.0,
    }
    pieza_b = {
        "nombre": "Top_Cover_1",
        "debug_id": "0.313_A 36::Top_Cover_1::rep2",
        "shift_x": 200.0,
        "shift_y": 10.0,
        "rot_deg": 0.0,
        "poligonos": [[[0, 0], [100, 0], [100, 50], [0, 50]]],
        "area": 5000.0,
    }
    otras = [
        {
            "nombre": f"Other_{i}",
            "debug_id": f"0.313_A 36::Other_{i}::rep1",
            "shift_x": float(i * 10),
            "shift_y": 0.0,
            "rot_deg": 0.0,
            "poligonos": [[[0, 0], [10, 0], [10, 10], [0, 10]]],
            "area": 100.0,
        }
        for i in range(3)
    ]

    def hoja(pid, seq, piezas):
        return {
            "placa_id": pid,
            "placa_w": 6096.0,
            "placa_h": 2438.4,
            "sheet_seq": seq,
            "sheet_uid": f"0.313_A 36::{pid}::{seq}",
            "es_retazo": False,
            "piezas": piezas,
            "kerf_usado": 0.15,
            "margin_usado": 5.0,
            "corner_usado": "INFERIOR IZQUIERDA",
        }

    # 3 placas en donante; se mueve 1 Top_Cover de la última
    h1 = hoja("PLC063", 1, otras[:1])
    h2 = hoja("PLC063", 2, otras[1:2])
    h3 = hoja("PLC063", 3, [pieza_a, pieza_b, otras[2]])
    dest = hoja("PLC059", 1, [])

    origen_data = {
        "0.313_A 36": {
            "hojas": [h1, h2, h3],
            "piezas_pool": [{"nombre": p["nombre"]} for p in (otras + [pieza_a, pieza_b])],
            "piezas_pool_engine": True,
        }
    }
    dest_data = {
        "0.313_A 36": {
            "hojas": [dest],
            "piezas_pool": [],
            "piezas_pool_engine": True,
        }
    }

    # Stub mínimo del renest destino: aceptar la pieza
    def fake_transferir_y_reoptimizar(
        resultados_nesting,
        pieza_info,
        hoja_destino,
        hoja_origen=None,
        idx_hint=None,
        resultados_destino=None,
        dest_grupo=None,
    ):
        # quitar de origen y poner en destino
        piezas = hoja_origen.get("piezas") or []
        if idx_hint is not None and 0 <= idx_hint < len(piezas):
            mov = piezas.pop(idx_hint)
        else:
            mov = pieza_info
            piezas[:] = [p for p in piezas if p is not mov and id(p) != id(mov)]
        hoja_destino.setdefault("piezas", []).append(mov)
        if dest_grupo is not None:
            motor._ajustar_piezas_pool_cross_wo(
                origen_data["0.313_A 36"], dest_grupo, [mov]
            )
        return True

    motor.transferir_y_reoptimizar = fake_transferir_y_reoptimizar
    motor._resolver_candidatos_transferencia = (
        MotorNesting._resolver_candidatos_transferencia.__get__(motor, MotorNesting)
    )
    motor._piezas_reales_en_hoja = MotorNesting._piezas_reales_en_hoja.__get__(
        motor, MotorNesting
    )
    motor._pieza_real_en_hoja_por_idx = MotorNesting._pieza_real_en_hoja_por_idx.__get__(
        motor, MotorNesting
    )
    motor._misma_pieza_visual = MotorNesting._misma_pieza_visual.__get__(motor, MotorNesting)
    motor._grupo_de_hoja = MotorNesting._grupo_de_hoja.__get__(motor, MotorNesting)
    motor._resolver_hoja_viva = MotorNesting._resolver_hoja_viva.__get__(motor, MotorNesting)
    motor._idx_hoja_en_grupo = MotorNesting._idx_hoja_en_grupo.__get__(motor, MotorNesting)
    motor._conteo_piezas_en_grupos = MotorNesting._conteo_piezas_en_grupos.__get__(
        motor, MotorNesting
    )
    motor._conteo_piezas_reales_en_hojas = MotorNesting._conteo_piezas_reales_en_hojas.__get__(
        motor, MotorNesting
    )
    motor._ajustar_piezas_pool_cross_wo = MotorNesting._ajustar_piezas_pool_cross_wo.__get__(
        motor, MotorNesting
    )
    motor._agregar_piezas_a_pool = MotorNesting._agregar_piezas_a_pool.__get__(motor, MotorNesting)
    motor._quitar_piezas_de_pool = MotorNesting._quitar_piezas_de_pool.__get__(motor, MotorNesting)

    n_hojas_antes = len(origen_data["0.313_A 36"]["hojas"])
    res = motor.transferir_piezas_a_placa(
        origen_data,
        h3,
        dest,
        piezas_especificas=[pieza_a],
        piezas_indices=[0],
        resultados_destino=dest_data,
    )
    assert res.get("ok"), res
    assert res.get("movidas") == 1
    assert len(origen_data["0.313_A 36"]["hojas"]) == n_hojas_antes, (
        "no deben desaparecer otras placas del donante"
    )
    assert len(h3["piezas"]) == 2
    assert len(dest["piezas"]) == 1


def main() -> int:
    test_replicar_es_noop()
    test_persistir_y_cargar_desacoplan()
    test_transfer_meta_no_replica_ni_borra_otras_placas_donante()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
