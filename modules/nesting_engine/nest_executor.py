"""Router del motor activo: Local o NvidiaSpark con fallback local."""
from __future__ import annotations

import time
from typing import Any

from modules.nesting_engine.engines.types import PackSheetRequest, PackSheetResult
from modules.nesting_engine.nest_engine_job import (
    pack_request_to_dict,
    pack_result_from_dict,
)
from modules.nesting_engine.nest_remote_client import (
    NestRemoteError,
    pack_engine_remote,
    ping_remote,
    remote_status,
)
from modules.nesting_engine.nest_runtime_contract import RUNTIME_LOCAL, RUNTIME_SPARK, normalize_prefer
from modules.nesting_engine.nest_runtime_prefs import load_nest_runtime_prefs


_LAST_STATUS: dict[str, Any] = {
    "prefer": "local",
    "chosen": "local",
    "fallback_used": False,
    "detail": None,
}


def last_executor_status() -> dict[str, Any]:
    return dict(_LAST_STATUS)


def spark_endpoint(prefs: dict[str, Any] | None = None) -> tuple[str, int, float, float]:
    current = prefs or load_nest_runtime_prefs()
    spark = dict(current.get("spark") or {})
    return (
        str(spark.get("host") or "192.168.2.35"),
        int(spark.get("port") or 8765),
        float(spark.get("timeout_s") or 600.0),
        float(spark.get("connect_timeout_s") or 3.0),
    )


def health_spark(prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    host, port, _timeout_s, connect_timeout_s = spark_endpoint(prefs)
    return remote_status(host=host, port=port, connect_timeout_s=connect_timeout_s)


def _pack_engine_local(request: PackSheetRequest, *, engine_id: str) -> PackSheetResult:
    from modules.nesting_engine import engine_registry as registry

    return registry.empaquetar_una_hoja_detalle_local(request, engine_id=engine_id)


def pack_engine(
    request: PackSheetRequest,
    *,
    engine_id: str,
    prefer: str | None = None,
    prefs: dict[str, Any] | None = None,
) -> PackSheetResult:
    """Ejecuta el mismo motor en NvidiaSpark o en este PC si no responde."""
    global _LAST_STATUS
    current = prefs or load_nest_runtime_prefs()
    mode = normalize_prefer(prefer if prefer is not None else str(current.get("prefer") or "local"))
    host, port, timeout_s, connect_timeout_s = spark_endpoint(current)
    started = time.perf_counter()

    def local(*, fallback_used: bool, detail: str) -> PackSheetResult:
        result = _pack_engine_local(request, engine_id=engine_id)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        result.runtime = {
            "runtime": RUNTIME_LOCAL,
            "prefer": mode,
            "fallback_used": bool(fallback_used),
            "detail": detail,
            "engine_id": str(engine_id),
            "executor_elapsed_ms": float(elapsed_ms),
        }
        _LAST_STATUS = {
            "prefer": mode,
            "chosen": RUNTIME_LOCAL,
            "fallback_used": bool(fallback_used),
            "detail": detail,
            "engine_id": str(engine_id),
            "host": None,
        }
        return result

    if mode == "local":
        return local(fallback_used=False, detail="prefer_local")

    try:
        if not ping_remote(host=host, port=port, connect_timeout_s=connect_timeout_s):
            return local(fallback_used=True, detail=f"spark_ping_fail:{host}:{port}")

        payload = pack_request_to_dict(request, engine_id=str(engine_id))
        remote = pack_engine_remote(
            payload,
            host=host,
            port=port,
            connect_timeout_s=connect_timeout_s,
            timeout_s=timeout_s,
        )
        result = pack_result_from_dict(remote)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        existing = result.runtime if isinstance(result.runtime, dict) else {}
        runtime_name = str(existing.get("runtime") or RUNTIME_SPARK)
        result.runtime = {
            "runtime": runtime_name,
            "prefer": mode,
            "fallback_used": False,
            "host": host,
            "detail": str(existing.get("detail") or "remote_ok"),
            "engine_id": str(engine_id),
            "executor_elapsed_ms": float(elapsed_ms),
        }
        _LAST_STATUS = {
            "prefer": mode,
            "chosen": runtime_name,
            "fallback_used": False,
            "detail": "remote_ok",
            "engine_id": str(engine_id),
            "host": host,
            "port": port,
        }
        print(f"[NEST-SPARK] engine={engine_id} -> {runtime_name}@{host}:{port}", flush=True)
        return result
    except (NestRemoteError, OSError, TimeoutError, ConnectionError, ValueError) as exc:
        return local(fallback_used=True, detail=f"spark_fail:{exc}")
