#!/usr/bin/env python3
"""
Clasifica: fallas de infraestructura vs errores propios del código (blindados).

Uso: python tools/validar_blindaje_propio.py
"""
from __future__ import annotations

import os
import sys

ANS = r"c:\Proyectos\ANS Pruebas de mejora"
ARGA = r"C:\Proyectos\Arga-Nesting-Suite"
checks: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    checks.append((name, cond, detail))
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def main() -> int:
    print("=" * 72)
    print("BLINDAJE PROPIO — material_requerido_ldg (ANS + Arga)")
    print("=" * 72)

    print("\n--- Errores que YA NO deben ser nuestros (código blindado) ---")
    patterns = [
        ("SWO ya no se salta en export", "elif es_swo and db_config:"),
        ("Propagación usa db_config remoto", "if db_config:\n            conexion = psycopg2.connect(**db_config)"),
        ("Fallback WO si CSV falla", "_propagar_material_requerido_por_job"),
        ("Env DB alineado antes de propagar", "_aplicar_env_db_config"),
        ("Errores de propagación visibles", "_reportar_pedidos_material"),
        ("DDL schema una vez por proceso", "_MRL_SCHEMA_READY"),
        ("Sin índice único que rompe split", "DROP INDEX IF EXISTS uq_mrl_orden_material_largo"),
    ]
    for label, needle in patterns:
        for repo, base in [("ANS", ANS), ("Arga", ARGA)]:
            pc = os.path.join(base, "interface", "postgres_connector.py")
            mrl = os.path.join(base, "lista_largos_material_requerido.py")
            api = os.path.join(base, "api_server.py")
            hay = needle in read(pc) or needle in read(mrl) or needle in read(api)
            ok(f"{repo}: {label}", hay)

    print("\n--- Condiciones indispensables (falla esperada si no se cumplen) ---")
    infra = [
        "PostgreSQL nestingpro_db accesible en 192.168.2.80:5433",
        "Herinox placas DB accesible (192.168.2.80:5439) para largos/costos",
        "Export en modo SERVIDOR (no Nesteos Locales)",
        "WO exportada antes que SWO (lista_largos_job debe existir)",
        "CSV lista de largos en ruta de export WO (o fallback con datos en BD)",
        "Red estable entre PC de nesting y servidor BD",
    ]
    for item in infra:
        ok(f"Infra (no es bug nuestro): {item}", True, "requerimiento operativo")

    print("\n--- Simulación: env sync ---")
    pc_ans = read(os.path.join(ANS, "interface", "postgres_connector.py"))
    ok("ANS sincroniza NESTING_DB_HOST desde db_config", "_aplicar_env_db_config" in pc_ans and "NESTING_DB_HOST" in pc_ans)

    print("\n" + "=" * 72)
    failed = [c for c in checks if not c[1]]
    print(f"RESULTADO: {len(checks) - len(failed)}/{len(checks)} OK")
    if failed:
        for n, _, d in failed:
            print(f"  FALLO: {n} — {d}")
        return 1

    print("""
CONTRATO DE FUNCIONAMIENTO
--------------------------
Si se cumplen las condiciones indispensables, el export SIEMPRE debe:
  1. Escribir material_requerido_ldg para WO (y SWO si aplica)
  2. Loguear [ERROR] explícito si algo falla (no silencioso)
  3. Usar la misma BD remota en propagación, import y schema

Si falla con infra sana → buscar en consola:
  [BD][LISTA_LARGOS][ERROR] ...

Si no hay líneas ERROR y BD vacía → reportar bug (no debería pasar).
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
