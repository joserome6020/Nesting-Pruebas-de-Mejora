"""Candado SWO-108: SKU ContPAQ capturado como Herinox no bloquea la OC.

Caso real 2026-10-02: MRL 1825 de SWO-108 traía ``codigo_herinox=TUB017``
(material Inventor ``TUB017 | TUBO perfil | A 36 | TUBO A36 CED 40 2 IN``).
TUB017 es el SKU ContPAQ de la equivalencia VERIFIED ``HR166 → TUB017``. El
resolver solo buscaba por Herinox, el match 1:1 ``TUB017 → TUB017`` chocaba con
HR166 y la fila quedaba PENDING: preflight ContPAQ HTTP 500, sin VSM ni OC.
Ya había pasado en SWO-068 con TUB005 (HR174).

Reglas:
- Si el código ya es el SKU ContPAQ de una equivalencia VERIFIED, se resuelve
  con esa equivalencia y la fila queda con el Herinox real (HR166).
- Un código sin equivalencia ni SKU verificado sigue PENDING (no se adivina).
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from interface.material_code_mapping_service import (  # noqa: E402
    mapeo_es_verificado,
    resolver_codigo_contpaq,
    sincronizar_codigos_contpaq_mrl,
)


EQUIVALENCIAS = [
    {"id": 59, "herinox_codigo": "HR166", "codigo_contpaq": "TUB017",
     "estatus": "VERIFIED", "activo": True, "origen": "CATALOGO_CONTPAQ_AUDITADO"},
    {"id": 1, "herinox_codigo": "HR164", "codigo_contpaq": "TUB010",
     "estatus": "VERIFIED", "activo": True, "origen": "CATALOGO_CONTPAQ_AUDITADO"},
]


class CursorFalso:
    def __init__(self, mrl: list[dict]):
        self.mrl = mrl
        self.updates: list[tuple] = []
        self._resultado: list[dict] = []
        self.rowcount = 0

    def execute(self, sql: str, params=()):
        s = " ".join(sql.split())
        p = [str(x).upper() if isinstance(x, str) else x for x in (params or ())]
        if s.startswith("SELECT id, codigo, codigo_herinox FROM material_requerido_ldg"):
            self._resultado = [dict(f) for f in self.mrl]
        elif "FROM material_codigo_equivalencias" in s and "UPPER(codigo_contpaq)" in s:
            self._resultado = [
                self._fila(e) for e in EQUIVALENCIAS
                if e["activo"] and e["estatus"] == "VERIFIED"
                and e["codigo_contpaq"] == p[0]
            ]
        elif "FROM material_codigo_equivalencias" in s and "UPPER(herinox_codigo)" in s:
            self._resultado = [
                self._fila(e) for e in EQUIVALENCIAS
                if e["activo"] and e["herinox_codigo"] == p[0]
            ]
        elif s.startswith("UPDATE material_requerido_ldg"):
            self.updates.append(tuple(params))
            self.rowcount = 1
            self._resultado = []
        else:
            raise AssertionError(f"SQL inesperado en el candado: {s[:120]}")

    @staticmethod
    def _fila(e: dict) -> dict:
        return {
            "id": e["id"], "herinox_codigo": e["herinox_codigo"],
            "codigo_contpaq": e["codigo_contpaq"], "estatus": e["estatus"],
            "origen": e["origen"], "especificacion_normalizada": "",
            "verificado_at": None, "contpaq_catalog_seen_at": None,
        }

    def fetchone(self):
        return self._resultado[0] if self._resultado else None

    def fetchall(self):
        return list(self._resultado)


def test_resolver_sku_contpaq_capturado_como_herinox():
    cur = CursorFalso([])
    mapeo = resolver_codigo_contpaq(cur, "TUB017", resultados_catalogo={})
    assert mapeo_es_verificado(mapeo), mapeo
    assert mapeo["herinox_codigo"] == "HR166"
    assert mapeo["codigo_contpaq"] == "TUB017"
    assert mapeo["mapping_id"] == 59


def test_resolver_herinox_normal_sin_cambio():
    mapeo = resolver_codigo_contpaq(CursorFalso([]), "HR164", resultados_catalogo={})
    assert mapeo_es_verificado(mapeo)
    assert mapeo["herinox_codigo"] == "HR164"
    assert mapeo["codigo_contpaq"] == "TUB010"


def test_codigo_desconocido_sigue_pending():
    mapeo = resolver_codigo_contpaq(CursorFalso([]), "TUB999", resultados_catalogo={})
    assert not mapeo_es_verificado(mapeo)
    assert mapeo["estatus"] == "PENDING"


def test_sync_mrl_swo108_fila_1825():
    cur = CursorFalso([
        {"id": 1820, "codigo": "HR164", "codigo_herinox": "HR164"},
        {"id": 1825, "codigo": "TUB017", "codigo_herinox": "TUB017"},
    ])
    res = sincronizar_codigos_contpaq_mrl(
        cur, orden_id="SWO-108", tipo_orden="SWO", resultados_catalogo={}
    )
    assert res["pendientes"] == 0, res
    assert res["verificadas"] == 2
    upd = {u[-1]: u for u in cur.updates}
    assert upd[1825][:4] == ("HR166", "TUB017", "VERIFIED", 59), upd[1825]
    assert upd[1820][:4] == ("HR164", "TUB010", "VERIFIED", 1), upd[1820]


if __name__ == "__main__":
    test_resolver_sku_contpaq_capturado_como_herinox()
    test_resolver_herinox_normal_sin_cambio()
    test_codigo_desconocido_sigue_pending()
    test_sync_mrl_swo108_fila_1825()
    print("OK test_contpaq_sku_como_herinox")
