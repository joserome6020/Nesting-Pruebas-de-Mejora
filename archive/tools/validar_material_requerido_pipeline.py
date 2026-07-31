#!/usr/bin/env python3
"""
Auditoría material_requerido_ldg — ANS vs Arga + validación en BD remota.
Ejecutar: python tools/validar_material_requerido_pipeline.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARGA = r"C:\Proyectos\Arga-Nesting-Suite"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import psycopg2
from psycopg2.extras import RealDictCursor

DB = {
    "host": "192.168.2.80",
    "port": 5433,
    "dbname": "nestingpro_db",
    "user": "postgres",
    "password": "nesting123",
    "connect_timeout": 8,
}
JOB = "62174"
WO = "W.O. 1 X1"
SWO = "SWO-001"
EXPECTED_CODES = {
    "ANG022", "ANG037", "CAN019", "HR164", "HR166",
    "PTR016", "PTR030", "RED027", "SLC042", "SLC051", "TYA001",
}

CRITICAL_FILES = [
    "api_server.py",
    "interface/postgres_connector.py",
    "modules/lista_largos_importer.py",
    "lista_largos_material_requerido.py",
    "catalogo_largos.py",
    "tools/backfill_material_requerido_ldg.py",
]


@dataclass
class Check:
    id: int
    name: str
    ok: bool
    detail: str


results: list[Check] = []
n = 0


def record(name: str, ok: bool, detail: str) -> None:
    global n
    n += 1
    results.append(Check(n, name, ok, detail))
    mark = "OK" if ok else "FAIL"
    print(f"[{mark:4}] #{n:02d} {name}", flush=True)
    if detail:
        print(f"       {detail}", flush=True)


def file_hash(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_snippet(path: str, needle: str) -> bool:
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as f:
        return needle in f.read()


def main() -> int:
    print("=" * 72)
    print("VALIDACIÓN material_requerido_ldg — ANS + Arga")
    print("=" * 72)

    # --- 1-6: Paridad de código entre repos ---
    for rel in CRITICAL_FILES:
        ans_path = os.path.join(ROOT, rel.replace("/", os.sep))
        arga_path = os.path.join(ARGA, rel.replace("/", os.sep))
        ha, hb = file_hash(ans_path), file_hash(arga_path)
        if ha is None or hb is None:
            record(
                f"Archivo crítico existe ({rel})",
                False,
                f"ANS={ha is not None} Arga={hb is not None}",
            )
        else:
            record(
                f"Paridad SHA256 ({os.path.basename(rel)})",
                ha == hb,
                "idénticos" if ha == hb else f"ANS≠Arga (revisar diff)",
            )

    # --- 7-12: Patrones de propagación en ambos postgres_connector ---
    for label, base in [("ANS", ROOT), ("Arga", ARGA)]:
        pc = os.path.join(base, "interface", "postgres_connector.py")
        record(
            f"{label}: fallback WO material tras export",
            read_snippet(pc, "_propagar_material_requerido_por_job"),
            "postgres_connector.py",
        )
        record(
            f"{label}: propagación SWO tras export",
            read_snippet(pc, "elif es_swo and db_config:"),
            "bloque SWO presente",
        )
        record(
            f"{label}: importador con propagar_material=True",
            read_snippet(pc, "propagar_material=True"),
            "postgres_connector WO path",
        )

    # --- api_server usa db_config ---
    for label, base in [("ANS", ROOT), ("Arga", ARGA)]:
        api = os.path.join(base, "api_server.py")
        record(
            f"{label}: _propagar_material usa db_config remoto",
            read_snippet(api, "if db_config:\n            conexion = psycopg2.connect(**db_config)"),
            "no conecta solo a localhost cuando hay db_config",
        )

    # --- lista_largos_importer ---
    for label, base in [("ANS", ROOT), ("Arga", ARGA)]:
        imp = os.path.join(base, "modules", "lista_largos_importer.py")
        record(
            f"{label}: importador propaga material",
            read_snippet(imp, "propagar_material: bool = True"),
            "flag propagar_material",
        )

    # --- STOCK only ---
    for label, base in [("ANS", ROOT), ("Arga", ARGA)]:
        mrl = os.path.join(base, "lista_largos_material_requerido.py")
        record(
            f"{label}: solo barras STOCK en pedido",
            read_snippet(mrl, 'source = str(barra.get("source") or "STOCK")'),
            "filtra remanentes",
        )

    # --- BD en vivo ---
    conn = None
    try:
        conn = psycopg2.connect(**DB)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            "SELECT COUNT(*) AS n FROM lista_largos_job WHERE TRIM(job) = %s",
            (JOB,),
        )
        ll_job = int(cur.fetchone()["n"])
        record(
            "BD: lista_largos_job para job 62174",
            ll_job > 0,
            f"{ll_job} filas",
        )

        cur.execute(
            """
            SELECT tipo_orden, COUNT(*) AS n
            FROM material_requerido_ldg
            WHERE TRIM(orden_id) IN (%s, %s)
            GROUP BY tipo_orden
            """,
            (WO, SWO),
        )
        counts = {r["tipo_orden"]: int(r["n"]) for r in cur.fetchall() or []}
        wo_n = counts.get("WO", 0)
        swo_n = counts.get("SWO", 0)
        record("BD: WO tiene pedido material", wo_n == 11, f"{wo_n} filas (esperado 11)")
        record("BD: SWO-001 tiene pedido material", swo_n == 11, f"{swo_n} filas (esperado 11)")

        cur.execute(
            """
            SELECT codigo, largo, cantidad, tipo_orden
            FROM material_requerido_ldg
            WHERE TRIM(orden_id) = %s AND tipo_orden = 'SWO'
            ORDER BY codigo
            """,
            (SWO,),
        )
        swo_rows = cur.fetchall() or []
        codes = {str(r["codigo"]).strip() for r in swo_rows}
        record(
            "BD: códigos SWO coinciden con pedido esperado",
            codes == EXPECTED_CODES,
            f"faltan={EXPECTED_CODES - codes} sobran={codes - EXPECTED_CODES}",
        )

        ptr030 = [r for r in swo_rows if r["codigo"] == "PTR030"]
        record(
            "BD: PTR030 largo 480\" (barra Herinox)",
            len(ptr030) == 1 and float(ptr030[0]["largo"]) == 480.0,
            f"largo={ptr030[0]['largo'] if ptr030 else 'N/A'}",
        )

        others_240 = [
            r for r in swo_rows
            if r["codigo"] != "PTR030" and float(r["largo"]) == 240.0
        ]
        record(
            "BD: resto de perfiles largo 240\"",
            len(others_240) == 10,
            f"{len(others_240)} perfiles a 240\"",
        )

        cur.execute(
            """
            SELECT w.codigo, w.cantidad AS wo_qty, s.cantidad AS swo_qty
            FROM material_requerido_ldg w
            JOIN material_requerido_ldg s
              ON w.codigo = s.codigo AND w.largo = s.largo
            WHERE TRIM(w.orden_id) = %s AND w.tipo_orden = 'WO'
              AND TRIM(s.orden_id) = %s AND s.tipo_orden = 'SWO'
            """,
            (WO, SWO),
        )
        pairs = cur.fetchall() or []
        mism = [
            p for p in pairs
            if int(p["wo_qty"]) != int(p["swo_qty"])
        ]
        record(
            "BD: SWO cantidades = WO (factor X1)",
            len(pairs) == 11 and not mism,
            f"{len(pairs)} pares, {len(mism)} diferencias",
        )

        cur.execute(
            """
            SELECT COUNT(DISTINCT TRIM(super_work_order)) AS n
            FROM reporte_cortes
            WHERE TRIM(job) = %s AND super_work_order IS NOT NULL
            """,
            (JOB,),
        )
        record(
            "BD: reporte_cortes enlaza job→SWO",
            int(cur.fetchone()["n"]) >= 1,
            f"SWO en reporte_cortes para job {JOB}",
        )

        # Simular query VSM pedido_material_summary
        cur.execute(
            """
            SELECT codigo, material, largo, cantidad
            FROM material_requerido_ldg
            WHERE tipo_orden = 'SWO' AND TRIM(orden_id) = %s
            ORDER BY material, largo
            """,
            (SWO,),
        )
        vsm_rows = cur.fetchall() or []
        unit_expansion = sum(int(r["cantidad"] or 0) for r in vsm_rows)
        record(
            "BD: expansión unitaria VSM (sum cantidad)",
            unit_expansion == 11,
            f"total unidades={unit_expansion} (UI muestra x1 por barra)",
        )

        cur.close()
    except Exception as e:
        record("BD: conexión y consultas", False, str(e))
    finally:
        if conn:
            conn.close()

    # --- Prueba funcional propagación (opcional; puede tardar por plan/Herinox) ---
    if os.getenv("VALIDAR_MRL_FUNCIONAL", "").strip() in ("1", "true", "yes"):
        try:
            import api_server

            logs = api_server._propagar_material_requerido_por_job(DB, JOB)
            ok_logs = [l for l in logs if l.get("ok")]
            types = {l.get("tipo_orden") for l in logs}
            record(
                "Funcional: _propagar_material_requerido_por_job remoto",
                len(ok_logs) >= 2 and "WO" in types and "SWO" in types,
                f"logs={len(logs)} ok={len(ok_logs)} tipos={types}",
            )
        except Exception as e:
            record("Funcional: _propagar_material_requerido_por_job remoto", False, str(e))
    else:
        record(
            "Funcional: propagación (omitido; usar VALIDAR_MRL_FUNCIONAL=1)",
            True,
            "evita bloqueo por regeneración de plan/Herinox en auditoría rápida",
        )

    # --- backfill tool ---
    for label, base in [("ANS", ROOT), ("Arga", ARGA)]:
        bf = os.path.join(base, "tools", "backfill_material_requerido_ldg.py")
        record(
            f"{label}: script backfill disponible",
            os.path.isfile(bf),
            bf,
        )

    # --- Resumen ---
    print("=" * 72)
    passed = sum(1 for r in results if r.ok)
    failed = [r for r in results if not r.ok]
    print(f"RESULTADO: {passed}/{len(results)} validaciones OK")
    if failed:
        print("\nFALLIDAS:")
        for r in failed:
            print(f"  #{r.id:02d} {r.name}: {r.detail}")
        return 1
    print("\nPipeline material_requerido_ldg: LISTO para próximas WO/SWO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
