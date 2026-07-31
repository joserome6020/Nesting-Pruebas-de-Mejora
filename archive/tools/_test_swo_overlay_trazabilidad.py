"""Regresión: overlays SWO no entran al gate de trazabilidad WO→Job."""
from modules.nesting_engine.efficiency_metrics import _es_pieza_real_nombre

OVERLAYS = [
    "REF__W.O. 1 X1__P11",
    "TATUAJE__RTZ1-0.375",
    "RETAZO_GUILLOTINA__RTZ1-0.375",
    "REMANENTE__RTZ1",
    "CU_CORTE__X",
    "RTZCU_ZONA__RTZCU1",
]
REALES = [
    "W.O. 1 X1__62176-1248-P11",
    "W.O. 1 X1__62176-1248-P29",
    "62176-1248-P11",
]


def prefijos_trazables(nombres):
    out = set()
    for n in nombres:
        if not _es_pieza_real_nombre(n):
            continue
        u = n.upper()
        if "__" in u:
            out.add(u.split("__")[0].strip())
    return out


def main():
    assert all(not _es_pieza_real_nombre(n) for n in OVERLAYS), OVERLAYS
    assert all(_es_pieza_real_nombre(n) for n in REALES), REALES
    prefs = prefijos_trazables(OVERLAYS + REALES)
    assert prefs == {"W.O. 1 X1"}, prefs
    assert not prefs.intersection(
        {"REF", "TATUAJE", "RETAZO_GUILLOTINA", "REMANENTE", "CU_CORTE", "RTZCU_ZONA"}
    )
    print("OK: overlays excluidos; solo prefijo real W.O. 1 X1")


if __name__ == "__main__":
    main()
