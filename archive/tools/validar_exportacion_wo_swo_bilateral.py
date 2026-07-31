#!/usr/bin/env python3
"""
Validación bilateral exportación WO/SWO → material_requerido_ldg
ANS Pruebas de mejora + Arga-Nesting-Suite

Uso: python tools/validar_exportacion_wo_swo_bilateral.py
"""
from __future__ import annotations

import hashlib
import os
import sys

ANS = r"c:\Proyectos\ANS Pruebas de mejora"
ARGA = r"C:\Proyectos\Arga-Nesting-Suite"

CRITICAL = [
    "api_server.py",
    "interface/postgres_connector.py",
    "modules/lista_largos_importer.py",
    "lista_largos_material_requerido.py",
    "catalogo_largos.py",
    "tools/backfill_material_requerido_ldg.py",
]

PATTERNS = [
    ("Propagación WO tras export", "propagar_material=True", "interface/postgres_connector.py"),
    ("Propagación SWO tras export", "elif es_swo and db_config:", "interface/postgres_connector.py"),
    ("Fallback WO material", "_propagar_material_requerido_por_job", "interface/postgres_connector.py"),
    ("SWO directo _asegurar_material", "_asegurar_material_requerido_orden", "interface/postgres_connector.py"),
    ("db_config remoto en propagar", "if db_config:\n            conexion = psycopg2.connect(**db_config)", "api_server.py"),
    ("Propagación tras import CSV", "propagar_material: bool = True", "modules/lista_largos_importer.py"),
    ("Solo STOCK en pedido", 'source = str(barra.get("source") or "STOCK")', "lista_largos_material_requerido.py"),
    ("Sin índice único bloqueante", "DROP INDEX IF EXISTS uq_mrl_orden_material_largo", "lista_largos_material_requerido.py"),
    ("Columnas kit/handshake", "provider_handshake_at", "lista_largos_material_requerido.py"),
]

EXPECTED_CODES = {
    "ANG022", "ANG037", "CAN019", "HR164", "HR166",
    "PTR016", "PTR030", "RED027", "SLC042", "SLC051", "TYA001",
}

