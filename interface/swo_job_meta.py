"""Identidad comercial (cliente/job/producto) de una WO al armar una SWO."""
from __future__ import annotations

import csv
import glob
import os

PLACEHOLDERS_JOB = frozenset({"", "N/A", "NA", "NONE", "NULL", "-"})


def es_job_placeholder(job) -> bool:
    return str(job or "").strip().upper() in PLACEHOLDERS_JOB


def leer_job_data(ruta_base_job: str | None, job: str) -> tuple[str, str, str]:
    """Devuelve (cliente, job_numero, producto) desde `job_data_*.csv` del job.

    El job VSM es el nombre de la carpeta (`job`). Si el CSV no se llama
    `job_data_{job}.csv` (p. ej. copiado de otro job), solo se toman cliente y
    producto y el job se queda con el nombre de la carpeta. Nunca devuelve
    'N/A' como job si hay un nombre de carpeta.
    """
    job = str(job or "").strip()
    cliente = producto = "N/A"
    job_com = ""
    if ruta_base_job:
        exacto = os.path.join(ruta_base_job, f"job_data_{job}.csv")
        archivos = [exacto] if os.path.isfile(exacto) else sorted(
            glob.glob(os.path.join(ruta_base_job, "job_data_*.csv"))
        )
        if len(archivos) == 1:
            try:
                with open(archivos[0], encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    enc = [str(e).strip().upper() for e in next(reader, [])]
                    datos = next(reader, [])

                def _col(nombre: str) -> str:
                    if nombre in enc and enc.index(nombre) < len(datos):
                        return str(datos[enc.index(nombre)]).strip()
                    return ""

                cliente = _col("CLIENTE") or cliente
                producto = _col("PRODUCTO") or producto
                if archivos[0] == exacto:
                    job_com = _col("JOB NUMBER") or _col("JOB")
            except Exception:
                pass
    if es_job_placeholder(job_com):
        job_com = job if not es_job_placeholder(job) else "N/A"
    return cliente, job_com, producto
