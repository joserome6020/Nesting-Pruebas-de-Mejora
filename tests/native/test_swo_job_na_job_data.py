"""Candado SWO-099 (W.O. 124 X2, job VSM 261092-HI): nunca registrar job 'N/A'.

Caso real 2026-09-29: la carpeta `ATC_COMPARTMENT\\VANTRAN\\261092-HI` traía
`job_data_261092.csv` (copiado del job TANKS 261092). Al descargar la SWO el ANS
buscaba exactamente `job_data_261092-HI.csv`, no lo encontraba y registraba
cliente/job 'N/A' en diccionario_swo -> reporte_cortes -> VSM_JOB:N/A
"Job no encontrado".
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from interface.swo_job_meta import es_job_placeholder, leer_job_data  # noqa: E402

ENC = "Job Number,Producto,Cliente,Cantidad,Purchase Order,Costo Total\n"


def _carpeta(base: Path, nombre: str, csv_nombre: str | None, fila: str) -> str:
    d = base / nombre
    d.mkdir(parents=True)
    if csv_nombre:
        (d / csv_nombre).write_text(ENC + fila + "\n", encoding="utf-8")
    return str(d)


def main() -> int:
    fallos: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        ruta = _carpeta(base, "261092-HI", "job_data_261092.csv",
                        "261092,ATC/COMPARTMENT,VANTRAN,2,27164,2654.54")
        got = leer_job_data(ruta, "261092-HI")
        if got != ("VANTRAN", "261092-HI", "ATC/COMPARTMENT"):
            fallos.append(f"CSV mal nombrado (SWO-099): {got}")

        ruta = _carpeta(base, "261091-HI", "job_data_261091-HI.csv",
                        "261091-HI,ATC/COMPARTMENT,VANTRAN,1,27164,841.67")
        got = leer_job_data(ruta, "261091-HI")
        if got != ("VANTRAN", "261091-HI", "ATC/COMPARTMENT"):
            fallos.append(f"CSV correcto: {got}")

        ruta = _carpeta(base, "999999-X", None, "")
        got = leer_job_data(ruta, "999999-X")
        if got[1] != "999999-X":
            fallos.append(f"Sin CSV debe usar la carpeta como job: {got}")

        ruta = _carpeta(base, "888888", "job_data_888888.csv", "N/A,TANKS,ACME,1,1,1")
        got = leer_job_data(ruta, "888888")
        if got[1] != "888888":
            fallos.append(f"CSV con Job Number N/A: {got}")

        if leer_job_data(None, "777777")[1] != "777777":
            fallos.append("Sin ruta de job debe usar el nombre del job")

    for j in ("N/A", "n/a", "", None, "NA"):
        if not es_job_placeholder(j):
            fallos.append(f"es_job_placeholder({j!r}) debe ser True")
    if es_job_placeholder("261092-HI"):
        fallos.append("261092-HI no es placeholder")

    src_exp = (RAIZ / "interface/qt/tabs/_mixin_export.py").read_text(encoding="utf-8")
    if "es_job_placeholder(j)" not in src_exp:
        fallos.append("_mixin_export debe bloquear jobs 'N/A' antes de VSM")
    src_pg = (RAIZ / "interface/postgres_connector.py").read_text(encoding="utf-8")
    if "NOT IN ('', 'N/A', 'NA', 'NONE')" not in src_pg:
        fallos.append("postgres_connector debe ignorar jobs 'N/A' del ERP")

    if fallos:
        print("FAIL:\n  " + "\n  ".join(fallos))
        return 1
    print("OK swo job N/A / job_data mal nombrado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
