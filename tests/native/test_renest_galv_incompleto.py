"""Candado: inventarios Galv incompletos se recuperan al renestear calibre.

Caso planta: edición manual dejó 0.11811_GALVANIZADO con «faltan N» y el renesteo
solo usaba conteo del nest (reducido), así que nunca recuperaba las piezas vs PARTS.
Además al fusionar grupos debía unirse piezas_pool.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from modules.nesting_engine.manager import MotorNesting


def test_merge_resultado_concatena_piezas_pool():
    resultados = {
        "0.11811_GALVANIZADO": {
            "hojas": [{"piezas": [{"nombre": "A"}]}],
            "piezas_pool": [{"nombre": "A"}],
            "piezas_pool_engine": True,
            "costo_total": 1.0,
            "costo_empresa": 1.0,
            "costo_proveedor": 0.0,
        }
    }
    MotorNesting._merge_resultado_en_mapa(
        resultados,
        "0.11811_GALVANIZADO",
        {
            "hojas": [{"piezas": [{"nombre": "B"}]}],
            "piezas_pool": [{"nombre": "B"}],
            "piezas_pool_engine": True,
            "costo_total": 2.0,
            "costo_empresa": 0.0,
            "costo_proveedor": 2.0,
        },
    )
    merged = resultados["0.11811_GALVANIZADO"]
    assert len(merged["hojas"]) == 2
    nombres_pool = [p["nombre"] for p in merged["piezas_pool"]]
    assert nombres_pool == ["A", "B"]
    assert merged.get("piezas_pool_engine") is True


class _FakeTab:
    def __init__(self, app):
        self.app = app

    # Copias mínimas del mixin (sin Qt).
    def _contar_piezas_reales_grupo(self, clave):
        from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin

        return NestingCalcMixin._contar_piezas_reales_grupo(self, clave)

    def _conteo_piezas_job_grupo(self, clave):
        from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin

        return NestingCalcMixin._conteo_piezas_job_grupo(self, clave)

    def _grupo_inventario_incompleto(self, clave):
        from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin

        return NestingCalcMixin._grupo_inventario_incompleto(self, clave)

    def _conteo_para_renest_calibre(self, clave):
        from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin

        return NestingCalcMixin._conteo_para_renest_calibre(self, clave)

    def _nombre_canonico_pieza(self, nom):
        return str(nom or "").strip()

    def _es_pieza_virtual(self, nom):
        return False

    def _datos_partes_activos_para_nesting(self):
        return list(getattr(self.app, "datos_partes_actuales", []) or [])


def test_renest_incompleto_restaura_desde_parts():
    app = SimpleNamespace(
        resultados_nesting={
            "0.11811_GALVANIZADO": {
                "inventario_ok": False,
                "advertencia": "Inventario incompleto: faltan 97 colocación(es), sobran 0.",
                "hojas": [
                    {
                        "piezas": [
                            {"nombre": "GENE-BKS-10-001"},
                            {"nombre": "GENE-BKS-10-001"},
                        ]
                    }
                ],
            }
        },
        datos_partes_actuales=[
            ("GENE-BKS-10-001", "GALVANIZADO", 5, "0.11811", "LISTO", r"C:\x.dxf"),
            ("OTRA-A36", "A 36", 10, "0.11811", "LISTO", r"C:\y.dxf"),
        ],
        motor_nesting=MotorNesting.__new__(MotorNesting),
    )
    tab = _FakeTab(app)
    conteo = tab._conteo_para_renest_calibre("0.11811_GALVANIZADO")
    # Nest tenía 2; PARTS pide 5 → debe recuperar a 5.
    assert conteo.get("GENE-BKS-10-001") == 5
    assert "OTRA-A36" not in conteo


def test_renest_completo_conserva_nest_sin_inflar():
    app = SimpleNamespace(
        resultados_nesting={
            "0.11811_A 36": {
                "inventario_ok": True,
                "hojas": [
                    {"piezas": [{"nombre": "P1"}, {"nombre": "P1"}]}
                ],
            }
        },
        datos_partes_actuales=[
            ("P1", "A 36", 8, "0.11811", "LISTO", r"C:\z.dxf"),
        ],
        motor_nesting=MotorNesting.__new__(MotorNesting),
    )
    tab = _FakeTab(app)
    conteo = tab._conteo_para_renest_calibre("0.11811_A 36")
    assert conteo.get("P1") == 2


if __name__ == "__main__":
    test_merge_resultado_concatena_piezas_pool()
    test_renest_incompleto_restaura_desde_parts()
    test_renest_completo_conserva_nest_sin_inflar()
    print("SMOKE OK")
