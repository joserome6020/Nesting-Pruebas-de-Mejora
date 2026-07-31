"""Chequea si ANG035 existe en catálogo Herinox y con qué costo."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "interface")):
    if p not in sys.path:
        sys.path.insert(0, p)

from catalogo_largos import (
    _cargar_placas_largos_desde_herinox,
    datos_material_requerido_pedido,
)


def main():
    cat = _cargar_placas_largos_desde_herinox(solo_disponibles=False) or []
    if isinstance(cat, dict):
        placas = list(cat.values())
    else:
        placas = list(cat)
    print("placas en catalogo:", len(placas))

    by_code = []
    for placa in placas:
        if not isinstance(placa, dict):
            continue
        cod = str(placa.get("codigo") or placa.get("clave") or "").strip().upper()
        if cod == "ANG035" or "ANG035" in cod:
            by_code.append(placa)

    print("matches codigo ANG035:", len(by_code))
    for p in by_code[:5]:
        print(
            {
                "codigo": p.get("codigo") or p.get("clave"),
                "descripcion": str(p.get("descripcion") or p.get("nombre") or "")[:80],
                "costo_actual": p.get("costo_actual"),
                "disponible": p.get("disponible"),
            }
        )

    # sample ANG* codes present
    angs = sorted(
        {
            str(p.get("codigo") or "").strip().upper()
            for p in placas
            if isinstance(p, dict) and str(p.get("codigo") or "").upper().startswith("ANG")
        }
    )
    print("codigos ANG* en Herinox:", angs)

    for mat in (
        "ANG035 | ANGULO perfil | A 36 | 3 X 3 X 0.3125 IN",
        "ANG022 | ANGULO perfil | A 36 | 2 X 2 X 0.25 IN",
    ):
        d = datos_material_requerido_pedido(mat, 1, catalogo=placas)
        print("pedido", mat.split("|", 1)[0].strip(), "->", d)


if __name__ == "__main__":
    main()
