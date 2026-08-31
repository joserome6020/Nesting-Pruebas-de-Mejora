import json
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass
from pathlib import Path

CENTRALIZED_BASE_URL = "http://192.168.2.80:8003"
CONTPAQ_PO_SWO_URL = "http://192.168.2.80:8006/run"
CONTPAQ_PO_VALIDATE_URL = "http://192.168.2.80:8006/validate"
CONTPAQ_PO_WO_URL = "http://192.168.2.80:8005/crearPedido/"
WEB_REPORTE_URL = "http://192.168.2.80:8000/api/reportes/guardar"

# Reintentos ante timeouts/caídas momentáneas de red (muy comunes en LAN).
_API_RETRIES = 3
_API_RETRY_SLEEP_S = 1.25


@dataclass(frozen=True)
class ApiOperationResult:
    """Resultado detallado y compatible con comprobaciones booleanas."""

    ok: bool
    operation: str
    target: str
    detail: str = ""
    http_status: int | None = None
    response: dict | None = None

    def __bool__(self) -> bool:
        return self.ok

    def summary(self) -> str:
        status = f" HTTP {self.http_status}" if self.http_status is not None else ""
        detail = f": {self.detail}" if self.detail else ""
        return f"{self.operation} [{self.target}]{status}{detail}"


def _http_ok(code: int | None) -> bool:
    """Todo 2xx confirma que el servidor aceptó y procesó la operación."""
    try:
        return 200 <= int(code or 0) < 300
    except (TypeError, ValueError):
        return False


def _respuesta_idempotente(code: int | None, body: dict | None) -> bool:
    """Un 409 explícitamente duplicado confirma que la orden ya fue creada."""
    if int(code or 0) != 409:
        return False
    texto = json.dumps(body or {}, ensure_ascii=False).lower()
    return any(
        token in texto
        for token in (
            "already exists",
            "already created",
            "ya existe",
            "ya fue creada",
            "duplicad",
        )
    )


def _json_export_safe(value):
    """Convierte geometría de Shapely y contenedores de UI a JSON estable."""
    geo = getattr(value, "__geo_interface__", None)
    if isinstance(geo, dict):
        return geo
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return tolist()
    if isinstance(value, set):
        return sorted(value, key=str)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, urllib.error.URLError)):
        return True
    if isinstance(exc, urllib.error.HTTPError) and int(getattr(exc, "code", 0) or 0) >= 500:
        return True
    msg = str(exc or "").lower()
    return any(
        token in msg
        for token in (
            "timed out",
            "timeout",
            "temporarily unavailable",
            "connection reset",
            "connection refused",
            "broken pipe",
            "remote end closed",
        )
    )


def _with_retries(operation_label: str, fn, *, retries: int = _API_RETRIES):
    """Ejecuta fn() con reintentos ante fallos de red/5xx."""
    last_exc: Exception | None = None
    attempts = max(1, int(retries or 1))
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts or not _is_retryable_http_error(exc):
                raise
            print(
                f"[CENTRALIZED][RETRY] {operation_label}: intento {attempt}/{attempts} "
                f"falló ({exc}); reintentando…"
            )
            time.sleep(_API_RETRY_SLEEP_S * attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{operation_label}: sin resultado")


def enviar_reporte_a_api(nombre_swo, datos_resultados):
    """Envía el reporte consolidado del acomodo al servidor web."""
    url_api = WEB_REPORTE_URL
    swo = str(nombre_swo or "").strip()
    payload = {"swo": nombre_swo, "snapshot": datos_resultados}

    def _once():
        data_json = json.dumps(payload, default=_json_export_safe).encode("utf-8")
        req = urllib.request.Request(url_api, data=data_json, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=12) as response:
            code = response.getcode()
            raw = response.read().decode("utf-8")
            respuesta = json.loads(raw) if raw.strip() else {}
            if _http_ok(code) and (code == 204 or respuesta.get("estatus") == "ok"):
                print(f"[EXITO] Reporte {nombre_swo} inyectado a la Base de Datos para la Web.")
                return ApiOperationResult(
                    True, "reporte web", swo, "Reporte confirmado.", code, respuesta
                )
            detail = str(respuesta.get("mensaje") or respuesta or "Respuesta sin estatus OK")
            return ApiOperationResult(False, "reporte web", swo, detail, code, respuesta)

    try:
        return _with_retries(f"reporte web {swo}", _once)
    except Exception as exc:
        print(f"[ERROR] API Web: {exc}")
        return ApiOperationResult(False, "reporte web", swo, str(exc))


def _patch_json(url, payload_dict, timeout=8, *, incluir_respuesta: bool = False):
    """Hace PATCH JSON; opcionalmente conserva el cuerpo para verificación."""

    def _once():
        data = json.dumps(payload_dict).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="PATCH")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw.strip() else {}
            return (code, body) if incluir_respuesta else code

    return _with_retries(f"PATCH {url}", _once)


