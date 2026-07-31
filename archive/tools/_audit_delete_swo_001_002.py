"""Inventario + borrado controlado de SWO-001 / SWO-002 en VSM + ANS."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import psycopg2
from psycopg2.extras import RealDictCursor

CENTRALIZED = "http://192.168.2.80:8003"
SWOS = ("SWO-001", "SWO-002")

ANS_DB = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=15,
)

VSM_DB = dict(
    host=os.getenv("VSM_DB_HOST", "192.168.2.80"),
    port=os.getenv("VSM_DB_PORT", "5437"),
    dbname=os.getenv("VSM_DB_NAME", "foldertree"),
    user=os.getenv("VSM_DB_USER", "user"),
    password=os.getenv("VSM_DB_PASSWORD", "password"),
    connect_timeout=15,
)


def http_json(method: str, path: str, timeout: int = 30):
    req = urllib.request.Request(f"{CENTRALIZED}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            body = json.loads(raw) if raw.strip() else {}
            return resp.status, body
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {"raw": raw}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def audit_vsm_api() -> dict:
    section("VSM API")
    out = {}
    for swo in SWOS:
        status, body = http_json("GET", f"/nesting/swo/{urllib.parse.quote(swo)}/jobs")
        print(f"{swo}/jobs -> HTTP {status}: {body}")
        out[swo] = {"jobs_status": status, "jobs": body}
        for job in (body.get("jobs") or []) if isinstance(body, dict) else []:
            js, jb = http_json("GET", f"/jobs/by-number/{urllib.parse.quote(str(job))}")
            print(f"  job {job} -> HTTP {js}: {json.dumps(jb, ensure_ascii=False)[:500]}")
            out[swo].setdefault("job_details", {})[str(job)] = {"status": js, "body": jb}
    return out


def table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS reg", (f"public.{table}",))
    row = cur.fetchone() or {}
    return bool(row.get("reg"))


def audit_ans(cur) -> dict:
    section("ANS DB inventario")
    counts = {}
    checks = [
        ("erp_super_work_orders", "SELECT * FROM erp_super_work_orders WHERE TRIM(nombre_swo)=ANY(%s)", (list(SWOS),)),
        ("pqart_swo", "SELECT TRIM(nombre_swo) AS k, COUNT(*) n FROM pqart_swo WHERE TRIM(nombre_swo)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("lista_largos_swo", "SELECT TRIM(super_work_order) AS k, COUNT(*) n FROM lista_largos_swo WHERE TRIM(super_work_order)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("material_requerido_ldg", "SELECT TRIM(orden_id) AS k, COUNT(*) n FROM material_requerido_ldg WHERE tipo_orden='SWO' AND TRIM(orden_id)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("lista_largos_planes", "SELECT TRIM(orden_id) AS k, COUNT(*) n FROM lista_largos_planes WHERE tipo_orden='SWO' AND TRIM(orden_id)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("lista_largos_sesiones", "SELECT TRIM(orden_id) AS k, COUNT(*) n FROM lista_largos_sesiones WHERE tipo_orden='SWO' AND TRIM(orden_id)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("lista_largos_cortes", "SELECT TRIM(orden_id) AS k, COUNT(*) n FROM lista_largos_cortes WHERE tipo_orden='SWO' AND TRIM(orden_id)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("lista_largos_eventos_pieza", "SELECT TRIM(orden_id) AS k, COUNT(*) n FROM lista_largos_eventos_pieza WHERE tipo_orden='SWO' AND TRIM(orden_id)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("lista_largos_eventos_sobrante", "SELECT TRIM(orden_id) AS k, COUNT(*) n FROM lista_largos_eventos_sobrante WHERE tipo_orden='SWO' AND TRIM(orden_id)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("lista_largos_sobrantes", "SELECT TRIM(orden_id) AS k, COUNT(*) n FROM lista_largos_sobrantes WHERE tipo_orden='SWO' AND TRIM(orden_id)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("lista_largos_remanentes", "SELECT COUNT(*) n FROM lista_largos_remanentes WHERE TRIM(reservado_para_orden_id)=ANY(%s) OR TRIM(fuente_orden_id)=ANY(%s)", (list(SWOS), list(SWOS))),
        ("reporte_cortes", "SELECT TRIM(super_work_order) AS k, COUNT(*) n, array_agg(DISTINCT TRIM(work_order)) wos FROM reporte_cortes WHERE TRIM(super_work_order)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("reportes_dinamicos", "SELECT TRIM(super_work_order) AS k, COUNT(*) n FROM reportes_dinamicos WHERE TRIM(super_work_order)=ANY(%s) GROUP BY 1", (list(SWOS),)),
        ("diccionario_swo", "SELECT * FROM diccionario_swo WHERE TRIM(prefijo_carpeta)=ANY(%s) OR TRIM(prefijo_carpeta) ILIKE ANY(ARRAY['%%SWO-001%%','%%SWO-002%%'])", (list(SWOS),)),
        ("export_stage_checkpoints", "SELECT scope_id, stage, COUNT(*) n FROM export_stage_checkpoints WHERE TRIM(scope_id)=ANY(%s) GROUP BY 1,2", (list(SWOS),)),
        ("costos_prorrateo", None, None),  # audit dinámico abajo
    ]
    for name, sql, params in checks:
        try:
            if not table_exists(cur, name):
                print(f"{name}: (tabla no existe)")
                continue
            if name == "costos_prorrateo":
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='costos_prorrateo'
                    """
                )
                cols = {r["column_name"] for r in cur.fetchall()}
                clauses = []
                params_dyn: list = []
                for col in ("super_work_order", "work_order", "job", "orden_id"):
                    if col in cols:
                        clauses.append(f"TRIM(COALESCE({col}::text,''))=ANY(%s)")
                        params_dyn.append(list(SWOS))
                if not clauses:
                    print("costos_prorrateo: (sin columnas útiles)")
                    continue
                cur.execute(
                    f"SELECT COUNT(*) n FROM costos_prorrateo WHERE {' OR '.join(clauses)}",
                    tuple(params_dyn),
                )
                rows = [dict(r) for r in cur.fetchall()]
                counts[name] = rows
                print(f"{name}: {rows}")
                continue
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            counts[name] = rows
            print(f"{name}: {rows if rows else '(vacío)'}")
        except Exception as e:
            cur.connection.rollback()
            print(f"{name}: ERR {e}")
            counts[name] = {"error": str(e)}
    return counts


