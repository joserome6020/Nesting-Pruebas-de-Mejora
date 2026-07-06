import os

import config
from modules.historial_jobs import norm_job_name


class EscanerServidor:
    def __init__(self):
        self.ruta_base = config.RUTA_SERVIDOR_RAIZ

    def buscar_nuevos_jobs(self, jobs_ya_procesados):
        """
        Explora el servidor buscando carpetas 'AutoDXF'.
        Retorna: (lista_jobs, mensaje_error)
        """
        jobs_encontrados = []
        historial = {norm_job_name(p) for p in (jobs_ya_procesados or [])}

        if not os.path.exists(self.ruta_base):
            return [], "No se encuentra la ruta del servidor.\nVerifique VPN o conexión LAN."

        try:
            productos = list(os.scandir(self.ruta_base))
        except OSError as e:
            return [], f"No se pudo leer el servidor:\n{e}"

        try:
            for producto in productos:
                if not producto.is_dir():
                    continue
                try:
                    clientes = list(os.scandir(producto.path))
                except OSError:
                    continue

                for cliente in clientes:
                    if not cliente.is_dir():
                        continue
                    try:
                        jobs = list(os.scandir(cliente.path))
                    except OSError:
                        continue

                    for job in jobs:
                        if not job.is_dir():
                            continue

                        ruta_autodxf = os.path.join(job.path, "MODEL CORE FILES", "AutoDXF")
                        if not os.path.isdir(ruta_autodxf):
                            continue

                        if norm_job_name(job.name) in historial:
                            continue

                        jobs_encontrados.append(
                            {
                                "job_name": job.name,
                                "cliente": cliente.name,
                                "producto": producto.name,
                                "ruta_full": ruta_autodxf,
                                "ruta_job_root": job.path,
                            }
                        )

        except Exception as e:
            return [], str(e)

        return jobs_encontrados, None
