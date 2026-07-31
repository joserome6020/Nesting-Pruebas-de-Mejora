#!/usr/bin/env python3
"""
Simula escenarios de distintas PCs para Lista de largos / Consulta_Herinox:
  A) Carpeta corporativa Z:\\...\\AutoDXF 2.0
  B) Job local sin Z: (solo AutoDXF\\ del job)
  C) Variable ARGA_AUTODXF20_DIR
  D) Python genérico (sys.executable)
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORP_UNC = Path(r"\\192.168.2.47\arga") / (
    r"♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK"
    r"\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología"
    r"\7.- Configuración para equipos de Computo\AutoDXF 2.0"
)
CORP = CORP_UNC if CORP_UNC.is_dir() else Path(
    r"Z:\♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK"
    r"\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología"
    r"\7.- Configuración para equipos de Computo\AutoDXF 2.0"
)
# Script autocontenido (mismo que en Z:\...\AutoDXF 2.0\Consulta_Herinox.py)
BRIDGE_STANDALONE = ROOT / "modules" / "consulta_herinox_bridge.py"


def _combo_count(json_path: Path) -> tuple[int, str]:
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    b64 = raw.get("largos_catalog_b64") or ""
    if not b64:
        return 0, raw.get("largos_bridge_source", "?")
    tsv = base64.b64decode(b64).decode("utf-8")
    n = sum(1 for ln in tsv.splitlines() if ln.strip())
    return n, raw.get("largos_bridge_source", "?")


def _run_bridge(py_script: Path, out_json: Path) -> tuple[bool, str]:
    cmd = [sys.executable, str(py_script), str(out_json)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            cwd=str(py_script.parent),
        )
        detail = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        if proc.returncode != 0:
            return False, f"exit {proc.returncode}: {detail[:300]}"
        if not out_json.is_file():
            return False, "no generó JSON"
        return True, detail[:220]
    except Exception as exc:
        return False, str(exc)


def _find_bridge_like_ilogic(corp: Path | None, job_autodxf: Path, env_dir: str | None) -> Path | None:
    """Réplica simplificada de FindBridgeScriptListaLargos."""
    names = ("Consulta_Herinox.py", "Consuta_Herinox.py")
    dirs: list[Path] = []
    if corp and corp.is_dir():
        dirs.append(corp)
    if env_dir:
        p = Path(env_dir)
        if p.is_dir() and p not in dirs:
            dirs.append(p)
    if job_autodxf.is_dir() and job_autodxf not in dirs:
        dirs.append(job_autodxf)
    for d in dirs:
        for n in names:
            hit = d / n
            if hit.is_file():
                return hit
    return None


def escenario_a_corp() -> tuple[bool, str]:
    py = CORP / "Consulta_Herinox.py"
    json_out = CORP / "herinox_sync.local.json"
    if not py.is_file():
        return False, f"Z: no accesible o sin script: {py}"
    ok, msg = _run_bridge(py, json_out)
    if not ok:
        return False, f"corp bridge falló: {msg}"
    n, src = _combo_count(json_out)
    if n < 1:
        return False, "JSON corp sin largos_catalog_b64"
    return True, f"corp OK: {n} largos ({src}) | {msg}"


def escenario_b_job_sin_z() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="arga_job_") as tmp:
        job = Path(tmp) / "OTC_TEST"
        autodxf = job / "AutoDXF"
        autodxf.mkdir(parents=True)
        dest_py = autodxf / "Consulta_Herinox.py"
        corp_py = CORP / "Consulta_Herinox.py"
        src = corp_py if corp_py.is_file() else BRIDGE_STANDALONE
        shutil.copy2(src, dest_py)
        found = _find_bridge_like_ilogic(corp=None, job_autodxf=autodxf, env_dir=None)
        if not found:
            return False, "simulación job: no encontró Consulta_Herinox.py"
        out_json = autodxf / "herinox_sync.local.json"
        ok, msg = _run_bridge(found, out_json)
        if not ok:
            return False, f"job bridge falló: {msg}"
        n, src = _combo_count(out_json)
        if n < 1:
            return False, "job JSON sin catálogo"
        return True, f"job sin Z: OK: {n} largos | script en {dest_py}"


def escenario_c_env_var() -> tuple[bool, str]:
    if not CORP.is_dir():
        return False, "omitido: Z: no disponible para usar como ARGA_AUTODXF20_DIR"
    with tempfile.TemporaryDirectory(prefix="arga_env_") as tmp:
        job_autodxf = Path(tmp) / "AutoDXF"
        job_autodxf.mkdir()
        found = _find_bridge_like_ilogic(corp=None, job_autodxf=job_autodxf, env_dir=str(CORP))
        if not found or found.parent != CORP:
            return False, f"env ARGA_AUTODXF20_DIR no resolvió corp: {found}"
        out_json = job_autodxf / "herinox_sync.local.json"
        ok, msg = _run_bridge(found, CORP / "herinox_sync.local.json")
        if ok:
            shutil.copy2(CORP / "herinox_sync.local.json", out_json)
        if not ok or not out_json.is_file():
            return False, f"env scenario falló: {msg}"
        n, _ = _combo_count(out_json)
        return True, f"ARGA_AUTODXF20_DIR OK: {n} largos copiados al job"


def escenario_d_python() -> tuple[bool, str]:
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return False, "psycopg2 no instalado en este Python"
    ver = sys.version.split()[0]
    exe = sys.executable
    return True, f"Python {ver} en {exe}"


def escenario_e_unc_sin_z() -> tuple[bool, str]:
    """PC sin Z: — solo UNC al share arga."""
    py = CORP_UNC / "Consulta_Herinox.py"
    if not py.is_file():
        return False, f"UNC no accesible: {py}"
    with tempfile.TemporaryDirectory(prefix="arga_noz_") as tmp:
        job_autodxf = Path(tmp) / "AutoDXF"
        job_autodxf.mkdir()
        found = _find_bridge_like_ilogic(corp=None, job_autodxf=job_autodxf, env_dir=str(CORP_UNC))
        if not found:
            return False, "sin Z: no resolvió script vía ARGA_AUTODXF20_DIR simulado"
        out_json = job_autodxf / "herinox_sync.local.json"
        ok, msg = _run_bridge(found, out_json)
        if not ok:
            return False, f"UNC sin Z falló: {msg}"
        n, _ = _combo_count(out_json)
        return True, f"UNC sin Z: OK ({n} largos)"


def main() -> int:
    casos = [
        ("A — Carpeta corporativa (UNC o Z:)", escenario_a_corp),
        ("B — Job local (PC sin Z:, script en AutoDXF\\)", escenario_b_job_sin_z),
        ("C — Variable ARGA_AUTODXF20_DIR", escenario_c_env_var),
        ("D — Python + psycopg2 en esta PC", escenario_d_python),
        ("E — UNC \\\\192.168.2.47\\arga sin unidad Z:", escenario_e_unc_sin_z),
    ]
    print("=== Prueba multi-PC Lista de largos / Consulta_Herinox ===\n")
    fallos = 0
    for titulo, fn in casos:
        try:
            ok, det = fn()
        except Exception as exc:
            ok, det = False, str(exc)
        estado = "OK" if ok else "FALLO"
        if not ok:
            fallos += 1
        print(f"[{estado}] {titulo}")
        print(f"       {det}\n")

    if fallos:
        print(f"RESULTADO: {fallos} escenario(s) fallaron.")
        print("Requisitos por PC: Python 3, psycopg2, red a 192.168.2.80:5439,")
        print("y acceso a \\\\192.168.2.47\\arga\\...\\AutoDXF 2.0 (o Z: o ARGA_AUTODXF20_DIR).")
        return 1
    print("RESULTADO: OK — los 4 escenarios simulados pasan en esta máquina.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
