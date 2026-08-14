"""Candado: la tabla de placa distingue cantidad local, total nest y demanda."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _pieza(nombre: str) -> dict:
    return {"nombre": nombre, "poligonos": [[(0, 0), (1, 0), (1, 1)]]}


def test_auditoria_cuenta_el_grupo_completo_no_solo_h1() -> None:
    """PARTS puede pedir 6; H1 mostrar 2 no es una discrepancia por sí misma."""
    from interface.qt.nesting_graphics import _auditoria_cantidades_grupo

    app = SimpleNamespace(
        resultados_nesting={
            "0.0747_A 36": {
                "piezas_pool": [_pieza("GRANDE")] * 6 + [_pieza("ANGOSTA")] * 6,
                "hojas": [
                    {"piezas": [_pieza("GRANDE")] * 2 + [_pieza("ANGOSTA")] * 6},
                    {"piezas": [_pieza("GRANDE")] * 4},
                    # Vista virtual: no puede inflar el conteo.
                    {"cu_rtz_virtual": True, "piezas": [_pieza("GRANDE")] * 4},
                ],
            }
        }
    )

    audit = _auditoria_cantidades_grupo(app, "0.0747_A 36")
    assert audit["GRANDE"] == {"nest": 6, "req": 6}
    assert audit["ANGOSTA"] == {"nest": 6, "req": 6}


def test_auditoria_expone_faltante_en_vez_de_ocultarlo() -> None:
    """Si H2-Hn no contienen las otras piezas, la tabla debe marcar 2/2/6."""
    from interface.qt.nesting_graphics import _auditoria_cantidades_grupo

    app = SimpleNamespace(
        resultados_nesting={
            "0.0747_A 36": {
                "piezas_pool": [_pieza("GRANDE")] * 6,
                "hojas": [{"piezas": [_pieza("GRANDE")] * 2}],
            }
        }
    )
    audit = _auditoria_cantidades_grupo(app, "0.0747_A 36")
    assert audit["GRANDE"] == {"nest": 2, "req": 6}
    assert audit["GRANDE"]["nest"] != audit["GRANDE"]["req"]


def test_tabla_declara_los_tres_alcances() -> None:
    """Evita volver a etiquetar la cantidad local ambigua como 'Cant.'."""
    fuente = (RAIZ / "interface" / "qt" / "nesting_graphics.py").read_text(
        encoding="utf-8"
    )
    assert "PLACA / NEST / REQ" in fuente


if __name__ == "__main__":
    test_auditoria_cuenta_el_grupo_completo_no_solo_h1()
    test_auditoria_expone_faltante_en_vez_de_ocultarlo()
    test_tabla_declara_los_tres_alcances()
    print("OK auditoria_cantidades_nesting")
