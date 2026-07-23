"""Regenera material_requerido_ldg desde plan para WO/SWO del job 62174."""
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, r"c:\Proyectos\ANS Pruebas de mejora")
import api_server

CFG = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)

ORDENES = [("W.O. 1 X1", "WO"), ("W.O. 2 X5", "WO"), ("SWO-001", "SWO")]


def main():
    conn = psycopg2.connect(**CFG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    for orden_id, tipo in ORDENES:
        ok, msg = api_server._asegurar_material_requerido_orden(cur, orden_id, tipo)
        print(f"{tipo} {orden_id}: ok={ok} | {msg}")
    conn.commit()
    cur.close()
    conn.close()
    print("Listo.")


if __name__ == "__main__":
    main()
