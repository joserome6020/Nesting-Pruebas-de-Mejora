#!/usr/bin/env python3
"""Valida que Consulta_Herinox alimente el catálogo de Lista de largos desde PostgreSQL."""
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.consulta_herinox_bridge import (
    fetch_largos_from_db,
    refresh_herinox_bridge_json,
)


def _simular_carga_inventor(payload: dict) -> list[dict]:
    """Réplica de TryCargarFilasDesdeHerinoxJson (iLogic VB)."""
    b64 = payload.get("largos_catalog_b64") or ""
    if not b64:
        return []
    tsv = base64.b64decode(b64).decode("utf-8")
    filas = []
    for linea in tsv.splitlines():
        texto = linea.strip()
        if not texto:
            continue
        partes = texto.split("\t")
        if len(partes) < 2:
            continue
        combo = partes[9].strip() if len(partes) > 9 else ""
        if not combo:
            combo = f"{partes[0]} | {partes[2]} | {partes[3]}"
        filas.append(
            {
                "codigo": partes[0].strip(),
                "clasificacion": partes[1].strip(),
                "combo": combo,
                "valido_inventor": "|" in combo and combo.lower() != "no clasificado",
            }
        )
    return filas


def main() -> int:
    autodxf_json = Path(
        r"Z:\♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK"
        r"\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología"
        r"\7.- Configuración para equipos de Computo\AutoDXF 2.0\herinox_sync.local.json"
    )

    db_rows = fetch_largos_from_db()
    payload = refresh_herinox_bridge_json(str(autodxf_json) if autodxf_json.parent.exists() else None)
    inventario = _simular_carga_inventor(payload)

    errores = []
    if payload.get("largos_count") != len(db_rows):
        errores.append(
            f"conteo JSON ({payload.get('largos_count')}) != DB ({len(db_rows)})"
        )
    if len(inventario) != len(db_rows):
        errores.append(
            f"filas parseadas Inventor ({len(inventario)}) != DB ({len(db_rows)})"
        )

    invalidas = [f for f in inventario if not f["valido_inventor"]]
    if invalidas:
        errores.append(f"{len(invalidas)} combos sin '|' (inválidos para MaterialHerinoxEsValido)")

    con_esp = [f for f in inventario if "esp N/D" not in f["combo"]]
    ptr045 = next((f for f in inventario if f["codigo"] == "PTR045"), None)
    if ptr045 and "0.339" not in ptr045["combo"]:
        errores.append("PTR045 deberia mostrar esp 0.339 in en el combo")
    can019 = next((f for f in inventario if f["codigo"] == "CAN019"), None)
    if can019 and "esp N/D" not in can019["combo"]:
        errores.append("CAN019 deberia mantener esp N/D (sin espesor en BD)")

    db_codes = {r["codigo"] for r in db_rows}
    inv_codes = {f["codigo"] for f in inventario}
    faltan = sorted(db_codes - inv_codes)
    if faltan:
        errores.append(f"codigos DB no llegaron al catálogo: {faltan[:5]}")

    print("=== Validación Lista de largos / Consulta_Herinox ===")
    print(f"DB Herinox LARGO:     {len(db_rows)}")
    print(f"JSON largos_count:    {payload.get('largos_count')}")
    print(f"Filas combo Inventor: {len(inventario)}")
    print(f"Combos con espesor:   {len(con_esp)}")
    print(f"bridge_source:        {payload.get('largos_bridge_source')}")
    if inventario:
        ejemplo = inventario[0]
        print(f"Ejemplo combo:        {ejemplo['combo'][:90]}...")
    if errores:
        print("RESULTADO: FALLO")
        for e in errores:
            print(f"  - {e}")
        return 1
    print("RESULTADO: OK — el listado de la regla refleja el inventario LARGO de Herinox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