def audit_vsm_db(cur) -> dict:
    section("VSM DB schema / refs")
    cur.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema='public'
          AND (
            column_name ILIKE '%swo%'
            OR column_name ILIKE '%super_work%'
            OR column_name = 'job_number'
            OR column_name = 'name'
          )
        ORDER BY 1,2
        """
    )
    cols = [dict(r) for r in cur.fetchall()]
    print("columnas relevantes:", len(cols))
    for r in cols:
        print(" ", r)

    out = {"columns": cols, "hits": {}}
    # jobs by number
    for job in ("251007", "VANTRAN251007"):
        try:
            cur.execute(
                "SELECT id, job_number, status, local_path FROM jobs WHERE TRIM(job_number)=%s",
                (job,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            print(f"jobs[{job}]: {rows}")
            out["hits"][f"jobs:{job}"] = rows
        except Exception as e:
            cur.connection.rollback()
            print(f"jobs[{job}]: ERR {e}")

    # tables with swo-like columns
    swo_tables = sorted({c["table_name"] for c in cols if "swo" in c["column_name"].lower() or "super_work" in c["column_name"].lower()})
    for table in swo_tables:
        try:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                """,
                (table,),
            )
            tcols = [r["column_name"] for r in cur.fetchall()]
            swo_cols = [c for c in tcols if "swo" in c.lower() or "super_work" in c.lower()]
            for col in swo_cols:
                cur.execute(
                    f'SELECT COUNT(*) AS n FROM "{table}" WHERE TRIM({col}::text)=ANY(%s)',
                    (list(SWOS),),
                )
                n = int((cur.fetchone() or {}).get("n") or 0)
                print(f"{table}.{col} matches: {n}")
                out["hits"][f"{table}.{col}"] = n
                if n:
                    cur.execute(
                        f'SELECT * FROM "{table}" WHERE TRIM({col}::text)=ANY(%s) LIMIT 10',
                        (list(SWOS),),
                    )
                    print("  sample:", [dict(r) for r in cur.fetchall()])
        except Exception as e:
            cur.connection.rollback()
            print(f"{table}: ERR {e}")
    return out


