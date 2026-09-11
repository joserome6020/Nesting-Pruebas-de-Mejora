"""One-shot: crea public.cotas_dossier en nestingpro_db y verifica columnas."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interface.cotas_dossier_service import asegurar_tabla_cotas_dossier  # noqa: E402


def main() -> int:
    asegurar_tabla_cotas_dossier()
    import psycopg2
    import config

    conn = psycopg2.connect(
        host=getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
        database=getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
        user=getattr(config, "NESTING_DB_USER", "postgres"),
        password=getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
        port=getattr(config, "NESTING_DB_PORT", "5433"),
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'cotas_dossier'
        ORDER BY ordinal_position
        """
    )
    rows = cur.fetchall()
    print("cotas_dossier columns:")
    for name, dtype, nullable, default in rows:
        print(f"  - {name}: {dtype} null={nullable} default={default}")
    expected = {
        "id",
        "cliente",
        "producto",
        "job",
        "type",
        "cantidad_spoteos",
        "nombre_archivo",
        "ruta",
        "created_at",
    }
    got = {r[0] for r in rows}
    missing = expected - got
    if missing:
        print("MISSING:", sorted(missing))
        cur.close()
        conn.close()
        return 1
    print("OK migrate cotas_dossier")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
