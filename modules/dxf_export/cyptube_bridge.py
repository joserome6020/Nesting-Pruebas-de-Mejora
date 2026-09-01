"""
Gancho ANS → CypTube Automático (Modo B).

Tras escribir ``NESTEOS DE COBRE/cyptube_verticales.json``, lanza en segundo
plano::

    python <cyptube_main> auto-nest --nesteos-dir <NESTEOS DE COBRE> --skip-wait

La carpeta pasada es la **ruta real del export** (Nesteos Locales **o**
servidor ``\\\\192.168.2.80\\…``). El RPA debe leer/escribir CTDS ahí mismo.

Config: ``_config/cyptube_bridge.json``. Override env:
  ARGA_CYPTUBE_AUTO_NEST=0|1
  ARGA_CYPTUBE_MAIN=ruta\\main.py
  ARGA_CYPTUBE_PYTHON=ruta\\python.exe
  ARGA_CYPTUBE_DRY_RUN=1
  ARGA_CYPTUBE_LAUNCH_DELAY_S=15
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_CONFIG_RELATIVE = os.path.join("_config", "cyptube_bridge.json")

# Misma raíz que config.RUTA_SERVIDOR_RAIZ / CypTube nest_paths (fallback).
_DEFAULT_SERVER_ROOT = (
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM"
)

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "cyptube_main": r"C:\Proyectos\Cobre - CypTube\main.py",
    "python_exe": "",
    "skip_wait": True,
    "dry_run": False,
    "new_console": True,
    # Segundos antes de lanzar auto-nest (deja cerrar modales ANS sin estorbar al RPA).
    "launch_delay_s": 15.0,
    # Remapeo opcional si CypTube ve otra letra/UNC que ANS (mismo árbol).
    # Ejemplo: [{"from": "\\\\192.168.2.80\\Users\\…", "to": "Z:\\…"}]
    "path_maps": [],
}


@dataclass(frozen=True)
class CyptubeBridgeResult:
    launched: bool
    skipped: bool
    reason: str = ""
    cmd: tuple[str, ...] = ()
    pid: int | None = None
    cwd: str | None = None
    destino: str = ""  # "servidor" | "local" | "otro"
    nesteos_dir: str = ""


def _config_path() -> Path:
    try:
        import config as app_config

        return Path(app_config.asegurar_archivo_persistente(_CONFIG_RELATIVE))
    except Exception:
        return Path(__file__).resolve().parents[2] / _CONFIG_RELATIVE


def load_cyptube_bridge_prefs() -> dict[str, Any]:
    prefs = dict(_DEFAULTS)
    path = _config_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if key.startswith("_"):
                        continue
                    prefs[key] = value
        except Exception:
            pass

    env_en = (os.environ.get("ARGA_CYPTUBE_AUTO_NEST") or "").strip().lower()
    if env_en in ("0", "false", "off", "no"):
        prefs["enabled"] = False
    elif env_en in ("1", "true", "on", "yes"):
        prefs["enabled"] = True

    env_main = (os.environ.get("ARGA_CYPTUBE_MAIN") or "").strip()
    if env_main:
        prefs["cyptube_main"] = env_main

    env_py = (os.environ.get("ARGA_CYPTUBE_PYTHON") or "").strip()
    if env_py:
        prefs["python_exe"] = env_py

    env_dry = (os.environ.get("ARGA_CYPTUBE_DRY_RUN") or "").strip().lower()
    if env_dry in ("1", "true", "on", "yes"):
        prefs["dry_run"] = True
    elif env_dry in ("0", "false", "off", "no"):
        prefs["dry_run"] = False

    env_delay = (os.environ.get("ARGA_CYPTUBE_LAUNCH_DELAY_S") or "").strip()
    if env_delay:
        try:
            prefs["launch_delay_s"] = max(0.0, float(env_delay))
        except ValueError:
            pass

    prefs["enabled"] = bool(prefs.get("enabled"))
    prefs["skip_wait"] = bool(prefs.get("skip_wait", True))
    prefs["dry_run"] = bool(prefs.get("dry_run"))
    prefs["new_console"] = bool(prefs.get("new_console", True))
    try:
        prefs["launch_delay_s"] = max(0.0, float(prefs.get("launch_delay_s", 15.0)))
    except (TypeError, ValueError):
        prefs["launch_delay_s"] = 15.0
    prefs["cyptube_main"] = str(prefs.get("cyptube_main") or "").strip()
    prefs["python_exe"] = str(prefs.get("python_exe") or "").strip()
    maps = prefs.get("path_maps")
    prefs["path_maps"] = list(maps) if isinstance(maps, list) else []
    return prefs


def resolve_python_exe(prefs: dict[str, Any] | None = None) -> str:
    data = prefs if isinstance(prefs, dict) else load_cyptube_bridge_prefs()
    configured = str(data.get("python_exe") or "").strip()
    if configured and os.path.isfile(configured):
        return configured
    return sys.executable or "python"


def _norm_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path or "")))


def normalize_nesteos_dir(path: str | Path) -> str:
    """
    Normaliza sin romper UNC. Evita Path.resolve() (puede cambiar \\server a letra).
    """
    raw = str(path or "").strip()
    if not raw:
        return ""
    # UNC: conservar prefijo \\; solo normpath el resto.
    if raw.startswith("\\\\") or raw.startswith("//"):
        return os.path.normpath(raw)
    return os.path.abspath(raw)


def classify_nesteos_destino(path: str | Path) -> str:
    """Clasifica destino del export: servidor | local | otro."""
    texto = str(path or "")
    if not texto.strip():
        return "otro"
    p = _norm_key(texto)
    try:
        import config as app_config

        raiz = _norm_key(getattr(app_config, "RUTA_SERVIDOR_RAIZ", "") or "")
    except Exception:
        raiz = _norm_key(_DEFAULT_SERVER_ROOT)
    if raiz and p.startswith(raiz):
        return "servidor"
    if "192.168.2.80" in texto.replace("/", "\\"):
        return "servidor"
    if texto.startswith("\\\\") or texto.startswith("//"):
        return "servidor"
    if "nesteos locales" in p:
        return "local"
    return "otro"


def apply_path_maps(path: str, prefs: dict[str, Any] | None = None) -> str:
    """Aplica remapeos from→to (prefijos) para la PC donde corre el RPA."""
    data = prefs if isinstance(prefs, dict) else load_cyptube_bridge_prefs()
    current = normalize_nesteos_dir(path)
    for item in data.get("path_maps") or []:
        if not isinstance(item, dict):
            continue
        src = str(item.get("from") or "").strip()
        dst = str(item.get("to") or "").strip()
        if not src or not dst:
            continue
        src_n = normalize_nesteos_dir(src)
        key_cur = _norm_key(current)
        key_src = _norm_key(src_n).rstrip("\\")
        if key_cur == key_src or key_cur == key_src + "\\":
            return normalize_nesteos_dir(dst)
        if key_cur.startswith(key_src + "\\"):
            remainder = key_cur[len(key_src) :].lstrip("\\")
            joined = dst.rstrip("\\/") + "\\" + remainder.replace("/", "\\")
            return normalize_nesteos_dir(joined)
    return current


def resolve_nesteos_dir_for_rpa(
    nesteos_cobre_dir: str | Path,
    *,
    prefs: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Ruta que debe recibir CypTube auto-nest + etiqueta destino.
    Returns (ruta_para_rpa, destino).
    """
    data = prefs if isinstance(prefs, dict) else load_cyptube_bridge_prefs()
    mapped = apply_path_maps(str(nesteos_cobre_dir), data)
    return mapped, classify_nesteos_destino(mapped)


