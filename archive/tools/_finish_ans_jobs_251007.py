"""Completa limpieza ANS de jobs 251007 / VANTRAN251007 tras borrado VSM."""
from __future__ import annotations

import argparse

import psycopg2
from psycopg2.extras import RealDictCursor

TARGETS = (
    {"job": "251007", "wo": "W.O. 1 X11"},
    {"job": "VANTRAN251007", "wo": "W.O. 2 X4"},
)
ANS_DB = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=15,
)


def table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS reg", (f"public.{table}",))
    return bool((cur.fetchone() or {}).get("reg"))


def columns(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        """,
        (table,),
    )
    return {r["column_name"] for r in cur.fetchall()}


def audit(cur) -> None:
    print("=== AUDIT ANS ===")
    for t in TARGETS:
        job, wo = t["job"], t["wo"]
        print(f"-- {job} / {wo}")
        checks = [
            ("reporte_cortes", "SELECT COUNT(*) n FROM reporte_cortes WHERE TRIM(work_order)=%s OR TRIM(job)=%s", (wo, job)),
            ("pqart_wo", "SELECT COUNT(*) n FROM pqart_wo WHERE TRIM(nombre_wo)=%s", (wo,)),
            ("material_requerido_ldg", "SELECT COUNT(*) n FROM material_requerido_ldg WHERE tipo_orden='WO' AND TRIM(orden_id)=%s", (wo,)),
            ("lista_largos_planes", "SELECT COUNT(*) n FROM lista_largos_planes WHERE tipo_orden='WO' AND TRIM(orden_id)=%s", (wo,)),
            ("erp_jobs", "SELECT COUNT(*) n FROM erp_jobs WHERE TRIM(job_number)=%s", (job,)),
            ("erp_work_orders", "SELECT COUNT(*) n FROM erp_work_orders WHERE TRIM(nombre_wo)=%s", (wo,)),
            ("export_stage_checkpoints", "SELECT COUNT(*) n FROM export_stage_checkpoints WHERE TRIM(scope_id)=%s OR TRIM(scope_id)=%s", (job, wo)),
        ]
        for name, sql, params in checks:
            if not table_exists(cur, name):
                continue
            cur.execute(sql, params)
            print(f"  {name}:", dict(cur.fetchone()))
        if table_exists(cur, "lista_largos_job"):
            cols = columns(cur, "lista_largos_job")
            if "job" in cols:
                cur.execute("SELECT COUNT(*) n FROM lista_largos_job WHERE TRIM(job)=%s OR TRIM(job)=%s", (job, wo))
                print("  lista_largos_job:", dict(cur.fetchone()))


def delete_one(cur, job: str, wo: str) -> dict[str, int]:
    removed: dict[str, int] = {}

    def run(label: str, sql: str, params: tuple) -> None:
        table = sql.split("FROM ", 1)[1].split(" ", 1)[0]
        if not table_exists(cur, table):
            return
        cur.execute(sql, params)
        if cur.rowcount:
            removed[label] = cur.rowcount

    for tbl in (
        "lista_largos_eventos_pieza",
        "lista_largos_eventos_sobrante",
        "lista_largos_cortes",
        "lista_largos_sobrantes",
        "lista_largos_sobrante",
        "lista_largos_sesiones",
        "lista_largos_turnos",
    ):
        try:
            run(tbl, f"DELETE FROM {tbl} WHERE tipo_orden='WO' AND TRIM(orden_id)=%s", (wo,))
        except Exception as e:
            cur.connection.rollback()
            removed[f"{tbl}_err"] = str(e)  # type: ignore[assignment]

    if table_exists(cur, "lista_largos_job"):
        cols = columns(cur, "lista_largos_job")
        clauses = []
        params: list = []
        if "job" in cols:
            clauses.append("TRIM(job)=%s")
            params.append(job)
            clauses.append("TRIM(job)=%s")
            params.append(wo)
        if "work_order" in cols:
            clauses.append("TRIM(work_order)=%s")
            params.append(wo)
        if clauses:
            cur.execute(f"SELECT id FROM lista_largos_job WHERE {' OR '.join(clauses)}", tuple(params))
            ids = [r["id"] for r in cur.fetchall()]
            if ids:
                for tbl in (
                    "lista_largos_eventos_pieza",
                    "lista_largos_eventos_sobrante",
                    "lista_largos_cortes",
                    "lista_largos_sobrantes",
                    "lista_largos_sesiones",
                    "lista_largos_turnos",
                ):
                    if table_exists(cur, tbl) and "job_id" in columns(cur, tbl):
                        cur.execute(f"DELETE FROM {tbl} WHERE job_id = ANY(%s)", (ids,))
                        if cur.rowcount:
                            removed[f"{tbl}_by_job_id"] = cur.rowcount
                cur.execute("DELETE FROM lista_largos_job WHERE id = ANY(%s)", (ids,))
                if cur.rowcount:
                    removed["lista_largos_job"] = cur.rowcount

    run("lista_largos_planes", "DELETE FROM lista_largos_planes WHERE tipo_orden='WO' AND TRIM(orden_id)=%s", (wo,))
    run("material_requerido_ldg", "DELETE FROM material_requerido_ldg WHERE tipo_orden='WO' AND TRIM(orden_id)=%s", (wo,))
    run("reporte_cortes", "DELETE FROM reporte_cortes WHERE TRIM(work_order)=%s OR TRIM(job)=%s", (wo, job))
    run("pqart_wo", "DELETE FROM pqart_wo WHERE TRIM(nombre_wo)=%s", (wo,))
    run("export_stage_checkpoints", "DELETE FROM export_stage_checkpoints WHERE TRIM(scope_id)=%s OR TRIM(scope_id)=%s", (job, wo))
    run("erp_work_orders", "DELETE FROM erp_work_orders WHERE TRIM(nombre_wo)=%s", (wo,))
    run("erp_jobs", "DELETE FROM erp_jobs WHERE TRIM(job_number)=%s", (job,))

    # tracking / costos opcionales
    for tbl, col in (
        ("erp_piezas_tracking", "wo_name"),
        ("erp_placas_tracking", "wo_name"),
    ):
        if table_exists(cur, tbl) and col in columns(cur, tbl):
            cur.execute(f"DELETE FROM {tbl} WHERE TRIM({col})=%s", (wo,))
            if cur.rowcount:
                removed[tbl] = cur.rowcount
    if table_exists(cur, "costos_prorrateo"):
        cols = columns(cur, "costos_prorrateo")
        clauses = []
        params = []
        for col in ("work_order", "job", "orden_id"):
            if col in cols:
                clauses.append(f"TRIM(COALESCE({col}::text,''))=%s")
                params.append(wo if col != "job" else job)
                if col != "job":
                    clauses.append(f"TRIM(COALESCE({col}::text,''))=%s")
                    params.append(job)
        if clauses:
            cur.execute(f"DELETE FROM costos_prorrateo WHERE {' OR '.join(clauses)}", tuple(params))
            if cur.rowcount:
                removed["costos_prorrateo"] = cur.rowcount
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    conn = psycopg2.connect(**ANS_DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    audit(cur)
    if not args.apply:
        print("Dry-run. --apply --confirm DELETE-ANS-251007")
        return 0
    if args.confirm != "DELETE-ANS-251007":
        print("Confirmación incorrecta")
        return 2
    print("\n=== DELETE ===")
    for t in TARGETS:
        removed = delete_one(cur, t["job"], t["wo"])
        print(t["job"], removed)
    conn.commit()
    print("\n=== VERIFY ===")
    audit(cur)
    cur.close()
    conn.close()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
