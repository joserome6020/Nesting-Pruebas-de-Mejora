#!/usr/bin/env python3
"""CSV limpio de Largos Herinox: solo columnas útiles."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg2
from psycopg2.extras import RealDictCursor
from catalogo_largos import HERINOX_DB_CONFIG

OUT = ROOT / "exports" / "herinox_largos.csv"
FIELDS = [
    "codigo",
    "perfil",
    "material",
    "descripcion",
    "ancho_in",
    "largo_ft",
    "thickness",
    "thk",
    "lb",
    "costo_mxn",
    "costo_usd",
    "costo_por_lb_usd",
    "disponible",
]


def main() -> int:
    OUT.parent.mkdir(exist_ok=True)
    conn = psycopg2.connect(**HERINOX_DB_CONFIG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT
                p."codigo" AS codigo,
                p."perfilEstructural" AS perfil,
                p."material" AS material,
                p."descripcion" AS descripcion,
                p."width" AS ancho_in,
                p."length" AS largo_ft,
                p."thickness" AS thickness,
                p."thk" AS thk,
                p."lbCalculadas" AS lb,
                p."costoActual" AS costo_mxn,
                p."costoActualUsd" AS costo_usd,
                p."costoPorLbUsd" AS costo_por_lb_usd,
                p."disponible" AS disponible
            FROM "Plate" p
            WHERE COALESCE(p."inventoryType", 'PLATE') = 'LARGO'
            ORDER BY
                p."perfilEstructural" NULLS LAST,
                p."codigo" NULLS LAST,
                p."length" NULLS LAST
            """
        )
        rows = cur.fetchall() or []
        with OUT.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {k: ("" if row.get(k) is None else row.get(k)) for k in FIELDS}
                )
        print(f"OK {len(rows)} filas -> {OUT}")
        return 0
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
