"""Desfusionar SWO-006: API VSM + limpieza nesting DB."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

import psycopg2
from psycopg2.extras import RealDictCursor

SWO = "SWO-006"
WOS = ("W.O. 10 X9", "W.O. 11 X9")
CENTRALIZED = "http://192.168.2.80:8003"
DB = dict(
    host="192.168.2.80",
    port=5433,
    database="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def delete_swo_api() -> tuple[bool, str]:
    url = f"{CENTRALIZED}/nesting/swo/{SWO}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, f"HTTP {resp.status}: {body[:500]}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return False, f"HTTP {e.code}: {body[:500]}"
    except Exception as e:
        return False, str(e)


def unfuse_nesting_db(cur) -> dict:
    changes: dict[str, int] = {}

    cur.execute(
        """
        UPDATE reporte_cortes
        SET super_work_order = NULL,
            estatus = 'Pendiente'
        WHERE TRIM(super_work_order) = %s
          AND TRIM(estatus) = 'Pendiente SWO'
        """,
        (SWO,),
    )
    changes["reporte_cortes_unfuse"] = cur.rowcount

    for sql, key in (
        (
            "DELETE FROM material_requerido_ldg WHERE tipo_orden = 'SWO' AND TRIM(orden_id) = %s",
            "material_requerido_ldg",
        ),
        (
            "DELETE FROM lista_largos_swo WHERE TRIM(super_work_order) = %s",
            "lista_largos_swo",
        ),
        (
            "DELETE FROM reportes_dinamicos WHERE TRIM(super_work_order) = %s",
            "reportes_dinamicos",
        ),
        (
            "DELETE FROM pqart_swo WHERE TRIM(nombre_swo) = %s",
            "pqart_swo",
        ),
        (
            "DELETE FROM erp_super_work_orders WHERE TRIM(nombre_swo) = %s",
            "erp_super_work_orders",
        ),
        (
            "DELETE FROM diccionario_swo WHERE TRIM(prefijo_carpeta) ILIKE %s",
            "diccionario_swo",
        ),
    ):
        try:
            cur.execute(sql, (SWO,))
            if cur.rowcount:
                changes[key] = cur.rowcount
        except Exception as exc:
            changes[f"{key}_error"] = str(exc)

    return changes


def verify(cur) -> None:
    cur.execute(
        """
        SELECT TRIM(work_order) wo, TRIM(estatus) estatus,
               super_work_order IS NOT NULL AS en_swo, COUNT(*) n
        FROM reporte_cortes
        WHERE TRIM(work_order) = ANY(%s)
        GROUP BY 1, 2, 3
        ORDER BY 1
        """,
        (list(WOS),),
    )
    print("reporte_cortes:")
    for row in cur.fetchall():
        print(" ", dict(row))

    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM reporte_cortes
        WHERE TRIM(super_work_order) = %s
        """,
        (SWO,),
    )
    print("filas aun en SWO:", dict(cur.fetchone() or {}))


def verify_api() -> None:
    try:
        with urllib.request.urlopen(
            f"{CENTRALIZED}/nesting/work_orders/super", timeout=15
        ) as resp:
            data = json.loads(resp.read().decode())
        swos = [
            s.get("id")
            for s in (data.get("superWorkOrders") or [])
            if str(s.get("id") or "").strip() == SWO
        ]
        print("SWO en VSM super list:", swos or "(no aparece)")
    except Exception as e:
        print("VSM verify error:", e)

    try:
        with urllib.request.urlopen(
            f"{CENTRALIZED}/nesting/work_orders/ingenieria", timeout=15
        ) as resp:
            data = json.loads(resp.read().decode())
        ids = {
            str(w.get("id") or "").strip()
            for w in (data.get("workOrders") or [])
        }
        print("WO en ingenieria pendiente fusion:", [w for w in WOS if w in ids])
    except Exception as e:
        print("ingenieria verify error:", e)


def main() -> int:
    _print_section("ANTES")
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    verify(cur)

    _print_section("DELETE VSM API")
    ok_api, msg_api = delete_swo_api()
    print(msg_api)

    _print_section("LIMPIEZA NESTING DB")
    changes = unfuse_nesting_db(cur)
    print(changes)
    conn.commit()

    _print_section("DESPUÉS (DB)")
    verify(cur)
    cur.close()
    conn.close()

    _print_section("DESPUÉS (VSM API)")
    verify_api()

    if not ok_api:
        print("\n[WARN] API DELETE falló; se aplicó limpieza directa en nesting DB.")
    print("\nListo: SWO-006 desfusionada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
