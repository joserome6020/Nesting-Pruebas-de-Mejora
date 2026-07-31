"""Borra jobs VSM 251007 / VANTRAN251007 y limpia ANS relacionado."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import psycopg2
from psycopg2.extras import RealDictCursor

CENTRALIZED = "http://192.168.2.80:8003"
TARGETS = (
    {"job": "251007", "job_id": 1, "wo": "W.O. 1 X11"},
    {"job": "VANTRAN251007", "job_id": 3, "wo": "W.O. 2 X4"},
)

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


def http_json(method: str, path: str):
    req = urllib.request.Request(f"{CENTRALIZED}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {"raw": raw}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


def table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS reg", (f"public.{table}",))
    return bool((cur.fetchone() or {}).get("reg"))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def audit_vsm() -> None:
    section("VSM API / DB")
    for t in TARGETS:
        st, body = http_json("GET", f"/jobs/by-number/{urllib.parse.quote(t['job'])}")
        print(f"API {t['job']}: HTTP {st} { {k: body.get(k) for k in ('id','job_number','status')} if isinstance(body, dict) else body }")
    with psycopg2.connect(**VSM_DB) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for t in TARGETS:
                cur.execute(
                    "SELECT id, job_number, status FROM jobs WHERE id=%s OR TRIM(job_number)=%s",
                    (t["job_id"], t["job"]),
                )
                print(f"DB {t['job']}:", [dict(r) for r in cur.fetchall()])


def audit_ans(cur) -> None:
    section("ANS")
    for t in TARGETS:
        job, wo = t["job"], t["wo"]
        checks = [
            ("reporte_cortes", "SELECT COUNT(*) n FROM reporte_cortes WHERE TRIM(work_order)=%s OR TRIM(job)=%s", (wo, job)),
            ("pqart_wo", "SELECT COUNT(*) n FROM pqart_wo WHERE TRIM(nombre_wo)=%s", (wo,)),
            ("material_requerido_ldg", "SELECT COUNT(*) n FROM material_requerido_ldg WHERE tipo_orden='WO' AND TRIM(orden_id)=%s", (wo,)),
            ("lista_largos_planes", "SELECT COUNT(*) n FROM lista_largos_planes WHERE tipo_orden='WO' AND TRIM(orden_id)=%s", (wo,)),
            ("lista_largos_job", "SELECT COUNT(*) n FROM lista_largos_job WHERE TRIM(work_order)=%s OR TRIM(job)=%s", (wo, job)),
            ("erp_jobs", "SELECT COUNT(*) n FROM erp_jobs WHERE TRIM(job_number)=%s", (job,)),
            ("erp_work_orders", "SELECT COUNT(*) n FROM erp_work_orders WHERE TRIM(nombre_wo)=%s", (wo,)),
            ("export_stage_checkpoints", "SELECT COUNT(*) n FROM export_stage_checkpoints WHERE TRIM(scope_id)=%s OR TRIM(scope_id)=%s", (job, wo)),
        ]
        print(f"-- {job} / {wo}")
        for name, sql, params in checks:
            try:
                if not table_exists(cur, name):
                    print(f"  {name}: (no existe)")
                    continue
                cur.execute(sql, params)
                print(f"  {name}:", dict(cur.fetchone()))
            except Exception as e:
                cur.connection.rollback()
                print(f"  {name}: ERR {e}")


def purge_vsm_db(job_id: int, job: str) -> dict[str, int]:
    deleted: dict[str, int] = {}
    with psycopg2.connect(**VSM_DB) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT job_number FROM jobs WHERE id=%s FOR UPDATE", (job_id,))
            row = cur.fetchone()
            if not row or str(row.get("job_number") or "").strip() != job:
                raise RuntimeError(f"VSM id {job_id} no es {job}: {row}")
            statements = (
                (
                    "dossier_files",
                    """
                    DELETE FROM dossier_files
                    WHERE dossier_id IN (SELECT id FROM dossiers WHERE job_id=%s)
                    """,
                ),
                (
                    "defect_images",
                    """
                    DELETE FROM defect_images
                    WHERE defect_record_id IN (SELECT id FROM defect_records WHERE job_id=%s)
                    """,
                ),
                ("file_annotations", "DELETE FROM file_annotations WHERE job_id=%s"),
                ("file_reviews", "DELETE FROM file_reviews WHERE job_id=%s"),
                ("defect_records", "DELETE FROM defect_records WHERE job_id=%s"),
                ("dossiers", "DELETE FROM dossiers WHERE job_id=%s"),
                ("job_history", "DELETE FROM job_history WHERE job_id=%s"),
                ("work_orders", "DELETE FROM work_orders WHERE job_id=%s"),
                ("jobs", "DELETE FROM jobs WHERE id=%s"),
            )
            for label, sql in statements:
                table = label
                if not table_exists(cur, table):
                    continue
                cur.execute(sql, (job_id,))
                if cur.rowcount:
                    deleted[label] = cur.rowcount
            if deleted.get("jobs") != 1:
                raise RuntimeError(f"No se borró job {job}: {deleted}")
        conn.commit()
    return deleted


def delete_vsm_job(job_id: int, job: str) -> dict:
    st, body = http_json("DELETE", f"/jobs/{job_id}")
    if st == 204 or (200 <= st < 300):
        return {"mode": "api", "http": st, "body": body}
    if st in (404, 0):
        # ya no está vía API; limpiar DB por si acaso
        pass
    # fallback DB (también si API 500 por FKs)
    removed = purge_vsm_db(job_id, job)
    return {"mode": "direct-db", "http": st, "body": body, "removed": removed}


def delete_ans_for(cur, job: str, wo: str) -> dict[str, int]:
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
        except Exception:
            cur.connection.rollback()

    try:
        run(
            "lista_largos_remanentes",
            "DELETE FROM lista_largos_remanentes WHERE TRIM(reservado_para_orden_id)=%s OR TRIM(fuente_orden_id)=%s",
            (wo, wo),
        )
    except Exception:
        cur.connection.rollback()

    # lista_largos_job + hijos por job_id
    if table_exists(cur, "lista_largos_job"):
        cur.execute(
            "SELECT id FROM lista_largos_job WHERE TRIM(work_order)=%s OR TRIM(job)=%s OR TRIM(job)=%s",
            (wo, job, wo),
        )
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
                if table_exists(cur, tbl):
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
    try:
        run("lista_largos_job_by_job", "DELETE FROM lista_largos_job WHERE TRIM(job)=%s", (job,))
    except Exception:
        cur.connection.rollback()
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    audit_vsm()
    ans = psycopg2.connect(**ANS_DB)
    cur = ans.cursor(cursor_factory=RealDictCursor)
    audit_ans(cur)

    if not args.apply:
        print("\nDry-run. Aplicar: --apply --confirm DELETE-JOBS-251007")
        cur.close()
        ans.close()
        return 0
    if args.confirm != "DELETE-JOBS-251007":
        print("Confirmación incorrecta.")
        return 2

    section("DELETE VSM JOBS")
    for t in TARGETS:
        # confirmar id actual
        st, body = http_json("GET", f"/jobs/by-number/{urllib.parse.quote(t['job'])}")
        job_id = int((body or {}).get("id") or t["job_id"]) if st == 200 else t["job_id"]
        try:
            result = delete_vsm_job(job_id, t["job"])
            print(t["job"], result)
        except Exception as e:
            # si ya no existe
            st2, _ = http_json("GET", f"/jobs/by-number/{urllib.parse.quote(t['job'])}")
            if st2 == 404:
                print(t["job"], "ya ausente")
            else:
                raise

    section("DELETE ANS")
    for t in TARGETS:
        removed = delete_ans_for(cur, t["job"], t["wo"])
        print(t["job"], removed)
    ans.commit()

    section("VERIFY")
    audit_vsm()
    audit_ans(cur)
    cur.close()
    ans.close()
    print("\nListo. Carpetas fuente en servidor NO se tocaron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
