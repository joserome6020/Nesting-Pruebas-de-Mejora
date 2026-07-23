"""Elimina HR164 y HR166 de material_requerido_ldg (sin pedido de compra)."""
import sys
import psycopg2
from psycopg2.extras import RealDictCursor

CFG = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)
CODIGOS = ("HR164", "HR166")
ORDEN = "SWO-001"
TIPO = "SWO"


def main():
    do_delete = "--delete" in sys.argv
    conn = psycopg2.connect(**CFG)
    conn.autocommit = do_delete
    cur = conn.cursor(cursor_factory=RealDictCursor)

    print("=== Antes ===")
    for cod in CODIGOS:
        cur.execute(
            """
            SELECT orden_id, tipo_orden, codigo, material, largo, cantidad, costo
            FROM material_requerido_ldg
            WHERE TRIM(codigo) = %s
            ORDER BY orden_id, tipo_orden
            """,
            (cod,),
        )
        rows = cur.fetchall()
        print(f"{cod}: {len(rows)} fila(s)")
        for r in rows:
            print(" ", dict(r))

    if not do_delete:
        print("\nEjecute con --delete para borrar.")
        cur.close()
        conn.close()
        return

    cur.execute(
        """
        DELETE FROM material_requerido_ldg
        WHERE TRIM(codigo) = ANY(%s)
        RETURNING orden_id, tipo_orden, codigo
        """,
        (list(CODIGOS),),
    )
    deleted = cur.fetchall()
    print(f"\nEliminadas: {len(deleted)}")
    for r in deleted:
        print(" ", dict(r))

    print("\n=== Después ===")
    for cod in CODIGOS:
        cur.execute(
            "SELECT COUNT(*) AS n FROM material_requerido_ldg WHERE TRIM(codigo) = %s",
            (cod,),
        )
        print(f"{cod}: {cur.fetchone()['n']}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
