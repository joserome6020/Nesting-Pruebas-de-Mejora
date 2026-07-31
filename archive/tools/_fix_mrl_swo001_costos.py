"""Inspecciona y repara costo MRL ANG035 / SWO-001 para ContPAQ."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IFACE = os.path.join(ROOT, "interface")
for p in (ROOT, IFACE):
    if p not in sys.path:
        sys.path.insert(0, p)

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


def main():
    do_fix = "--fix" in sys.argv
    conn = psycopg2.connect(**CFG)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    print("=== MRL SWO-001 ===")
    cur.execute(
        """
        SELECT id, orden_id, tipo_orden, codigo, material, largo, cantidad, costo
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        ORDER BY id
        """,
        ("SWO-001", "SWO"),
    )
    rows = cur.fetchall()
    for r in rows:
        print(dict(r))

    bad = [
        r
        for r in rows
        if r.get("costo") is None or float(r.get("costo") or 0) <= 0
    ]
    print(f"\nSin costo valido: {len(bad)}")
    for r in bad:
        print(" ", dict(r))

    print("\n=== WO referencia ANG* ===")
    cur.execute(
        """
        SELECT id, orden_id, codigo, largo, cantidad, costo
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND UPPER(TRIM(codigo)) LIKE 'ANG%%'
        ORDER BY codigo
        """,
        ("W.O. 1 X1",),
    )
    for r in cur.fetchall():
        print(dict(r))

    if not do_fix:
        print("\nDry-run. Use --fix para enriquecer costos Herinox en SWO-001.")
        cur.close()
        conn.close()
        return

    from lista_largos_material_requerido import enriquecer_pedido_herinox_cursor

    n = enriquecer_pedido_herinox_cursor(cur, "SWO-001", "SWO", forzar_costo=True)
    print(f"\nenriquecer filas tocadas: {n}")
    conn.commit()

    cur.execute(
        """
        SELECT id, codigo, largo, cantidad, costo
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        ORDER BY id
        """,
        ("SWO-001", "SWO"),
    )
    print("=== MRL despues ===")
    for r in cur.fetchall():
        print(dict(r))

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
