"""Probe Herinox Plate table for empresa/proveedor split."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2
from psycopg2.extras import RealDictCursor
from catalogo_largos import HERINOX_DB_CONFIG

conn = psycopg2.connect(**HERINOX_DB_CONFIG)
cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute(
    """
    SELECT column_name FROM information_schema.columns
    WHERE table_schema='public' AND table_name='Plate'
    ORDER BY ordinal_position
    """
)
cols = [r["column_name"] for r in cur.fetchall()]
print("Plate columns:", cols)

for c in ("origen", "proveedor", "stockType", "owner", "tipo", "source", "inventoryType"):
    if c in cols:
        cur.execute(
            f'SELECT "{c}", COUNT(*) AS n FROM "Plate" '
            f'WHERE COALESCE("inventoryType", \'PLATE\') IN (\'PLATE\', \'PLACA\', \'LAMINA\') '
            f'GROUP BY "{c}" ORDER BY n DESC'
        )
        print(f"\n{c} distribution:")
        for r in cur.fetchall():
            print(f"  {r[c]!r}: {r['n']}")

cur.execute(
    """
    SELECT COUNT(*) AS n FROM "Plate"
    WHERE COALESCE("inventoryType", 'PLATE') IN ('PLATE', 'PLACA', 'LAMINA')
      AND "codigo" IS NOT NULL AND TRIM("codigo") <> ''
    """
)
print("\nTotal PLATE rows:", cur.fetchone()["n"])

for c in ("placa", "descripcion"):
    if c in cols:
        cur.execute(
            f'SELECT "{c}", COUNT(*) AS n FROM "Plate" '
            f'WHERE COALESCE("inventoryType", \'PLATE\') IN (\'PLATE\', \'PLACA\', \'LAMINA\') '
            f'GROUP BY "{c}" ORDER BY n DESC LIMIT 15'
        )
        print(f"\n{c} distribution:")
        for r in cur.fetchall():
            print(f"  {r[c]!r}: {r['n']}")

cur.execute(
    """
    SELECT "codigo", "material", "placa", "descripcion", "disponible"
    FROM "Plate"
    WHERE COALESCE("inventoryType", 'PLATE') IN ('PLATE', 'PLACA', 'LAMINA')
    ORDER BY "codigo"
    LIMIT 8
    """
)
print("\nSample rows:")
for r in cur.fetchall():
    print(dict(r))

# Compare with Excel codes
import pandas as pd
xlsx = ROOT / "modules" / "Plates.xlsx"
if xlsx.is_file():
    emp = pd.read_excel(xlsx, sheet_name=0, dtype=str)
    prov = pd.read_excel(xlsx, sheet_name=1, dtype=str)
    emp_codes = set(emp["Arga Code"].dropna().str.strip().str.upper())
    prov_codes = set(prov["Arga Code"].dropna().str.strip().str.upper())
    cur.execute(
        """
        SELECT UPPER(TRIM("codigo")) AS c FROM "Plate"
        WHERE COALESCE("inventoryType", 'PLATE') IN ('PLATE', 'PLACA', 'LAMINA')
        """
    )
    db_codes = {r["c"] for r in cur.fetchall() if r["c"]}
    print(f"\nExcel empresa: {len(emp_codes)}, proveedor: {len(prov_codes)}, DB: {len(db_codes)}")
    print(f"In Excel empresa but not DB: {len(emp_codes - db_codes)}")
    print(f"In Excel prov but not DB: {len(prov_codes - db_codes)}")
    print(f"In DB but not Excel: {len(db_codes - emp_codes - prov_codes)}")

cur.close()
conn.close()
