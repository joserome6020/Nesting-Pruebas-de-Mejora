"""Cliente IPC para ArgaNestWorker.exe (proceso aislado).

Activación: ARGA_NEST_WORKER=1
Strict: ARGA_NEST_WORKER_STRICT=1 → no caer a in-process si falla el worker.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_PROC: subprocess.Popen[str] | None = None
_PROC_LOCK = threading.Lock()
_LAST_ERROR: str | None = None


def default_worker_exe() -> Path:
    env = (os.environ.get("ARGA_NEST_WORKER_EXE") or "").strip()
    if env:
        return Path(env)
    candidates: list[Path] = []
    try:
        import config as app_config

        for root in app_config.app_search_roots():
            base = Path(root)
            candidates.append(base / "ArgaNestWorker.exe")
            candidates.append(base / "native" / "bin" / "ArgaNestWorker.exe")
            candidates.append(
                base / "native" / "ArgaNestCore" / "build" / "Release" / "ArgaNestWorker.exe"
            )
    except Exception:
        pass
    candidates.extend(
        [
            _ROOT / "ArgaNestWorker.exe",
            _ROOT / "native" / "bin" / "ArgaNestWorker.exe",
            _ROOT / "native" / "ArgaNestCore" / "build" / "Release" / "ArgaNestWorker.exe",
        ]
    )
    seen: set[str] = set()
    for c in candidates:
        key = str(c.resolve()) if c.exists() else str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.is_file():
            return c
    return candidates[0] if candidates else (_ROOT / "native" / "bin" / "ArgaNestWorker.exe")


def worker_env_requested() -> bool:
    v = (os.environ.get("ARGA_NEST_WORKER") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def worker_strict() -> bool:
    v = (os.environ.get("ARGA_NEST_WORKER_STRICT") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def worker_available() -> bool:
    return default_worker_exe().is_file()


def worker_enabled() -> bool:
    return worker_env_requested() and worker_available()


def worker_status() -> dict[str, Any]:
    exe = default_worker_exe()
    return {
        "env_ARGA_NEST_WORKER": worker_env_requested(),
        "exe": str(exe),
        "exe_exists": exe.is_file(),
        "active": worker_enabled(),
        "strict": worker_strict(),
        "last_error": _LAST_ERROR,
        "alive": _PROC is not None and _PROC.poll() is None,
    }


def _ensure_proc() -> subprocess.Popen[str]:
    global _PROC, _LAST_ERROR
    with _PROC_LOCK:
        if _PROC is not None and _PROC.poll() is None:
            return _PROC
        exe = default_worker_exe()
        if not exe.is_file():
            raise FileNotFoundError(f"ArgaNestWorker no encontrado: {exe}")
        _PROC = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(_ROOT),
        )
        _LAST_ERROR = None
        return _PROC


def close_worker() -> None:
    global _PROC
    with _PROC_LOCK:
        if _PROC is None:
            return
        try:
            if _PROC.poll() is None and _PROC.stdin:
                _PROC.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                _PROC.stdin.flush()
        except Exception:
            pass
        try:
            _PROC.terminate()
        except Exception:
            pass
        _PROC = None


def worker_cmd(payload: dict[str, Any], *, timeout_s: float = 600.0) -> dict[str, Any]:
    """Envía un comando JSON-line y espera respuesta."""
    global _LAST_ERROR
    proc = _ensure_proc()
    assert proc.stdin and proc.stdout
    line_holder: list[str] = []
    err_holder: list[BaseException] = []

    def _read():
        try:
            line_holder.append(proc.stdout.readline())
        except BaseException as ex:  # noqa: BLE001
            err_holder.append(ex)

    try:
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    except Exception as ex:
        _LAST_ERROR = str(ex)
        close_worker()
        raise

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout=float(timeout_s))
    if t.is_alive():
        _LAST_ERROR = f"worker timeout after {timeout_s}s"
        close_worker()
        raise TimeoutError(_LAST_ERROR)
    if err_holder:
        _LAST_ERROR = str(err_holder[0])
        close_worker()
        raise RuntimeError(_LAST_ERROR)
    line = line_holder[0] if line_holder else ""
    if not line:
        _LAST_ERROR = "worker returned empty response"
        close_worker()
        raise RuntimeError(_LAST_ERROR)
    try:
        return json.loads(line)
    except Exception as ex:
        _LAST_ERROR = f"bad worker json: {ex}"
        raise


def pack_sheet_via_worker(request: dict[str, Any], *, timeout_s: float = 600.0) -> dict[str, Any]:
    resp = worker_cmd({"cmd": "pack_sheet", "request": request}, timeout_s=timeout_s)
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error") or "worker pack_sheet failed")
    return dict(resp.get("result") or {})


def pack_job_via_worker(request: dict[str, Any], *, timeout_s: float = 900.0) -> dict[str, Any]:
    resp = worker_cmd({"cmd": "pack_job", "request": request}, timeout_s=timeout_s)
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error") or "worker pack_job failed")
    return dict(resp.get("result") or {})


def export_dxf_via_worker(request: dict[str, Any], *, timeout_s: float = 600.0) -> str:
    resp = worker_cmd({"cmd": "export_dxf", "request": request}, timeout_s=timeout_s)
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error") or "worker export_dxf failed")
    return str(resp.get("dxf") or "")


def ping_worker() -> bool:
    try:
        return bool(worker_cmd({"cmd": "ping"}, timeout_s=10.0).get("pong"))
    except Exception as ex:
        global _LAST_ERROR
        _LAST_ERROR = str(ex)
        return False
