#!/usr/bin/env python3
"""Exporta todos los Largos de React-Herinox (tabla Plate) a CSV.

Incluye todas las columnas de Plate + el último PriceHistory útil
(APPROVED/PENDING con precio > 0), igual que usa el catálogo ANS.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2
from psycopg2.extras import RealDictCursor

from catalogo_largos import HERINOX_DB_CONFIG


def main() -> int:
    out_dir = ROOT / "exports"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"herinox_largos_completo_{stamp}.csv"

    conn = psycopg2.connect(**HERINOX_DB_CONFIG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'Plate'
            ORDER BY ordinal_position
            """
        )
        cols = [r["column_name"] for r in cur.fetchall()]
        if not cols:
            print("ERROR: no se encontraron columnas en public.Plate")
            return 1

        print(f"Plate columns ({len(cols)}): {', '.join(cols)}")
        quoted = ", ".join(f'p."{c}"' for c in cols)
        sql = f"""
            SELECT
                {quoted},
                ph."newPrice" AS precio_hist_newPrice,
                ph."pricePerLb" AS precio_hist_pricePerLb,
                ph."approvalStatus" AS precio_hist_approvalStatus,
                ph."changedAt" AS precio_hist_changedAt
            FROM "Plate" p
            LEFT JOIN LATERAL (
                SELECT "newPrice", "pricePerLb", "approvalStatus", "changedAt"
                FROM "PriceHistory"
                WHERE "plateId" = p."id"
                  AND (
                    COALESCE("newPrice", 0) > 0
                    OR COALESCE("pricePerLb", 0) > 0
                  )
                  AND "approvalStatus" IN ('APPROVED', 'PENDING')
                ORDER BY
                  CASE WHEN "approvalStatus" = 'APPROVED' THEN 0 ELSE 1 END,
                  "changedAt" DESC NULLS LAST
                LIMIT 1
            ) ph ON TRUE
            WHERE COALESCE(p."inventoryType", 'PLATE') = 'LARGO'
            ORDER BY
                p."perfilEstructural" NULLS LAST,
                p."material" NULLS LAST,
                p."codigo" NULLS LAST,
                p."length" NULLS LAST,
                p."width" NULLS LAST
        """
        cur.execute(sql)
        rows = cur.fetchall() or []
        print(f"LARGO rows: {len(rows)}")
        if not rows:
            print("Sin filas LARGO en Herinox.")
            return 1

        fieldnames = list(rows[0].keys())
        with out_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {k: ("" if v is None else v) for k, v in dict(row).items()}
                )

        cur.execute(
            """
            SELECT
                COUNT(*) AS n,
                COUNT(*) FILTER (WHERE "disponible" IS TRUE) AS disp,
                COUNT(*) FILTER (WHERE "disponible" IS NOT TRUE) AS no_disp
            FROM "Plate"
            WHERE COALESCE("inventoryType", 'PLATE') = 'LARGO'
            """
        )
        summ = cur.fetchone() or {}
        print(
            f"disponible=true: {summ.get('disp')} | "
            f"disponible!=true: {summ.get('no_disp')}"
        )
        print(f"CSV: {out_path}")
        return 0
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
