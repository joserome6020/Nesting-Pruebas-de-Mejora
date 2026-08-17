"""Cliente TCP JSON-line hacia el worker remoto NvidiaSpark."""
from __future__ import annotations

import json
import socket
from typing import Any


class NestRemoteError(RuntimeError):
    pass


def _recv_line(sock: socket.socket, *, timeout_s: float) -> str:
    sock.settimeout(float(timeout_s))
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in chunk:
            break
    if not buf:
        raise NestRemoteError("remote closed connection / empty response")
    line = bytes(buf).split(b"\n", 1)[0]
    return line.decode("utf-8", errors="replace")


def remote_cmd(
    payload: dict[str, Any],
    *,
    host: str,
    port: int,
    connect_timeout_s: float = 3.0,
    timeout_s: float = 600.0,
) -> dict[str, Any]:
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    with socket.create_connection((host, int(port)), timeout=float(connect_timeout_s)) as sock:
        sock.sendall(raw)
        line = _recv_line(sock, timeout_s=timeout_s)
    try:
        resp = json.loads(line)
    except Exception as ex:
        raise NestRemoteError(f"bad remote json: {ex}") from ex
    if not isinstance(resp, dict):
        raise NestRemoteError("remote response is not an object")
    return resp


def ping_remote(
    *,
    host: str,
    port: int,
    connect_timeout_s: float = 3.0,
) -> bool:
    try:
        resp = remote_cmd(
            {"cmd": "ping"},
            host=host,
            port=port,
            connect_timeout_s=connect_timeout_s,
            timeout_s=max(5.0, float(connect_timeout_s) * 2),
        )
        return bool(resp.get("ok") and resp.get("pong"))
    except Exception:
        return False


def pack_engine_remote(
    request: dict[str, Any],
    *,
    host: str,
    port: int,
    connect_timeout_s: float = 3.0,
    timeout_s: float = 600.0,
) -> dict[str, Any]:
    """Envía un job de motor (Lite/Force/APEX/…) al worker remoto."""
    resp = remote_cmd(
        {"cmd": "pack_engine", "request": request},
        host=host,
        port=port,
        connect_timeout_s=connect_timeout_s,
        timeout_s=timeout_s,
    )
    if not resp.get("ok"):
        raise NestRemoteError(str(resp.get("error") or "remote pack_engine failed"))
    result = resp.get("result")
    if not isinstance(result, dict):
        raise NestRemoteError("remote pack_engine missing result object")
    return dict(result)


def remote_status(*, host: str, port: int, connect_timeout_s: float = 3.0) -> dict[str, Any]:
    reachable = ping_remote(host=host, port=port, connect_timeout_s=connect_timeout_s)
    info: dict[str, Any] = {
        "host": host,
        "port": int(port),
        "reachable": reachable,
    }
    if not reachable:
        return info
    try:
        ver = remote_cmd(
            {"cmd": "version"},
            host=host,
            port=port,
            connect_timeout_s=connect_timeout_s,
            timeout_s=10.0,
        )
        info["version"] = ver.get("version")
        info["ok"] = bool(ver.get("ok"))
    except Exception as ex:
        info["version_error"] = str(ex)
    return info