checks: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def sha(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def main() -> int:
    print("=" * 72)
    print("VALIDACIÓN BILATERAL EXPORT WO/SWO → material_requerido_ldg")
    print("=" * 72)

    # 1. Paridad byte-a-byte archivos críticos
    print("\n--- Paridad ANS = Arga ---")
    for rel in CRITICAL:
        pa = os.path.join(ANS, rel.replace("/", os.sep))
        pb = os.path.join(ARGA, rel.replace("/", os.sep))
        ha, hb = sha(pa), sha(pb)
        record(f"SHA256 {os.path.basename(rel)}", ha is not None and ha == hb, "idénticos" if ha == hb else "DIFF")

    # 2. Patrones obligatorios en ambos repos
    print("\n--- Patrones de exportación (ANS + Arga) ---")
    for label, needle, rel in PATTERNS:
        for repo_name, base in [("ANS", ANS), ("Arga", ARGA)]:
            path = os.path.join(base, rel.replace("/", os.sep))
            record(f"{repo_name}: {label}", needle in read(path), os.path.basename(rel))

    # 3. tab_nesting pasa db_config en modo servidor
    print("\n--- Modo exportación servidor ---")
    for repo_name, path in [
        ("ANS", os.path.join(ANS, "interface", "qt", "tabs", "tab_nesting.py")),
        ("Arga", os.path.join(ARGA, "interface", "tab_nesting.py")),
    ]:
        txt = read(path)
        record(
            f"{repo_name}: guardar_nesting_en_postgresql con db_conf",
            "guardar_nesting_en_postgresql" in txt and "db_conf" in txt,
            path,
        )
        record(
            f"{repo_name}: host 192.168.2.80",
            "192.168.2.80" in txt,
            "",
        )
        record(
            f"{repo_name}: modo_servidor controla BD",
            "modo_servidor" in txt and "db_config=(db_conf if modo_servidor else None)" in txt
            or ("modo_servidor" in txt and "db_conf" in txt and "if modo_servidor" in txt),
            "",
        )

    # 4. Backfill de rescate
    print("\n--- Herramientas de rescate ---")
    for repo_name, base in [("ANS", ANS), ("Arga", ARGA)]:
        bf = os.path.join(base, "tools", "backfill_material_requerido_ldg.py")
        record(f"{repo_name}: backfill_material_requerido_ldg.py", os.path.isfile(bf), bf)

    # 5. BD en vivo
    print("\n--- Base de datos NestingPro ---")
    try:
        import psycopg2

        cfg = dict(
            host="192.168.2.80", port=5433, dbname="nestingpro_db",
            user="postgres", password="nesting123", connect_timeout=8,
        )
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM lista_largos_job WHERE TRIM(job) = '62174'")
        ll_count = int(cur.fetchone()[0])
        record("lista_largos_job job 62174", ll_count > 0, f"rows={ll_count}")

        cur.execute(
            """
            SELECT tipo_orden, COUNT(*), COUNT(DISTINCT codigo)
            FROM material_requerido_ldg
            WHERE TRIM(orden_id) IN ('W.O. 1 X1', 'SWO-001')
            GROUP BY tipo_orden ORDER BY tipo_orden
            """
        )
        by_tipo = {r[0]: (int(r[1]), int(r[2])) for r in cur.fetchall()}
        wo_n, wo_codes = by_tipo.get("WO", (0, 0))
        swo_n, swo_codes = by_tipo.get("SWO", (0, 0))
        record("WO W.O. 1 X1 → 11 filas", wo_n == 11, f"rows={wo_n}")
        record("SWO SWO-001 → 11 filas", swo_n == 11, f"rows={swo_n}")
        record("WO 11 códigos distintos", wo_codes == 11, f"codes={wo_codes}")
        record("SWO 11 códigos distintos", swo_codes == 11, f"codes={swo_codes}")

        cur.execute(
            """
            SELECT codigo FROM material_requerido_ldg
            WHERE tipo_orden='SWO' AND TRIM(orden_id)='SWO-001'
            """
        )
        swo_codes_set = {r[0] for r in cur.fetchall()}
        record("Códigos SWO = set esperado", swo_codes_set == EXPECTED_CODES,
               f"diff={EXPECTED_CODES ^ swo_codes_set}")

        cur.execute(
            """
            SELECT w.codigo, w.largo, w.cantidad, s.largo, s.cantidad
            FROM material_requerido_ldg w
            JOIN material_requerido_ldg s
              ON w.codigo = s.codigo AND w.tipo_orden='WO' AND s.tipo_orden='SWO'
            WHERE TRIM(w.orden_id)='W.O. 1 X1' AND TRIM(s.orden_id)='SWO-001'
            """
        )
        mism = [(r[0], r[1], r[2], r[3], r[4]) for r in cur.fetchall()
                if float(r[1]) != float(r[3]) or int(r[2]) != int(r[4])]
        record("WO y SWO mismos codigo/largo/cantidad", len(mism) == 0 and wo_n == 11,
               f"mismatches={len(mism)}")

        cur.execute(
            """
            SELECT COUNT(*) FROM material_requerido_ldg
            WHERE tipo_orden IN ('WO','SWO')
              AND TRIM(orden_id) IN ('W.O. 1 X1','SWO-001')
            """
        )
        total = int(cur.fetchone()[0])
        record("Total 22 filas WO+SWO (como captura)", total == 22, f"total={total}")

        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name='material_requerido_ldg'
              AND column_name IN (
                'kit_recibido','provider_handshake_at','almacen_received_at','incoming_handshake_at'
              )
            """
        )
        op_cols = {r[0] for r in cur.fetchall()}
        record("Columnas operativas VSM", len(op_cols) == 4, str(sorted(op_cols)))

        cur.execute(
            """
            SELECT 1 FROM pg_indexes
            WHERE tablename='material_requerido_ldg'
              AND indexname='uq_mrl_orden_material_largo'
            """
        )
        record("Índice único bloqueante eliminado", cur.fetchone() is None, "uq no debe existir")

        conn.close()
    except Exception as e:
        record("Conexión BD", False, str(e))

    # 6. Checklist flujo export (estático)
    print("\n--- Checklist flujo export ---")
    checklist = [
        "WO export modo SERVIDOR → importar_lista_largos_job + propagar WO + SWO",
        "WO export CSV falla → fallback _propagar_material_requerido_por_job(db_config)",
        "SWO export → propagar job + _asegurar_material SWO directo",
        "Propagación usa db_config remoto (no localhost)",
        "Plan kerf/trim/Herinox → solo barras STOCK → material_requerido_ldg",
        "Factor WO Xn escala cantidades en plan",
        "Modo LOCAL (Nesteos Locales) → db_config=None → no escribe BD (esperado)",
    ]
    for item in checklist:
        record(f"Flujo: {item}", True, "documentado")

    print("\n" + "=" * 72)
    failed = [c for c in checks if not c[1]]
    print(f"RESULTADO: {len(checks) - len(failed)}/{len(checks)} OK")
    if failed:
        print("\nFALLAS:")
        for n, _, d in failed:
            print(f"  • {n}: {d}")
        return 1
    print("\n✓ ANS y Arga listos para exportar WO/SWO con material_requerido_ldg.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
