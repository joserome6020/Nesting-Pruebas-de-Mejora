"""Compara demanda CSV vs BD vs plan para diagnosticar diferencia de barras."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import psycopg2
from psycopg2.extras import RealDictCursor

import config
from api_server import (
    _expandir_lista_para_wo,
    _extraer_factor_wo,
    _ll_generar_plan_desde_payload,
    _obtener_lista_base_por_job,
)
from lista_largos_material_requerido import agregar_filas_desde_plan

JOB = "1000 kva de prueba"
FACTOR = 1
WO_LABEL = f"LOTE X{FACTOR}"


def db_cfg():
    return {
        "host": getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
        "database": getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
        "user": getattr(config, "NESTING_DB_USER", "postgres"),
        "password": getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
        "port": getattr(config, "NESTING_DB_PORT", "5433"),
    }


def piezas_desde_rows(rows):
    from api_server import _ll_expandir_rows_a_piezas

    return _ll_expandir_rows_a_piezas(rows)


def stats_plan(plan):
    data = plan.get("data") or {}
    stock = rem = 0
    piezas = 0
    por_mat = {}
    for mat, barras in data.items():
        por_mat[mat] = {"stock": 0, "rem": 0, "piezas": 0}
        for b in barras:
            src = str(b.get("source") or "STOCK").upper()
            n = len(b.get("cortes") or [])
            piezas += n
            if src == "REMANENTE":
                rem += 1
                por_mat[mat]["rem"] += 1
            else:
                stock += 1
                por_mat[mat]["stock"] += 1
            por_mat[mat]["piezas"] += n
    return {
        "total_barras": stock + rem,
        "stock": stock,
        "remanente": rem,
        "piezas": piezas,
        "por_material": por_mat,
    }


def generar_plan(cursor, rows, orden_id):
    payload = {
        "tipo": "wo",
        "identificador": orden_id,
        "work_order": WO_LABEL,
        "jobs": [JOB],
        "factor_wo": _extraer_factor_wo(WO_LABEL),
        "rows": rows,
    }
    plan, _ = _ll_generar_plan_desde_payload(cursor, orden_id, "WO", payload)
    return plan


def main():
    conexion = psycopg2.connect(**db_cfg())
    cursor = conexion.cursor(cursor_factory=RealDictCursor)

    base_bd = _obtener_lista_base_por_job(cursor, JOB)
    rows_bd = _expandir_lista_para_wo(cursor, JOB, WO_LABEL)

    print(f"=== JOB: {JOB!r} · {WO_LABEL} ===\n")
    print(f"Filas lista_largos_job (BD): {len(base_bd)}")
    print(f"Filas expandidas BD: {len(rows_bd)}")
    print(f"Piezas expandidas BD: {len(piezas_desde_rows(rows_bd))}")

    # CSV desde ruta historial si existe
    rows_csv = []
    hist_path = os.path.join(_ROOT, "historial_jobs.json")
    ruta_job = ""
    if os.path.isfile(hist_path):
        import json

        try:
            jobs = json.loads(open(hist_path, encoding="utf-8").read())
            for j in jobs:
                if "1000 kva" in str(j).lower():
                    ruta_job = str(j)
                    break
        except Exception:
            pass

    if ruta_job:
        from modules.lista_largos_importer import _leer_csv_lista_largos, _resolver_csv_lista_largos
        from pathlib import Path

        autodxf = Path(ruta_job)
        csv_path = _resolver_csv_lista_largos(autodxf)
        print(f"\nRuta job: {ruta_job}")
        print(f"CSV: {csv_path}")
        if csv_path and csv_path.exists():
            raw = _leer_csv_lista_largos(csv_path)
            factor = _extraer_factor_wo(WO_LABEL)
            for row in raw:
                cantidad_base = int(float(row.get("cantidad") or row.get("cantidad_base") or 0))
                if cantidad_base <= 0:
                    continue
                rows_csv.append(
                    {
                        "job": JOB,
                        "work_order": WO_LABEL,
                        "factor_wo": factor,
                        "nombre": row.get("nombre"),
                        "clasificacion": row.get("clasificacion"),
                        "largo_in": float(row.get("largo_in") or 0),
                        "cantidad": cantidad_base * factor,
                        "cantidad_base": cantidad_base,
                    }
                )
            print(f"Filas CSV: {len(raw)} -> expandidas: {len(rows_csv)}")
            print(f"Piezas CSV: {len(piezas_desde_rows(rows_csv))}")

    plan_bd = generar_plan(cursor, rows_bd, "TEST-BD")
    plan_csv = generar_plan(cursor, rows_csv, "TEST-CSV") if rows_csv else {}

    for label, plan in [("BD", plan_bd), ("CSV", plan_csv)]:
        if not plan:
            continue
        st = stats_plan(plan)
        mrl = agregar_filas_desde_plan(plan)
        mrl_cant = sum(int(r.get("cantidad") or 0) for r in mrl)
        print(f"\n--- Plan desde {label} ---")
        print(f"  Tiras totales: {st['total_barras']} (stock={st['stock']}, rem={st['remanente']})")
        print(f"  Piezas anidadas: {st['piezas']}")
        print(f"  Filas MRL (barras comerciales): {len(mrl)} tipos, {mrl_cant} barras total")
        for mat, info in sorted(st["por_material"].items()):
            print(f"    {mat}: stock={info['stock']} rem={info['rem']} pzas={info['piezas']}")

    if rows_bd and rows_csv:
        p_bd = {(r["nombre"], r["clasificacion"], r["largo_in"], r["cantidad"]) for r in rows_bd}
        p_csv = {(r["nombre"], r["clasificacion"], r["largo_in"], r["cantidad"]) for r in rows_csv}
        solo_bd = p_bd - p_csv
        solo_csv = p_csv - p_bd
        if solo_bd or solo_csv:
            print("\n!!! DIFERENCIA en filas demanda BD vs CSV !!!")
            if solo_csv:
                print(f"  Solo en CSV ({len(solo_csv)}):")
                for x in list(solo_csv)[:10]:
                    print(f"    {x}")
            if solo_bd:
                print(f"  Solo en BD ({len(solo_bd)}):")
                for x in list(solo_bd)[:10]:
                    print(f"    {x}")

    cursor.close()
    conexion.close()


if __name__ == "__main__":
    main()
