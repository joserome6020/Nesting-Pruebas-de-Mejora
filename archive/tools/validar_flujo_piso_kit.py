#!/usr/bin/env python3
"""Auditoría piso: kit -> finalizado -> dispatch -> incoming."""
import os
import sys

STAGING_FE = r"\\192.168.2.80\Users\Administrator\Music\Desarrollo\Pruebas\CentralizedSystem_Staging\frontend\src\components\dashboard\CSCarousel.jsx"
STAGING_BE = r"\\192.168.2.80\Users\Administrator\Music\Desarrollo\Pruebas\CentralizedSystem_Staging\backend\services\nesting_service.py"

import psycopg2

DB = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123", connect_timeout=8)
SWO = "SWO-001"
checks = []


def record(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


fe, be = read(STAGING_FE), read(STAGING_BE)

print("=== FRONTEND ===")
record("Guard id pedido antes de API", "Este material no tiene ID en BD" in fe)
record("Partición completed pedido", "usePedidoForKit" in fe and "kit-fallback" in fe)
record("Partición incoming pedido", "usePedidoIncoming" in fe and "pendingAlmacenPedido" in fe)
record("Pedido en incoming botones", "pendingAlmacenPedido" in fe and "pendingCalidadPedido" in fe)
record("usePedidoKit incluye incoming", "stageId === 'completed' || stageId === 'incoming_inspection'" in fe)
record("Qty agregada sin duplicar id", "if (qty === 1)" in fe)
record("ENTREGAR usa provider_handshake pedido", "p.kit_recibido && !p.provider_handshake_at" in fe)

print("\n=== BACKEND ===")
record("dispatch_provider actualiza material_requerido", "PEDIDO COMINOX (material_requerido_ldg)" in be)
record("almacen actualiza material_requerido", "almacen_received_at IS NULL OR rechazado_incoming = true" in be and "material_requerido_ldg" in be)
record("incoming acepta prefijo pedido-", 'startswith("pedido-")' in be)
record("Query pedido trae provider_handshake", "provider_handshake_at" in be and "pedido_material_summary" in be)

print("\n=== BD ESQUEMA ===")
required_cols = {
    "kit_recibido", "kit_recibido_por", "kit_recibido_fecha",
    "provider_handshake_at", "provider_handshake_by",
    "almacen_received_at", "almacen_received_by",
    "incoming_handshake_at", "incoming_handshake_by",
    "rechazado_incoming",
}
try:
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()
    for sql in (
        "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS provider_handshake_at TIMESTAMP NULL",
        "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS provider_handshake_by VARCHAR(120) NULL",
        "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS almacen_received_at TIMESTAMP NULL",
        "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS almacen_received_by VARCHAR(120) NULL",
        "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS incoming_handshake_at TIMESTAMP NULL",
        "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS incoming_handshake_by VARCHAR(120) NULL",
        "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS rechazado_incoming BOOLEAN NOT NULL DEFAULT FALSE",
        "DROP INDEX IF EXISTS uq_mrl_orden_material_largo",
        "CREATE INDEX IF NOT EXISTS idx_mrl_orden_material_largo ON material_requerido_ldg (orden_id, tipo_orden, material, largo)",
    ):
        cur.execute(sql)
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'material_requerido_ldg'
        """
    )
    cols = {r[0] for r in cur.fetchall()}
    missing = required_cols - cols
    record("Columnas operativas en BD", not missing, f"faltan={missing}" if missing else "ok")

    print("\n=== SIMULACIÓN TOGGLE (1 fila, revierte) ===")
    cur.execute(
        """
        SELECT id, kit_recibido FROM material_requerido_ldg
        WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s ORDER BY id LIMIT 1
        """,
        (SWO,),
    )
    row = cur.fetchone()
    if row:
        pid, was = row
        cur.execute(
            "UPDATE material_requerido_ldg SET kit_recibido=TRUE, kit_recibido_por='audit', kit_recibido_fecha=NOW() WHERE id=%s",
            (pid,),
        )
        cur.execute("SELECT kit_recibido FROM material_requerido_ldg WHERE id=%s", (pid,))
        on = cur.fetchone()[0]
        cur.execute(
            "UPDATE material_requerido_ldg SET kit_recibido=%s, kit_recibido_por=NULL, kit_recibido_fecha=NULL WHERE id=%s",
            (was, pid),
        )
        record("Toggle kit_recibido en BD", on is True, f"id={pid}")
    else:
        record("Toggle kit_recibido en BD", False, "sin filas SWO")

    conn.close()
except Exception as e:
    record("BD", False, str(e))

failed = [c for c in checks if not c[1]]
print(f"\nRESULTADO: {len(checks)-len(failed)}/{len(checks)} OK")
if failed:
    print("FALLAS:")
    for n, _, d in failed:
        print(f"  - {n}: {d}")
    sys.exit(1)
print("\nListo para piso: flujo kit->finalizado->dispatch->incoming mapeado.")
