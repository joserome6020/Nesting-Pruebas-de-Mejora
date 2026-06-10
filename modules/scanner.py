import os
import config

class EscanerServidor:
    def __init__(self):
        self.ruta_base = config.RUTA_SERVIDOR_RAIZ

    def buscar_nuevos_jobs(self, jobs_ya_procesados):
        """
        Explora el servidor buscando carpetas 'AutoDXF'.
        Retorna: (lista_jobs, mensaje_error)
        """
        jobs_encontrados = []
        
        # 1. Validación inicial
        if not os.path.exists(self.ruta_base):
            return [], "No se encuentra la ruta del servidor.\nVerifique VPN o conexión LAN."

        try:
            # 2. Navegación Jerárquica: Producto -> Cliente -> Job
            # Usamos os.scandir para mayor rendimiento
            for producto in os.scandir(self.ruta_base):
                if not producto.is_dir(): continue
                
                for cliente in os.scandir(producto.path):
                    if not cliente.is_dir(): continue
                    
                    for job in os.scandir(cliente.path):
                        if not job.is_dir(): continue
                        
                        # RUTA OBJETIVO
                        ruta_autodxf = os.path.join(job.path, "MODEL CORE FILES", "AutoDXF")
                        
                        # VALIDACIONES
                        # 1. Existe la carpeta AutoDXF
                        # 2. No está en el historial
                        if os.path.exists(ruta_autodxf):
                            if job.path not in jobs_ya_procesados:
                                info = {
                                    "job_name": job.name,
                                    "cliente": cliente.name,
                                    "producto": producto.name,
                                    "ruta_full": ruta_autodxf,
                                    "ruta_job_root": job.path
                                }
                                jobs_encontrados.append(info)
                                
        except Exception as e:
            return [], str(e)

        return jobs_encontrados, None