def build_auto_nest_cmd(
    nesteos_cobre_dir: str | Path,
    *,
    prefs: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    data = prefs if isinstance(prefs, dict) else load_cyptube_bridge_prefs()
    main_py = str(data.get("cyptube_main") or "").strip()
    python_exe = resolve_python_exe(data)
    nest_dir, _destino = resolve_nesteos_dir_for_rpa(nesteos_cobre_dir, prefs=data)
    cmd: list[str] = [python_exe, main_py, "auto-nest", "--nesteos-dir", nest_dir]
    if bool(data.get("skip_wait", True)):
        cmd.append("--skip-wait")
    if bool(data.get("dry_run")):
        cmd.append("--dry-run")
    return tuple(cmd)


def launch_cyptube_auto_nest(
    nesteos_cobre_dir: str | Path,
    *,
    log_fn: Callable[[str], None] | None = None,
    prefs: dict[str, Any] | None = None,
    popen: Callable[..., Any] | None = None,
) -> CyptubeBridgeResult:
    """
    Dispara CypTube auto-nest sin bloquear el export ANS.
    Funciona igual si el export fue a Nesteos Locales o al servidor 80:
    pasa la carpeta absoluta donde ANS escribió el JSON/DXF (con path_maps si hay).
    """
    log = log_fn or (lambda _m: None)
    data = prefs if isinstance(prefs, dict) else load_cyptube_bridge_prefs()

    if not bool(data.get("enabled")):
        msg = "CyPTube bridge deshabilitado (config/env)"
        log(f"[CyPTube] {msg}")
        return CyptubeBridgeResult(launched=False, skipped=True, reason=msg)

    nest_dir, destino = resolve_nesteos_dir_for_rpa(nesteos_cobre_dir, prefs=data)
    if not nest_dir:
        msg = "ruta NESTEOS DE COBRE vacía"
        log(f"[CyPTube] SKIP — {msg}")
        return CyptubeBridgeResult(
            launched=False, skipped=True, reason=msg, destino=destino
        )

    if not os.path.isdir(nest_dir):
        msg = f"NESTEOS DE COBRE no existe ({destino}): {nest_dir}"
        log(f"[CyPTube] SKIP — {msg}")
        return CyptubeBridgeResult(
            launched=False,
            skipped=True,
            reason=msg,
            destino=destino,
            nesteos_dir=nest_dir,
        )

    main_py = str(data.get("cyptube_main") or "").strip()
    if not main_py or not os.path.isfile(main_py):
        msg = f"cyptube_main no encontrado: {main_py or '(vacío)'}"
        log(f"[CyPTube] SKIP — {msg}")
        return CyptubeBridgeResult(
            launched=False,
            skipped=True,
            reason=msg,
            destino=destino,
            nesteos_dir=nest_dir,
        )

    json_path = os.path.join(nest_dir, "cyptube_verticales.json")
    if not os.path.isfile(json_path):
        msg = f"falta manifiesto: {json_path}"
        log(f"[CyPTube] SKIP — {msg}")
        return CyptubeBridgeResult(
            launched=False,
            skipped=True,
            reason=msg,
            destino=destino,
            nesteos_dir=nest_dir,
        )

    cmd = build_auto_nest_cmd(nest_dir, prefs=data)
    cwd = str(Path(main_py).resolve().parent)
    runner = popen or subprocess.Popen
    delay_s = max(0.0, float(data.get("launch_delay_s") or 0.0))

    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32" and bool(data.get("new_console", True)):
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
        kwargs["close_fds"] = True

    def _spawn() -> None:
        if delay_s > 0:
            time.sleep(delay_s)
        try:
            proc = runner(list(cmd), **kwargs)
        except Exception as exc:
            log(f"[CyPTube] ERROR — no se pudo lanzar auto-nest: {exc}")
            return
        pid = getattr(proc, "pid", None)
        dry = " (dry-run)" if bool(data.get("dry_run")) else ""
        log(
            f"[CyPTube] auto-nest lanzado{dry} destino={destino} pid={pid} — "
            f"nesteos-dir={nest_dir}"
        )
        log(f"[CyPTube] cmd: {' '.join(cmd)}")

    if delay_s > 0:
        threading.Thread(target=_spawn, name="CyPTubeAutoNest", daemon=True).start()
        log(
            f"[CyPTube] auto-nest programado en {delay_s:.0f}s "
            f"(espera cierre de ventanas ANS) destino={destino}"
        )
        log(f"[CyPTube] cmd (pendiente): {' '.join(cmd)}")
        return CyptubeBridgeResult(
            launched=True,
            skipped=False,
            reason=f"scheduled_{int(delay_s)}s",
            cmd=cmd,
            cwd=cwd,
            destino=destino,
            nesteos_dir=nest_dir,
        )

    try:
        proc = runner(list(cmd), **kwargs)
    except Exception as exc:
        msg = f"no se pudo lanzar auto-nest: {exc}"
        log(f"[CyPTube] ERROR — {msg}")
        return CyptubeBridgeResult(
            launched=False,
            skipped=True,
            reason=msg,
            cmd=cmd,
            cwd=cwd,
            destino=destino,
            nesteos_dir=nest_dir,
        )

    pid = getattr(proc, "pid", None)
    dry = " (dry-run)" if bool(data.get("dry_run")) else ""
    log(
        f"[CyPTube] auto-nest lanzado{dry} destino={destino} pid={pid} — "
        f"nesteos-dir={nest_dir}"
    )
    log(f"[CyPTube] cmd: {' '.join(cmd)}")
    return CyptubeBridgeResult(
        launched=True,
        skipped=False,
        reason="ok",
        cmd=cmd,
        pid=pid,
        cwd=cwd,
        destino=destino,
        nesteos_dir=nest_dir,
    )
