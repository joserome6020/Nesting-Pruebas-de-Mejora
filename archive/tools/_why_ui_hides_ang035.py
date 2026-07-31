"""Por que React no muestra ANG035 si esta en Plate."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "interface")):
    if p not in sys.path:
        sys.path.insert(0, p)

import psycopg2
from psycopg2.extras import RealDictCursor
from catalogo_largos import HERINOX_DB_CONFIG


def main():
    conn = psycopg2.connect(**HERINOX_DB_CONFIG)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT "codigo", "disponible", "costoActual", "costoActualUsd", "costoPorLbUsd",
               "width", "length", "lbCalculadas", "descripcion", "thk", "perfilEstructural"
        FROM "Plate"
        WHERE "inventoryType" = 'LARGO'
          AND UPPER(TRIM("perfilEstructural")) = 'ANGULO'
          AND UPPER(TRIM("material")) LIKE '%%A 36%%'
        ORDER BY "codigo"
        """
    )
    rows = cur.fetchall()
    print(f"ANGULO A36 LARGO en BD: {len(rows)}")
    for r in rows:
        costo = float(r["costoActual"] or 0)
        flag = []
        if costo <= 0:
            flag.append("COSTO_0")
        if float(r["lbCalculadas"] or 0) <= 1.01:
            flag.append("LB_STUB")
        if float(r["width"] or 0) <= 1.01:
            flag.append("WIDTH_STUB")
        print(
            f"  {r['codigo']}: costo={costo} lb={r['lbCalculadas']} w={r['width']} "
            f"desc={r['descripcion']!r} {'|'.join(flag) or 'OK'}"
        )

    con_costo = [r for r in rows if float(r["costoActual"] or 0) > 0]
    print(f"\nCon costo > 0: {len(con_costo)}  (UI dice 8 disponibles)")
    print("codigos con costo:", [r["codigo"] for r in con_costo])
    print("ocultos tipicos (costo 0):", [r["codigo"] for r in rows if float(r["costoActual"] or 0) <= 0])

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
