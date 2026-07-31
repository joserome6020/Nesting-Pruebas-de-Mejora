"""Verdad cruda: ANG035 en BD Herinox vs lo que ve React."""
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
    print("HERINOX cfg:", {k: v for k, v in HERINOX_DB_CONFIG.items() if k != "password"})
    conn = psycopg2.connect(**HERINOX_DB_CONFIG)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT "id", "codigo", "material", "perfilEstructural", "descripcion",
               "inventoryType", "disponible", "width", "length", "thickness", "thk",
               "costoActual", "costoActualUsd", "costoPorLbUsd", "lbCalculadas",
               "createdAt", "updatedAt"
        FROM "Plate"
        WHERE UPPER(TRIM("codigo")) = 'ANG035'
           OR UPPER("descripcion") LIKE '%3 X 3 X 0.3125%'
           OR UPPER("descripcion") LIKE '%3X3X0.3125%'
        ORDER BY "codigo"
        """
    )
    rows = cur.fetchall()
    print(f"\nPlate matches: {len(rows)}")
    for r in rows:
        print(dict(r))

    cur.execute(
        """
        SELECT "codigo", "inventoryType", "disponible", "costoActual", "descripcion"
        FROM "Plate"
        WHERE UPPER(TRIM("codigo")) LIKE 'ANG%%'
          AND "inventoryType" = 'LARGO'
        ORDER BY "codigo"
        """
    )
    print("\nTodos ANG* LARGO:")
    for r in cur.fetchall():
        print(dict(r))

    # Price history for ANG035 if exists
    cur.execute(
        """
        SELECT ph.*
        FROM "PriceHistory" ph
        JOIN "Plate" p ON p."id" = ph."plateId"
        WHERE UPPER(TRIM(p."codigo")) = 'ANG035'
        ORDER BY ph."changedAt" DESC NULLS LAST
        LIMIT 10
        """
    )
    print("\nPriceHistory ANG035:")
    for r in cur.fetchall():
        print(dict(r))

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
