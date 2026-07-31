"""Diagnostica por que ANG035 no tiene costo en Herinox/MRL."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IFACE = os.path.join(ROOT, "interface")
for p in (ROOT, IFACE):
    if p not in sys.path:
        sys.path.insert(0, p)

from catalogo_largos import (
    _cargar_placas_largos_desde_herinox,
    datos_material_requerido_pedido,
    extraer_codigo_herinox_combo,
)


def main():
    cat = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
    print(f"catalogo size: {len(cat or {})}")
    hits = []
    for k, v in (cat or {}).items():
        blob = f"{k} {v}".upper()
        if "ANG035" in blob or "3 X 3 X 0.3125" in blob or "3X3X0.3125" in blob.replace(" ", ""):
            hits.append((k, v))
    print(f"hits ANG035/dim: {len(hits)}")
    for k, v in hits[:20]:
        print(" KEY", k)
        if isinstance(v, dict):
            for kk in ("codigo", "precio", "costo", "price", "descripcion", "clave", "disponible"):
                if kk in v:
                    print("  ", kk, v.get(kk))
            # print compact
            print("  keys", sorted(v.keys())[:30])
        else:
            print(" ", v)

    mat = "ANG035 | ANGULO perfil | A 36 | 3 X 3 X 0.3125 IN"
    datos = datos_material_requerido_pedido(mat, 1, catalogo=cat)
    print("\ndatos_material_requerido_pedido:", datos)
    print("codigo extraido:", extraer_codigo_herinox_combo(mat))

    # Compare ANG022 which has cost
    mat2 = "ANG022 | ANGULO perfil | A 36 | 2 X 2 X 0.25 IN"
    print("ANG022:", datos_material_requerido_pedido(mat2, 1, catalogo=cat))


if __name__ == "__main__":
    main()