def _post_json(url, payload_dict, timeout=15):
    """Hace un POST con payload JSON y retorna (código HTTP, respuesta dict)."""

    def _once():
        data = json.dumps(payload_dict).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                body = json.loads(raw) if raw.strip() else {}
                return resp.getcode(), body
        except urllib.error.HTTPError as err:
            # 409 puede ser una confirmación idempotente de un pedido que ya
            # existe. El llamador decide si el cuerpo la reconoce como tal.
            raw = err.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                body = {"raw": raw}
            return err.code, body

    return _with_retries(f"POST {url}", _once)


def preflight_servicios_centralizados(*, es_swo: bool = False) -> ApiOperationResult:
    """
    Verifica antes de exportar que VSM responda y, para una SWO, que ContPAQ
    también esté disponible. Las WO normales no generan una PO.
    """
    try:
        jobs = _listar_jobs_centralizado(timeout=8)
        if not isinstance(jobs, list):
            return ApiOperationResult(
                False,
                "preflight VSM",
                CENTRALIZED_BASE_URL,
                "El endpoint /jobs no devolvió una lista válida.",
            )
    except Exception as exc:
        return ApiOperationResult(
            False,
            "preflight VSM",
            CENTRALIZED_BASE_URL,
            f"No responde CentralizedSystem: {exc}",
        )

    if not es_swo:
        return ApiOperationResult(
            True,
            "preflight centralizado",
            CENTRALIZED_BASE_URL,
            f"VSM OK ({len(jobs)} jobs). ContPAQ no aplica a una WO normal.",
        )

    po_url = CONTPAQ_PO_SWO_URL
    try:
        req = urllib.request.Request(po_url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                _ = resp.getcode()
        except urllib.error.HTTPError as err:
            # 404/405/422 siguen demostrando que el servicio HTTP está vivo.
            if int(err.code or 0) >= 500:
                return ApiOperationResult(
                    False,
                    "preflight ContPAQ",
                    po_url,
                    f"ContPAQ respondió HTTP {err.code}.",
                    err.code,
                )
    except Exception as exc:
        return ApiOperationResult(
            False,
            "preflight ContPAQ",
            po_url,
            f"No responde ContPAQ: {exc}",
        )

    contrato = verificar_contrato_contpaq()
    if not contrato:
        return contrato

    return ApiOperationResult(
        True,
        "preflight centralizado",
        CENTRALIZED_BASE_URL,
        f"VSM OK ({len(jobs)} jobs) y ContPAQ con contrato de equivalencias activo.",
    )


def avanzar_swo_centralizado(swo_id):
    """
    Al exportar DXF/STEP desde Nesting para una SWO, marca la SWO como 'EXPORTADO'
    en la BD de Nesting (via CentralizedSystem API) para que la tarjeta pase a
    'Maxima Optimizacion - Finalizado' en el dashboard.

    Endpoint: POST /nesting/swo/auto-advance {"swo_id": "SWO-001"}
    """
    url = f"{CENTRALIZED_BASE_URL}/nesting/swo/auto-advance"
    swo = str(swo_id or "").strip()

    try:
        print(f"[CENTRALIZED] Avanzando SWO '{swo}' a EXPORTADO (Maxima Optimizacion Finalizado)...")
        code, body = _post_json(url, {"swo_id": swo}, timeout=20)

        if _http_ok(code):
            msg = body.get("mensaje", "OK")
            print(f"[CENTRALIZED] SWO '{swo}' -> EXPORTADO. {msg}")
            return ApiOperationResult(True, "avance VSM SWO", swo, str(msg), code, body)

        print(f"[CENTRALIZED] Error al avanzar SWO '{swo}'. Codigo: {code}")
        return ApiOperationResult(False, "avance VSM SWO", swo, str(body), code)
    except Exception as exc:
        print(f"[CENTRALIZED ERROR] Fallo al avanzar SWO '{swo}': {exc}")
        return ApiOperationResult(False, "avance VSM SWO", swo, str(exc))


def _get_json(url, timeout=8):
    def _once():
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return _with_retries(f"GET {url}", _once)


def verificar_contrato_contpaq() -> ApiOperationResult:
    """
    Rechaza imágenes InsertaPO antiguas antes de persistir CAD o avanzar VSM.

    El contrato de equivalencias es obligatorio para que los largos usen
    ``codigo_contpaq`` y no vuelvan a consultar el código Herinox como SKU.
    """
    base_url = CONTPAQ_PO_SWO_URL.rsplit("/", 1)[0]
    openapi_url = f"{base_url}/openapi.json"
    requeridos = {
        "/validate": "POST",
        "/catalog/verify-codes": "POST",
        "/catalog/search": "POST",
    }
    try:
        specification = _get_json(openapi_url, timeout=8)
    except Exception as exc:
        return ApiOperationResult(
            False,
            "contrato InsertaPO",
            openapi_url,
            f"No se pudo leer OpenAPI de InsertaPO: {exc}",
        )

    paths = specification.get("paths") if isinstance(specification, dict) else None
    if not isinstance(paths, dict):
        return ApiOperationResult(
            False,
            "contrato InsertaPO",
            openapi_url,
            "OpenAPI de InsertaPO no contiene rutas válidas.",
        )

    faltantes = [
        f"{method} {path}"
        for path, method in requeridos.items()
        if method.lower() not in (paths.get(path) or {})
    ]
    if faltantes:
        return ApiOperationResult(
            False,
            "contrato InsertaPO",
            openapi_url,
            "La imagen activa de InsertaPO es anterior al soporte de "
            f"equivalencias Herinox→ContPAQ; faltan {', '.join(faltantes)}. "
            "Despliegue la imagen actual con docker compose up -d --build "
            "--force-recreate insertapo.",
        )

    return ApiOperationResult(
        True,
        "contrato InsertaPO",
        openapi_url,
        "Contrato de equivalencias Herinox→ContPAQ confirmado.",
    )


def verificar_codigos_contpaq(
    codigos: list[str],
    *,
    catalog_url: str | None = None,
    timeout: int = 45,
) -> dict[str, dict]:
    """
    Consulta ContPAQi (InsertaPO) para saber si un código existe y su descripción.

    Usado para match directo Herinox==ContPAQi cuando no hay equivalencia distinta.
    """
    limpios = sorted(
        {
            str(c or "").strip().upper()
            for c in (codigos or [])
            if str(c or "").strip()
        }
    )
    if not limpios:
        return {}
    base = (catalog_url or CONTPAQ_PO_SWO_URL).rstrip("/").rsplit("/", 1)[0]
    url = f"{base}/catalog/verify-codes"
    code, body = _post_json(url, {"codigos": limpios}, timeout=timeout)
    if not _http_ok(code) or not isinstance(body, dict):
        raise RuntimeError(
            f"Catálogo ContPAQi HTTP {code}: {body!r}"
        )
    resultados = body.get("results") or []
    return {
        str(item.get("codigo") or "").strip().upper(): dict(item)
        for item in resultados
        if item and str(item.get("codigo") or "").strip()
    }


def _listar_jobs_centralizado(timeout=8):
    return _get_json(f"{CENTRALIZED_BASE_URL}/jobs", timeout=timeout) or []


def _leer_job_centralizado(job_number: str) -> dict | None:
    job = str(job_number or "").strip()
    if not job:
        return None
    url = (
        f"{CENTRALIZED_BASE_URL}/jobs/by-number/"
        f"{urllib.parse.quote(job)}"
    )
    try:
        data = _get_json(url, timeout=8)
        return data if isinstance(data, dict) else None
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise


def _tokens_job_centralizado(job_number: str) -> set[str]:
    raw = str(job_number or "").strip().upper()
    if not raw:
        return set()
    parts = re.split(r"[^A-Z0-9]+", raw)
    return {p for p in parts if len(p) >= 4}


def resolver_job_centralizado(job_number: str) -> tuple[str | None, dict | None]:
    """
    Resuelve el job_number del VSM cuando el nesting usa otro alias de carpeta.
    Ej.: nesting `06-30-2275TANK25325` -> VSM `06-30-2275TANK_HEADIRON25325`.
    """
    job_in = str(job_number or "").strip()
    if not job_in:
        return None, None

    url_get = (
        f"{CENTRALIZED_BASE_URL}/jobs/by-number/"
        f"{urllib.parse.quote(job_in)}"
    )
    try:
        data = _get_json(url_get)
        resolved = str(data.get("job_number") or job_in).strip()
        return resolved, data
    except urllib.error.HTTPError as err:
        if err.code != 404:
            raise

    tokens = _tokens_job_centralizado(job_in)
    if not tokens:
        return None, None

    candidatos: list[tuple[int, dict]] = []
    for item in _listar_jobs_centralizado():
        if not isinstance(item, dict):
            continue
        numero = str(item.get("job_number") or "").strip()
        if not numero:
            continue
        local_path = str(item.get("local_path") or "").replace("\\", "/").upper()
        numero_up = numero.upper()
        score = 0
        if job_in.upper() in numero_up or numero_up in job_in.upper():
            score += 8
        if job_in.upper() in local_path:
            score += 12
        score += sum(2 for tok in tokens if tok in numero_up)
        score += sum(3 for tok in tokens if tok in local_path)
        if score > 0:
            candidatos.append((score, item))

    if not candidatos:
        return None, None

    candidatos.sort(key=lambda x: (-x[0], str(x[1].get("job_number") or "")))
    mejor_score = candidatos[0][0]
    mejores = [item for score, item in candidatos if score == mejor_score]
    numeros_mejores = {
        str(item.get("job_number") or "").strip().upper()
        for item in mejores
        if str(item.get("job_number") or "").strip()
    }
    if len(numeros_mejores) != 1:
        print(
            f"[CENTRALIZED][ERROR] Alias VSM ambiguo para '{job_in}': "
            f"{sorted(numeros_mejores)}"
        )
        return None, None

    mejor = mejores[0]
    resolved = str(mejor.get("job_number") or "").strip()
    if resolved and resolved.upper() != job_in.upper():
        print(
            f"[CENTRALIZED] Job '{job_in}' resuelto como '{resolved}' "
            f"(alias VSM)."
        )
    return resolved or None, mejor


def _job_ingenieria_finalizada(data: dict | None) -> bool:
    """
    Reconoce la finalización con el mismo contrato que usa VSM.

    Algunas instalaciones mantienen ``status='inventor'`` aun después de
    completar Ingeniería y exponen el avance mediante flags/fechas/etapa. Por
    eso ``status`` nunca puede ser la única señal de verificación.
    """
    if not isinstance(data, dict):
        return False

    for key in (
        "engineering_completed",
        "engineering_finished",
        "is_engineering_complete",
        "ingenieria_finalizada",
        "completed",
        "is_completed",
    ):
        if data.get(key) is True:
            return True

    for key in (
        "engineering_completed_at",
        "engineering_finished_at",
        "completed_engineering_at",
        "ingenieria_finalizada_at",
        "completed_at",
    ):
        if data.get(key):
            return True

    # El JobRead de VSM representa los DXF pendientes de Ingeniería con
    # ``dxf_count``. La operación /complete los consume y el tablero muestra
    # Ingeniería Finalizado aunque el status base permanezca ``inventor``.
    # No inferimos nada cuando el campo no viene en la respuesta.
    if "dxf_count" in data:
        try:
            if int(data.get("dxf_count")) == 0:
                return True
        except (TypeError, ValueError):
            pass

    stage = str(data.get("stage") or data.get("current_stage") or "").strip().lower()
    if stage in {
        "engineering_complete",
        "engineering_completed",
        "ingenieria_finalizada",
        "nesting",
        "fusion",
        "production",
        "exported",
        "completed",
    }:
        return True

    status = str(data.get("status") or "").strip().lower()
    # Una etapa posterior no se debe retroceder ni volver a completar.
    return bool(status and status not in {"pending", "inventor"})


def _job_parece_finalizado_ingenieria(status: str) -> bool:
    """Compatibilidad para consumidores que todavía pasan solo ``status``."""
    return _job_ingenieria_finalizada({"status": status})


def avanzar_job_centralizado(job_number):
    """
    Al exportar DXF/STEP desde Nesting, marca el job en "Ingeniería Finalizado"
    en el dashboard (tarjeta verde) para que el admin pueda fusionarlo en una SWO.

    Casos manejados:
    - Job en 'pending'  -> lo mueve a 'inventor' primero, luego lo marca como finalizado
    - Job en 'inventor' -> lo marca directamente como finalizado
    - Job en otra etapa -> se conserva (idempotente) sin retroceder el flujo
    """
    base_url = CENTRALIZED_BASE_URL
    job_in = str(job_number or "").strip()

    try:
        resolved, data = resolver_job_centralizado(job_in)
        if not data or not resolved:
            print(f"[CENTRALIZED] Job '{job_in}' no encontrado en CentralizedSystem.")
            return ApiOperationResult(
                False, "avance VSM Job", job_in, "Job no encontrado."
            )

        job_id = data.get("id")
        job_status = (data.get("status") or "").strip().lower()
        job_number = resolved

        if not job_id:
            print(f"[CENTRALIZED] Job '{job_number}' no encontrado en CentralizedSystem.")
            return ApiOperationResult(
                False, "avance VSM Job", str(job_number).strip(), "Job sin id VSM."
            )

        print(f"[CENTRALIZED] Job '{job_number}' encontrado -> id={job_id}, status='{job_status}'")

        if not job_status:
            return ApiOperationResult(
                False, "avance VSM Job", str(job_number).strip(), "Estado VSM vacío."
            )

        if _job_ingenieria_finalizada(data):
            print(
                f"[CENTRALIZED] Job '{job_number}' ya está en etapa "
                f"'{job_status}'. Se conserva sin retroceder."
            )
            return ApiOperationResult(
                True,
                "avance VSM Job",
                str(job_number).strip(),
                f"Ya estaba en estado '{job_status}'.",
            )

        if job_status == "pending":
            print(f"[CENTRALIZED] Moviendo Job '{job_number}' de pending -> inventor...")
            code = _patch_json(
                f"{base_url}/jobs/{job_id}/status",
                {"status": "inventor"},
            )
            if not _http_ok(code):
                return ApiOperationResult(
                    False,
                    "avance VSM Job",
                    str(job_number).strip(),
                    "No se pudo mover de pending a inventor.",
                    code,
                )
            print(f"[CENTRALIZED] Job '{job_number}' movido a Ingenieria En Proceso.")

        print(f"[CENTRALIZED] Marcando Job '{job_number}' como Ingenieria Finalizado...")
        complete_result = _patch_json(
            f"{base_url}/jobs/{job_id}/complete",
            {},
            incluir_respuesta=True,
        )
        if isinstance(complete_result, tuple):
            code, complete_body = complete_result
        else:
            # Compatibilidad con mocks/implementaciones anteriores.
            code, complete_body = complete_result, {}
        if not _http_ok(code):
            return ApiOperationResult(
                False,
                "avance VSM Job",
                str(job_number).strip(),
                "No se pudo completar ingeniería.",
                code,
            )

        # La respuesta del contrato de /complete es JobRead. Si contiene
        # metadatos de tablero, es la evidencia más fresca de la mutación.
        if _job_ingenieria_finalizada(complete_body):
            return ApiOperationResult(
                True,
                "avance VSM Job",
                str(job_number).strip(),
                "Ingeniería finalizada según respuesta de /complete.",
                code,
                complete_body,
            )

        # Verificación de lectura: no confiar solo en el HTTP del PATCH. VSM
        # puede tardar en replicar la tarjeta; usamos backoff y señales de
        # finalización, no solamente ``status``.
        verificado = None
        for intento in range(5):
            time.sleep(0.5 * (intento + 1))
            verificado = _leer_job_centralizado(job_number)
            if _job_ingenieria_finalizada(verificado):
                break

        if not _job_ingenieria_finalizada(verificado):
            # /complete respondió 2xx, por lo que VSM aceptó la mutación. La
            # API /by-number de algunas versiones solo publica el status base
            # ('inventor') y no la subetapa del tablero. Reportamos el estado
            # como confirmado con observación para no abortar ContPAQ por un
            # falso negativo de lectura.
            snapshot = json.dumps(verificado or {}, ensure_ascii=False, default=str)
            print(
                "[CENTRALIZED][WARN] VSM aceptó /complete pero la lectura "
                f"aún no expone señal de finalización para '{job_number}': {snapshot}"
            )
            return ApiOperationResult(
                True,
                "avance VSM Job",
                str(job_number).strip(),
                "PATCH confirmado; VSM no expone aún la subetapa de Ingeniería "
                "en /jobs/by-number.",
                code,
                complete_body,
            )

        status_final = str((verificado or {}).get("status") or "").strip().lower()

        print(
            f"[CENTRALIZED] Job '{job_number}' -> Ingenieria Finalizado "
            f"(verificado status='{status_final}')."
        )
        return ApiOperationResult(
            True,
            "avance VSM Job",
            str(job_number).strip(),
            f"Ingeniería finalizada (status='{status_final}').",
            code,
            complete_body or verificado,
        )

    except Exception as exc:
        print(f"[CENTRALIZED ERROR] Fallo al procesar job '{job_number}': {exc}")
        return ApiOperationResult(
            False, "avance VSM Job", str(job_number or job_in).strip(), str(exc)
        )


def nombre_reporte_po(body: dict | None) -> str:
    """InsertaPO devuelve nombreReporte vacío si el correo/PDF no se envió."""
    if not isinstance(body, dict):
        return ""
    return str(body.get("nombreReporte") or body.get("nombre_reporte") or "").strip()


# Compat interno (tests / imports previos).
_nombre_reporte_po = nombre_reporte_po


def trigger_po_contpaq(nombre_swo):
    """
    Trigger: InsertaPOContPaq — Se llama cuando se exporta DXF de una SWO.
    Crea la Orden de Compra en ContPAQ/PostgreSQL para la SWO exportada.
    API en Docker: 192.168.2.80:8006/run

    Importante (SWO-047 / GAM 13040): InsertaPO puede devolver HTTP 200 con la
    OC creada y ``nombreReporte`` vacío si falló el SMTP. ``ok=True`` porque la
    PO ya existe (no re-disparar ``/run``); use ``correo_po_confirmado`` para
    alertar y reenviar el PDF sin duplicar la OC.
    """
    url = CONTPAQ_PO_SWO_URL
    swo = str(nombre_swo or "").strip()
    try:
        print(f"[PO-CONTPAQ] Disparando PO para SWO '{swo}'...")
        code, body = _post_json(url, {"SUPER_WORK_ORDER": swo}, timeout=45)
        if _http_ok(code) or _respuesta_idempotente(code, body):
            nombre = _nombre_reporte_po(body if isinstance(body, dict) else None)
            tiempo = (body or {}).get("execution_time", "?") if isinstance(body, dict) else "?"
            if not nombre:
                print(
                    f"[PO-CONTPAQ][WARN] PO ContPAQ OK para SWO '{swo}' "
                    f"(t={tiempo}s) pero nombreReporte vacío: el correo NO se envió."
                )
            else:
                print(
                    f"[PO-CONTPAQ] PO confirmada para SWO '{swo}'. "
                    f"Tiempo: {tiempo}s reporte='{nombre}'"
                )
            return ApiOperationResult(
                True,
                "pedido ContPAQ SWO",
                swo,
                str(body),
                code,
                body if isinstance(body, dict) else None,
            )
        print(f"[PO-CONTPAQ] Error al crear PO para SWO '{swo}'. Codigo: {code}")
        return ApiOperationResult(False, "pedido ContPAQ SWO", swo, str(body), code, body)
    except Exception as exc:
        print(f"[PO-CONTPAQ][ERROR] Fallo al crear PO para SWO '{swo}': {exc}")
        return ApiOperationResult(False, "pedido ContPAQ SWO", swo, str(exc))


def correo_po_confirmado(resultado: ApiOperationResult | None) -> bool:
    """True solo si InsertaPO devolvió nombreReporte (PDF/correo confirmado)."""
    if resultado is None or not resultado.ok:
        return False
    return bool(_nombre_reporte_po(resultado.response))


def validar_po_contpaq(nombre_swo):
    """Valida catálogo y equivalencias de una SWO sin crear una OC."""
    swo = str(nombre_swo or "").strip()
    try:
        print(f"[PO-CONTPAQ] Validando PO para SWO '{swo}' sin crear documento...")
        contrato = verificar_contrato_contpaq()
        if not contrato:
            return ApiOperationResult(
                False,
                "preflight ContPAQ SWO",
                swo,
                contrato.detail,
                contrato.http_status,
                contrato.response,
            )
        code, body = _post_json(
            CONTPAQ_PO_VALIDATE_URL,
            {"SUPER_WORK_ORDER": swo},
            timeout=45,
        )
        if _http_ok(code):
            detalle = str((body or {}).get("detail") or body or "Validación OK")
            print(f"[PO-CONTPAQ] Validación confirmada para SWO '{swo}': {detalle}")
            return ApiOperationResult(
                True,
                "preflight ContPAQ SWO",
                swo,
                detalle,
                code,
                body,
            )
        detalle = str((body or {}).get("detail") or body or "Validación rechazada.")
        print(f"[PO-CONTPAQ] Validación rechazada para SWO '{swo}': {detalle}")
        return ApiOperationResult(
            False,
            "preflight ContPAQ SWO",
            swo,
            detalle,
            code,
            body,
        )
    except Exception as exc:
        print(f"[PO-CONTPAQ][ERROR] Validación falló para SWO '{swo}': {exc}")
        return ApiOperationResult(False, "preflight ContPAQ SWO", swo, str(exc))


def trigger_pedido_po(job_number):
    """
    Las WO normales no generan pedido ContPAQ.

    La compra se consolida únicamente en una SWO mediante
    ``trigger_po_contpaq`` después de validar placas y MRL de largos.
    """
    job_in = str(job_number or "").strip()
    detalle = (
        "PO ContPAQ omitida intencionalmente para WO normal; "
        "solo las SWO generan una OC GAM consolidada."
    )
    print(f"[PEDIDO-PO] Job '{job_in}': {detalle}")
    return ApiOperationResult(
        True,
        "pedido ContPAQ WO omitido",
        job_in,
        detalle,
        response={"po_scope": "SWO_ONLY", "omitted": True},
    )
