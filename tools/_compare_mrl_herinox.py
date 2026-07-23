"""Compara largo MRL vs catálogo Herinox por código."""
import sys

sys.path.insert(0, r"c:\Proyectos\ANS Pruebas de mejora")

import psycopg2
from psycopg2.extras import RealDictCursor

from catalogo_largos import _cargar_placas_largos_desde_herinox, extraer_codigo_herinox_combo

CFG = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)

CODES = [
    "ANG022", "ANG037", "CAN019", "HR164", "HR166",
    "PTR016", "PTR030", "RED027", "SLC042", "SLC051", "TYA001",
]


def main():
    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
    por_codigo = {}
    for p in catalogo:
        c = str(p.get("codigo") or "").strip().upper()
        if c and c not in por_codigo:
            por_codigo[c] = p

    conn = psycopg2.connect(**CFG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT codigo, material, largo, cantidad
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = 'SWO-001' AND tipo_orden = 'SWO'
        ORDER BY codigo
        """
    )
    mrl = {str(r["codigo"]).upper(): r for r in cur.fetchall()}

    import api_server

    plan, _ = api_server._ll_obtener_o_generar_plan(cur, "SWO-001", "SWO", reservar=False)
    plan_largo = {}
    for mat, barras in (plan.get("data") or {}).items():
        cod = extraer_codigo_herinox_combo(mat)
        for b in barras or []:
            if str(b.get("source") or "").upper() == "REMANENTE":
                continue
            ls = float(b.get("largo_stock") or 0)
            plan_largo.setdefault(cod, []).append(ls)

    print(f"{'CODIGO':<8} {'HERINOX':>8} {'MRL L':>8} {'MRL Q':>6} {'PLAN in':>8} {'CAT Q':>6} {'OK':>4}")
    print("-" * 58)
    import math
    from lista_largos_material_requerido import _cantidad_barras_catalogo

    for cod in CODES:
        placa = por_codigo.get(cod)
        ft = float(placa.get("largo_ft") or 0) if placa else 0
        hin = round(ft * 12, 2) if ft else 0
        pins = plan_largo.get(cod, [0])
        total_plan = sum(pins)
        cat_qty = _cantidad_barras_catalogo(total_plan, hin) if hin else 1
        row = mrl.get(cod, {})
        min_ = float(row.get("largo") or 0)
        mqty = int(row.get("cantidad") or 0)
        ok = "OK" if hin and abs(hin - min_) < 1 and mqty == cat_qty else "NO"
        print(
            f"{cod:<8} {ft:>6}ft {min_:>8.0f} {mqty:>6} {total_plan:>8.0f} {cat_qty:>6} {ok:>4}"
        )

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
