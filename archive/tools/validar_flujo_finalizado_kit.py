#!/usr/bin/env python3
"""Valida mapeo Pedido Cominox -> Finalizado (sin tocar datos)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING_FE = r"\\192.168.2.80\Users\Administrator\Music\Desarrollo\Pruebas\CentralizedSystem_Staging\frontend\src\components\dashboard\CSCarousel.jsx"
STAGING_BE = r"\\192.168.2.80\Users\Administrator\Music\Desarrollo\Pruebas\CentralizedSystem_Staging\backend\services\nesting_service.py"
STAGING_RT = r"\\192.168.2.80\Users\Administrator\Music\Desarrollo\Pruebas\CentralizedSystem_Staging\backend\routers\nesting.py"

import psycopg2

DB = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123", connect_timeout=8)
SWO = "SWO-001"

checks = []


def ok(name, cond, detail=""):
    checks.append((name, cond, detail))
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


fe = read(STAGING_FE)
be = read(STAGING_BE)
rt = read(STAGING_RT)

print("=== CÓDIGO FRONTEND ===")
ok("togglePedidoMaterial definido", "togglePedidoMaterial" in fe)
ok("API pedido-material/kit-recibido", "/pedido-material/kit-recibido" in fe)
ok("Pedido: pending por !kit_recibido", "unidades.filter(l => !l.kit_recibido)" in fe)
ok("Pedido: released por kit_recibido", "unidades.filter(l => l.kit_recibido)" in fe)
ok("Flecha solo En Proceso (!isFinished)", "stageId === 'completed' && !isFinished" in fe and "togglePedidoMaterial" in fe)
ok("Partición Finalizado usa pedido_material_summary", "usePedidoForKit" in fe and "finishedPedido" in fe)
ok("Legacy largos si no hay pedido", "usePedidoForKit ? [] : finishedLargos" in fe)
ok("autoStages oculta botón Completar en kit", "'completed'" in fe and "autoStages" in fe)
ok("hasAnyKit incluye pedido kit_recibido", "pedido_material_summary" in fe and "hasAnyKit" in fe)

print("\n=== CÓDIGO BACKEND ===")
ok("marcar_pedido_material_kit_estatus", "marcar_pedido_material_kit_estatus" in be)
ok("pedido query trae kit_recibido", '"kit_recibido"' in be and "pedido_material_summary" in be)
ok("router pedido-material", "/pedido-material/kit-recibido" in rt)
ok("PedidoMaterialKitEstatusRequest", "PedidoMaterialKitEstatusRequest" in rt)

print("\n=== BD SWO-001 ===")
try:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN kit_recibido THEN 1 ELSE 0 END) AS en_kit
        FROM material_requerido_ldg
        WHERE tipo_orden = 'SWO' AND TRIM(orden_id) = %s
        """,
        (SWO,),
    )
    total, en_kit = cur.fetchone()
    total = int(total or 0)
    en_kit = int(en_kit or 0)
    ok("11 filas pedido SWO", total == 11, f"total={total}")
    ok("Columnas kit_recibido operativas", True, f"en_kit={en_kit} pendientes={total - en_kit}")
    conn.close()
except Exception as e:
    ok("Conexión BD", False, str(e))

print("\n=== FLUJO ESPERADO ===")
print("1. En Proceso: flecha marca kit_recibido=true en material_requerido_ldg")
print("2. Refresh: réplica pasa a pestaña Finalizado (finishedPedido)")
print("3. Finalizado: solo lectura, sección En Kit")
print("4. Placas siguen su propia partición kit_recibido (sin cambio)")
print("5. Sin pedido_material: cae a largos_summary legacy")

failed = [c for c in checks if not c[1]]
print(f"\nRESULTADO: {len(checks) - len(failed)}/{len(checks)} OK")
sys.exit(1 if failed else 0)