ANS_DELETE_PLAN = [
    # child-first largos
    ("lista_largos_eventos_pieza", "DELETE FROM lista_largos_eventos_pieza WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_eventos_sobrante", "DELETE FROM lista_largos_eventos_sobrante WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_cortes", "DELETE FROM lista_largos_cortes WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_sobrantes", "DELETE FROM lista_largos_sobrantes WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_sobrante", "DELETE FROM lista_largos_sobrante WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_sesiones", "DELETE FROM lista_largos_sesiones WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_turnos", "DELETE FROM lista_largos_turnos WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_remanentes_reservado", "DELETE FROM lista_largos_remanentes WHERE TRIM(reservado_para_orden_id)=%s"),
    ("lista_largos_remanentes_fuente", "DELETE FROM lista_largos_remanentes WHERE TRIM(fuente_orden_id)=%s"),
    ("lista_largos_planes", "DELETE FROM lista_largos_planes WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_plan", "DELETE FROM lista_largos_plan WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("material_requerido_ldg", "DELETE FROM material_requerido_ldg WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s"),
    ("lista_largos_swo", "DELETE FROM lista_largos_swo WHERE TRIM(super_work_order)=%s"),
    ("pqart_swo", "DELETE FROM pqart_swo WHERE TRIM(nombre_swo)=%s"),
    ("reportes_dinamicos", "DELETE FROM reportes_dinamicos WHERE TRIM(super_work_order)=%s"),
    ("reporte_cortes", "DELETE FROM reporte_cortes WHERE TRIM(super_work_order)=%s"),
    ("export_stage_checkpoints", "DELETE FROM export_stage_checkpoints WHERE TRIM(scope_id)=%s"),
    ("erp_super_work_orders", "DELETE FROM erp_super_work_orders WHERE TRIM(nombre_swo)=%s"),
    ("diccionario_swo", "DELETE FROM diccionario_swo WHERE TRIM(prefijo_carpeta)=%s OR TRIM(prefijo_carpeta) ILIKE %s"),
]


def delete_ans(cur, swo: str) -> dict[str, int]:
    removed: dict[str, int] = {}
    for label, sql in ANS_DELETE_PLAN:
        table = sql.split("FROM ", 1)[1].split(" ", 1)[0]
        if not table_exists(cur, table):
            continue
        try:
            if "diccionario_swo" in label:
                cur.execute(sql, (swo, f"%{swo}%"))
            else:
                cur.execute(sql, (swo,))
            if cur.rowcount:
                removed[label] = cur.rowcount
        except Exception as e:
            raise RuntimeError(f"{label}: {e}") from e

    # costos_prorrateo: columnas variables
    if table_exists(cur, "costos_prorrateo"):
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='costos_prorrateo'
            """
        )
        cols = {r["column_name"] for r in cur.fetchall()}
        clauses = []
        params: list = []
        for col in ("super_work_order", "work_order", "job", "orden_id"):
            if col in cols:
                clauses.append(f"TRIM(COALESCE({col}::text,''))=%s")
                params.append(swo)
        if clauses:
            cur.execute(
                f"DELETE FROM costos_prorrateo WHERE {' OR '.join(clauses)}",
                tuple(params),
            )
            if cur.rowcount:
                removed["costos_prorrateo"] = cur.rowcount
    return removed


def delete_vsm_api(swo: str) -> tuple[int, dict]:
    return http_json("DELETE", f"/nesting/swo/{urllib.parse.quote(swo)}")


def delete_vsm_db_refs(cur) -> dict[str, int]:
    """Limpia historial SWO en foldertree. No borra jobs ni carpetas fuente."""
    removed: dict[str, int] = {}
    cur.execute("DELETE FROM job_history WHERE TRIM(swo_id)=ANY(%s)", (list(SWOS),))
    if cur.rowcount:
        removed["job_history"] = cur.rowcount
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    audit_vsm_api()

    section("ANS connect")
    ans = psycopg2.connect(**ANS_DB)
    ans_cur = ans.cursor(cursor_factory=RealDictCursor)
    audit_ans(ans_cur)

    section("VSM DB connect")
    try:
        vsm = psycopg2.connect(**VSM_DB)
        vsm_cur = vsm.cursor(cursor_factory=RealDictCursor)
        audit_vsm_db(vsm_cur)
    except Exception as e:
        print("VSM DB no disponible:", e)
        vsm = None
        vsm_cur = None

    if not args.apply:
        print("\nDry-run only. Para aplicar: --apply --confirm DELETE-SWO-001-002")
        ans_cur.close()
        ans.close()
        if vsm:
            vsm_cur.close()
            vsm.close()
        return 0

    if args.confirm != "DELETE-SWO-001-002":
        print("Confirmación incorrecta. Abortado.")
        return 2

    section("APPLY VSM API DELETE")
    for swo in SWOS:
        status, body = delete_vsm_api(swo)
        print(f"DELETE {swo} -> HTTP {status}: {body}")
        if not (200 <= status < 300 or status == 404):
            print(f"[WARN] API DELETE falló para {swo}; se continúa con limpieza DB.")

    section("APPLY ANS DELETE")
    all_removed = {}
    for swo in SWOS:
        removed = delete_ans(ans_cur, swo)
        all_removed[swo] = removed
        print(swo, removed)
    ans.commit()

    if vsm_cur:
        section("APPLY VSM DB job_history")
        vsm_removed = delete_vsm_db_refs(vsm_cur)
        print(vsm_removed)
        vsm.commit()

    section("VERIFY ANS")
    audit_ans(ans_cur)

    section("VERIFY VSM API")
    audit_vsm_api()

    if vsm_cur:
        section("VERIFY VSM DB")
        audit_vsm_db(vsm_cur)
        vsm_cur.close()
        vsm.close()

    ans_cur.close()
    ans.close()
    print("\nListo. Jobs VSM 251007 / VANTRAN251007 y carpetas fuente NO se tocaron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
