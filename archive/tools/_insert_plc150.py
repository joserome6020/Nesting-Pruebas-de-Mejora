"""Inserta PLC150 en Herinox (tabla Plate) según alta individual."""
from __future__ import annotations

import json
import os
import secrets
import string
import sys
import time

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from catalogo_largos import HERINOX_DB_CONFIG


def _new_cuid() -> str:
    try:
        from cuid import cuid as _cuid

        return _cuid()
    except ImportError:
        alphabet = string.ascii_lowercase + string.digits
        ts = format(int(time.time() * 1000), "x")[-8:]
        body = "".join(secrets.choice(alphabet) for _ in range(16))
        return f"c{ts}{body}"[:25]


def main() -> int:
    plate = {
        "codigo": "PLC150",
        "material": "A 36 GALV",
        "thickness": "cal",
        "thk": "11",
        "width": 48,
        "length": 120,
        "costoActual": 2622.99,
        "lbCalculadas": 195.646,
        "disponible": True,
        "inventoryType": "PLATE",
    }

    conn = psycopg2.connect(**HERINOX_DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'Plate'
                ORDER BY ordinal_position
                """
            )
            cols = {c["column_name"]: c for c in cur.fetchall()}
            print("Columnas Plate:", list(cols.keys()))

            cur.execute('SELECT * FROM "Plate" WHERE "codigo" = %s', (plate["codigo"],))
            existing = cur.fetchone()
            if existing:
                print("Ya existe:", json.dumps({k: existing[k] for k in plate if k in existing}, default=str))
                return 0

            cur.execute(
                """
                SELECT *
                FROM "Plate"
                WHERE "material" = %s AND "thickness" = %s AND "thk" = %s
                  AND COALESCE("inventoryType", 'PLATE') IN ('PLATE', 'PLACA', 'LAMINA')
                ORDER BY "codigo"
                LIMIT 1
                """,
                (plate["material"], plate["thickness"], plate["thk"]),
            )
            ref = cur.fetchone()
            if ref:
                print("Referencia:", ref.get("codigo"))

            now_cols = [c for c in ("createdAt", "updatedAt") if c in cols]
            insert_cols = [
                c
                for c in (
                    "id",
                    "codigo",
                    "material",
                    "thickness",
                    "thk",
                    "width",
                    "length",
                    "lbCalculadas",
                    "costoActual",
                    "disponible",
                    "inventoryType",
                    "createdAt",
                    "updatedAt",
                )
                if c in cols
            ]
            values = {
                "id": _new_cuid(),
                "codigo": plate["codigo"],
                "material": plate["material"],
                "thickness": plate["thickness"],
                "thk": plate["thk"],
                "width": plate["width"],
                "length": plate["length"],
                "lbCalculadas": plate["lbCalculadas"],
                "costoActual": plate["costoActual"],
                "disponible": plate["disponible"],
                "inventoryType": plate["inventoryType"],
            }
            if ref:
                for extra in (
                    "costoActualUsd",
                    "costoPorLbUsd",
                    "densidad",
                    "proveedor",
                    "origen",
                ):
                    if extra in cols and ref.get(extra) not in (None, ""):
                        values[extra] = ref[extra]
                        insert_cols.append(extra)

            if "createdAt" in insert_cols:
                values["createdAt"] = "NOW()"
            if "updatedAt" in insert_cols:
                values["updatedAt"] = "NOW()"

            col_sql = ", ".join(f'"{c}"' for c in insert_cols)
            placeholders = []
            params = []
            for c in insert_cols:
                v = values[c]
                if v == "NOW()":
                    placeholders.append("NOW()")
                else:
                    placeholders.append("%s")
                    params.append(v)
            sql = f'INSERT INTO "Plate" ({col_sql}) VALUES ({", ".join(placeholders)}) RETURNING "codigo", "material", "thk", "width", "length", "lbCalculadas", "costoActual", "disponible"'
            cur.execute(sql, params)
            row = cur.fetchone()
            conn.commit()
            print("OK insertado:", dict(row))
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
