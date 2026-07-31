"""Inserta SCO028 (placa CU 1.75 x 144 x 1/4) en Herinox tabla Plate."""
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

# Densidad cobre ~0.323 lb/in³ (misma que SLC041 / SCO0xx en UI)
# 1.75 * 144 * 0.25 * 0.323 = 20.349
WIDTH = 1.75
LENGTH = 144.0
THK_IN = 0.25
LB = round(WIDTH * LENGTH * THK_IN * 0.323, 3)
# Precio inventado ~$4.3/LB (alineado a SLC041 $100 / 23.256)
COSTO = round(LB * 4.3, 2)


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
        "codigo": "SCO028",
        "placa": "PLACA",
        "material": "CU",
        "thickness": "in",
        "thk": "1/4",
        "width": WIDTH,
        "length": LENGTH,
        "costoActual": COSTO,
        "lbCalculadas": LB,
        "disponible": True,
        "inventoryType": "PLATE",
    }

    print(f"Datos: {plate}")

    conn = psycopg2.connect(**HERINOX_DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'Plate'
                ORDER BY ordinal_position
                """
            )
            cols = {c["column_name"] for c in cur.fetchall()}
            print("Columnas Plate:", sorted(cols))

            cur.execute('SELECT * FROM "Plate" WHERE "codigo" = %s', (plate["codigo"],))
            existing = cur.fetchone()
            if existing:
                print(
                    "Ya existe SCO028:",
                    json.dumps(dict(existing), default=str, ensure_ascii=False),
                )
                return 0

            # Referencia: otra placa CU 1/4 (p.ej. SLC041 o SCO015)
            cur.execute(
                """
                SELECT *
                FROM "Plate"
                WHERE "material" = 'CU'
                  AND COALESCE("inventoryType", 'PLATE') IN ('PLATE', 'PLACA', 'LAMINA')
                  AND (
                    "thk" IN ('1/4', '0.25', '0,25')
                    OR "thk"::text ILIKE '%%1/4%%'
                    OR CAST("width" AS text) IS NOT NULL
                  )
                ORDER BY
                  CASE WHEN "codigo" IN ('SLC041', 'SCO015', 'SCO003', 'SCO014', 'SCO002')
                       THEN 0 ELSE 1 END,
                  "codigo"
                LIMIT 5
                """
            )
            refs = cur.fetchall()
            for r in refs:
                print(
                    "Ref:",
                    r.get("codigo"),
                    "thk=",
                    r.get("thk"),
                    "thickness=",
                    r.get("thickness"),
                    "w=",
                    r.get("width"),
                    "l=",
                    r.get("length"),
                    "lb=",
                    r.get("lbCalculadas"),
                    "costo=",
                    r.get("costoActual"),
                    "placa=",
                    r.get("placa"),
                )

            ref = refs[0] if refs else None
            if ref:
                # Copiar formato exacto de thickness/thk de la referencia 1/4
                if ref.get("thickness") not in (None, ""):
                    plate["thickness"] = ref["thickness"]
                if ref.get("thk") not in (None, ""):
                    # Si la ref es 1/4 o 0.25, usamos el mismo formato
                    thk_ref = str(ref["thk"]).strip()
                    if thk_ref in ("1/4", "0.25", "0,25") or "1/4" in thk_ref:
                        plate["thk"] = ref["thk"]
                    elif abs(float(str(thk_ref).replace(",", ".")) - 0.25) < 0.001:
                        plate["thk"] = ref["thk"]
                if ref.get("placa") not in (None, ""):
                    plate["placa"] = ref["placa"]

            insert_cols = [
                c
                for c in (
                    "id",
                    "codigo",
                    "placa",
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
                "placa": plate.get("placa"),
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
                    "descripcion",
                    "unidad",
                ):
                    if extra in cols and ref.get(extra) not in (None, ""):
                        values[extra] = ref[extra]
                        if extra not in insert_cols:
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

            sql = (
                f'INSERT INTO "Plate" ({col_sql}) VALUES ({", ".join(placeholders)}) '
                'RETURNING "codigo", "placa", "material", "thickness", "thk", '
                '"width", "length", "lbCalculadas", "costoActual", "disponible", "inventoryType"'
            )
            cur.execute(sql, params)
            row = cur.fetchone()
            conn.commit()
            print("OK insertado:", json.dumps(dict(row), default=str, ensure_ascii=False))
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
