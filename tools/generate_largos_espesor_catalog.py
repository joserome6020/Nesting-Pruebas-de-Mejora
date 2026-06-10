#!/usr/bin/env python3
"""Genera modules/largos_espesor_catalog.json desde la biblioteca embebida en Lista de largos 2.0."""
from __future__ import annotations

import json
import re
from pathlib import Path

ILOGIC = Path(
    r"Z:\♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK"
    r"\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología"
    r"\7.- Configuración para equipos de Computo\AutoDXF 2.0\Lista de largos 2.0.iLogicVb"
)
OUT = Path(__file__).resolve().parents[1] / "modules" / "largos_espesor_catalog.json"


def main() -> int:
    text = ILOGIC.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"BEGIN_BIBLIOTECA_HERINOX_DATOS.*?datos As String = _\s*(.+?)\s*' END_BIBLIOTECA", text, re.S)
    if not m:
        raise SystemExit("No se encontro bloque BEGIN_BIBLIOTECA_HERINOX_DATOS")
    block = m.group(1)
    lines_raw = re.findall(r'"([^"]*)"', block)
    catalog: dict[str, dict] = {}
    for chunk in lines_raw:
        for line in chunk.replace("\r", "").split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            codigo = parts[0].strip()
            if not codigo:
                continue
            esp_raw = parts[5].strip() if len(parts) > 5 else ""
            catalog[codigo] = {
                "clasificacion": parts[1].strip(),
                "espesor": esp_raw,
            }
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    with_esp = sum(1 for v in catalog.values() if v.get("espesor"))
    print(f"OK -> {OUT} ({len(catalog)} codigos, {with_esp} con espesor)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
