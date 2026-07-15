"""Diagnóstico: qué ítem de SWO no encuentra DXF en red."""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def obtener_ruta_real_job(ruta_raiz: str, nombre_job: str) -> str | None:
    if not os.path.exists(ruta_raiz):
        return None
    for producto in os.listdir(ruta_raiz):
        ruta_prod = os.path.join(ruta_raiz, producto)
        if not os.path.isdir(ruta_prod):
            continue
        for cliente in os.listdir(ruta_prod):
            ruta_cli = os.path.join(ruta_prod, cliente)
            if not os.path.isdir(ruta_cli):
                continue
            ruta_job = os.path.join(ruta_cli, nombre_job)
            if os.path.isdir(ruta_job):
                return ruta_job
    return None


def listar_dxfs(root: str) -> list[str]:
    out: list[str] = []
    if not os.path.isdir(root):
        return out
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith(".dxf"):
                out.append(os.path.join(dp, f))
    return out


def buscar_dxf(ruta_autodxf: str, item: str) -> str:
    item_limpio = str(item or "").strip().lower()
    if not item_limpio:
        return ""
    candidatos: list[str] = []
    rp = os.path.join(ruta_autodxf, "Processed Files")
    if os.path.isdir(rp):
        candidatos.extend(listar_dxfs(rp))
    if os.path.isdir(ruta_autodxf):
        candidatos.extend(listar_dxfs(ruta_autodxf))
    for ruta in candidatos:
        f_lower = os.path.basename(ruta).lower()
        if (
            f_lower == f"{item_limpio}.dxf"
            or f_lower.startswith(f"{item_limpio},")
            or f_lower.startswith(f"{item_limpio} ")
            or f_lower.startswith(f"{item_limpio}_")
        ):
            return ruta
    return ""


def main() -> None:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    swo = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    cred = {
        "host": "192.168.2.80",
        "database": "nestingpro_db",
        "user": "postgres",
        "password": "nesting123",
        "port": "5433",
    }
    con = psycopg2.connect(**cred)
    cur = con.cursor(cursor_factory=RealDictCursor)

    if not swo:
        cur.execute(
            """
            SELECT DISTINCT super_work_order
            FROM reporte_cortes
            WHERE estatus = 'Pendiente SWO' AND super_work_order IS NOT NULL
            ORDER BY super_work_order
            """
        )
        pend = [r["super_work_order"] for r in cur.fetchall()]
        print("=== SWO pendientes en BD ===")
        for p in pend:
            print(p)
        if not pend:
            print("(ninguna)")
            cur.close()
            con.close()
            return
        swo = pend[0]
        print(f"\nDiagnosticando primera pendiente: {swo}\n")

    cur.execute(
        """
        SELECT DISTINCT super_work_order, estatus, COUNT(*) AS n
        FROM reporte_cortes
        WHERE super_work_order ILIKE %s
        GROUP BY super_work_order, estatus
        ORDER BY super_work_order, estatus
        """,
        (f"%{swo}%",),
    )
    print("=== SWO en BD (cualquier estatus) ===")
    for r in cur.fetchall():
        print(dict(r))

    cur.execute(
        """
        SELECT job, work_order, calibre, item, COUNT(*) AS qty
        FROM reporte_cortes
        WHERE super_work_order = %s AND estatus = 'Pendiente SWO'
        GROUP BY job, work_order, calibre, item
        ORDER BY job, item
        """,
        (swo,),
    )
    rows = cur.fetchall()
    if not rows:
        # Probar variantes comunes
        for alt in (swo.upper(), swo.lower(), f"SWO-{swo.replace('SWO','').strip()}"):
            cur.execute(
                """
                SELECT job, work_order, calibre, item, COUNT(*) AS qty
                FROM reporte_cortes
                WHERE super_work_order = %s AND estatus = 'Pendiente SWO'
                GROUP BY job, work_order, calibre, item
                """,
                (alt,),
            )
            alt_rows = cur.fetchall()
            if alt_rows:
                swo = alt
                rows = alt_rows
                print(f"\nUsando SWO id: {swo}")
                break

    print(f"\n=== Ítems Pendiente SWO ({swo}): {len(rows)} ===")
    ok = fail = 0
    for row in rows:
        job, item = row["job"], row["item"]
        base = obtener_ruta_real_job(config.RUTA_SERVIDOR_RAIZ, job)
        if not base:
            fail += 1
            print(f"FAIL | item={item} | job={job} | WO={row['work_order']} | qty={row['qty']}")
            print(f"       motivo: carpeta job no encontrada bajo {config.RUTA_SERVIDOR_RAIZ}")
            continue
        autodxf = os.path.join(base, "MODEL CORE FILES", "AutoDXF")
        ruta = buscar_dxf(autodxf, item)
        if ruta:
            ok += 1
        else:
            fail += 1
            print(f"FAIL | item={item} | job={job} | WO={row['work_order']} | qty={row['qty']}")
            print(f"       buscado en: {autodxf}")
            print(f"       esperado: {item}.dxf (o {item},*.dxf)")

    print(f"\nResumen: OK={ok} FAIL={fail} (mensaje UI = Faltaron {fail} archivos en la red)")
    for probe in ("62135-1251-P03", "SP-741"):
        cur.execute(
            """
            SELECT item, job, work_order, calibre, COUNT(*) AS qty
            FROM reporte_cortes
            WHERE super_work_order = %s AND estatus = 'Pendiente SWO' AND item = %s
            GROUP BY item, job, work_order, calibre
            """,
            (swo, probe),
        )
        hit = cur.fetchall()
        print(f"  BD item {probe}: {len(hit)} fila(s)" + (f" qty={hit[0]['qty']}" if hit else " (no en SWO)"))
    cur.close()
    con.close()


if __name__ == "__main__":
    main()
