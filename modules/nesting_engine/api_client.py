import json
import urllib.request
import urllib.parse

def enviar_reporte_a_api(nombre_swo, datos_resultados):
    """Envía el reporte del acomodo al servidor."""
    url_api = "http://192.168.2.80:8000/api/reportes/guardar"
    payload = {"swo": nombre_swo, "snapshot": datos_resultados}
    try:
        data_json = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url_api, data=data_json, method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=5) as response:
            respuesta = json.loads(response.read().decode('utf-8'))
            if respuesta.get("estatus") == "ok":
                print(f"[EXITO] Reporte {nombre_swo} inyectado a la Base de Datos para la Web.")
    except Exception as e:
        print(f"[ERROR] API Web: {str(e)}")


def _patch_json(url, payload_dict, timeout=5):
    """Hace un PATCH con payload JSON y retorna el código HTTP."""
    data = json.dumps(payload_dict).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='PATCH')
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode()


def _post_json(url, payload_dict, timeout=10):
    """Hace un POST con payload JSON y retorna (código HTTP, respuesta dict)."""
    data = json.dumps(payload_dict).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode('utf-8'))
        return resp.getcode(), body


def avanzar_swo_centralizado(swo_id):
    """
    Al exportar DXF/STEP desde Nesting para una SWO, marca la SWO como 'EXPORTADO'
    en la BD de Nesting (via CentralizedSystem API) para que la tarjeta pase a 
    'Maxima Optimizacion - Finalizado' en el dashboard.
    
    Endpoint: POST /nesting/swo/auto-advance {"swo_id": "SWO-001"}
    """
    base_url = "http://192.168.2.80:8003"
    url = f"{base_url}/nesting/swo/auto-advance"
    
    try:
        print(f"[CENTRALIZED] Avanzando SWO '{swo_id}' a EXPORTADO (Maxima Optimizacion Finalizado)...")
        code, body = _post_json(url, {"swo_id": str(swo_id).strip()})
        
        if code in (200, 201):
            msg = body.get("mensaje", "OK")
            print(f"[CENTRALIZED] SWO '{swo_id}' -> EXPORTADO. {msg}")
            return True
        else:
            print(f"[CENTRALIZED] Error al avanzar SWO '{swo_id}'. Codigo: {code}")
            return False
    except Exception as e:
        print(f"[CENTRALIZED ERROR] Fallo al avanzar SWO '{swo_id}': {str(e)}")
        return False


def avanzar_job_centralizado(job_number):
    """
    Al exportar DXF/STEP desde Nesting, marca el job en "Ingeniería Finalizado"
    en el dashboard (tarjeta verde) para que el admin pueda fusionarlo en una SWO.

    Casos manejados:
    - Job en 'pending'  -> lo mueve a 'inventor' primero, luego lo marca como finalizado
    - Job en 'inventor' -> lo marca directamente como finalizado
    - Job en otra etapa -> se omite para no retroceder el flujo
    """
    base_url = "http://192.168.2.80:8003"

    # 1. Buscar el job por número
    url_get = f"{base_url}/jobs/by-number/{urllib.parse.quote(str(job_number).strip())}"
    try:
        req_get = urllib.request.Request(url_get, method='GET')
        with urllib.request.urlopen(req_get, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            job_id = data.get("id")
            job_status = (data.get("status") or "").strip().lower()

        if not job_id:
            print(f"[CENTRALIZED] Job '{job_number}' no encontrado en CentralizedSystem.")
            return False

        print(f"[CENTRALIZED] Job '{job_number}' encontrado -> id={job_id}, status='{job_status}'")

        # 2. Si está más avanzado que 'inventor', no tocarlo
        if job_status not in ("pending", "inventor"):
            print(f"[CENTRALIZED] Job '{job_number}' ya esta en etapa '{job_status}'. Se omite el avance.")
            return False

        # 3. Si está en 'pending', primero moverlo a 'inventor' (Ingeniería En Proceso)
        if job_status == "pending":
            print(f"[CENTRALIZED] Moviendo Job '{job_number}' de pending -> inventor...")
            code = _patch_json(
                f"{base_url}/jobs/{job_id}/status",
                {"status": "inventor"}
            )
            if code not in (200, 201):
                print(f"[CENTRALIZED] No se pudo mover a inventor. Codigo: {code}")
                return False
            print(f"[CENTRALIZED] Job '{job_number}' movido a Ingenieria En Proceso.")

        # 4. Marcar la etapa de Ingeniería como FINALIZADA (tarjeta verde)
        print(f"[CENTRALIZED] Marcando Job '{job_number}' como Ingenieria Finalizado...")
        code = _patch_json(
            f"{base_url}/jobs/{job_id}/complete",
            {}
        )
        if code in (200, 201):
            print(f"[CENTRALIZED] Job '{job_number}' -> Ingenieria Finalizado. Listo para fusion SWO.")
            return True
        else:
            print(f"[CENTRALIZED] Error al marcar como finalizado. Codigo: {code}")
            return False

    except Exception as e:
        print(f"[CENTRALIZED ERROR] Fallo al procesar job '{job_number}': {str(e)}")
        return False


def trigger_po_contpaq(nombre_swo):
    """
    Trigger: InsertaPOContPaq — Se llama cuando se exporta DXF de una SWO.
    Crea la Orden de Compra en ContPAQ/PostgreSQL para la SWO exportada.
    API en Docker: 192.168.2.80:8006/run
    """
    url = "http://192.168.2.80:8006/run"
    try:
        print(f"[PO-CONTPAQ] Disparando PO para SWO '{nombre_swo}'...")
        code, body = _post_json(url, {"SUPER_WORK_ORDER": str(nombre_swo).strip()}, timeout=30)
        if code in (200, 201):
            print(f"[PO-CONTPAQ] PO creada exitosamente para SWO '{nombre_swo}'. Tiempo: {body.get('execution_time', '?')}s")
            return True
        else:
            print(f"[PO-CONTPAQ] Error al crear PO para SWO '{nombre_swo}'. Codigo: {code}")
            return False
    except Exception as e:
        print(f"[PO-CONTPAQ][ERROR] Fallo al crear PO para SWO '{nombre_swo}': {str(e)}")
        return False


def trigger_pedido_po(job_number):
    """
    Trigger: creaPedidoPO — Se llama cuando se exporta DXF de un Job.
    Crea el Pedido en ContPAQ/PostgreSQL para el Job y sus WOs.
    API en Docker: 192.168.2.80:8005/crearPedido
    """
    url = "http://192.168.2.80:8005/crearPedido/"
    try:
        print(f"[PEDIDO-PO] Disparando Pedido para Job '{job_number}'...")
        code, body = _post_json(url, {"jobNumber": str(job_number).strip()}, timeout=30)
        if code in (200, 201):
            print(f"[PEDIDO-PO] Pedido creado exitosamente para Job '{job_number}'. Datos: {body.get('datosCreados', [])}")
            return True
        else:
            print(f"[PEDIDO-PO] Error al crear pedido para Job '{job_number}'. Codigo: {code}")
            return False
    except Exception as e:
        print(f"[PEDIDO-PO][ERROR] Fallo al crear pedido para Job '{job_number}': {str(e)}")
        return False