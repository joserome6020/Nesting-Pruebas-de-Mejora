"""Auto-update por canal de release (sin git ni rebuild en cliente).

Flujo:
  1) `check_for_updates()` descarga `latest.json` del canal
     (`ARGA_NEST_CHANNEL_URL` o GitHub Releases del repo por defecto).
  2) Compara `latest.version` contra el `arga_build_manifest.json` que
     viaja dentro de la carpeta del .exe.
  3) `apply_update(info)` descarga el zip, verifica sha256, lo extrae a
     `%LOCALAPPDATA%\\ArgaNestingSuite\\app\\<version>\\` y marca
     `pending_switch` en `install.json`.
  4) Al cerrar la app, lanza `tools/arga_apply_switch.ps1` que espera
     el cierre del PID, reemplaza la junction `app\\current` por la
     versión nueva y relanza el .exe.

Compat de API pública (usada por `interface/qt/main_window.py`):
  - dataclasses: `UpdateInfo`, `UpdateResult`
  - funciones : `check_for_updates`, `apply_update`,
                `dismiss_available_update`, `launch_restart`, `entry_mode`

Modo desarrollo (`python main.py`): `check_for_updates()` reporta que no
hay update (el canal es para .exe). Para simular canal en dev, exportar
`ARGA_NEST_CHANNEL_URL` a un latest.json local o remoto.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    import config as _cfg
except Exception:  # pragma: no cover — solo si el módulo se importa sin repo
    _cfg = None  # type: ignore[assignment]


# --------------------------------------------------------------------- Config

APP_NAME = "ArgaNestingSuite"
GITHUB_REPO = os.environ.get("ARGA_NEST_GITHUB_REPO") or "joserome6020/Nesting-Pruebas-de-Mejora"
DEFAULT_CHANNEL_URL = (
    f"https://github.com/{GITHUB_REPO}/releases/latest/download/latest.json"
)
CHANNEL_URL = str(os.environ.get("ARGA_NEST_CHANNEL_URL") or DEFAULT_CHANNEL_URL).strip()
MANIFEST_NAME = "arga_build_manifest.json"
INSTALL_JSON = "install.json"
APPLY_SWITCH_PS1 = Path("tools") / "arga_apply_switch.ps1"
DOWNLOAD_TIMEOUT_SEC = 300
LATEST_TIMEOUT_SEC = 25
KEEP_PREVIOUS_VERSIONS = 2  # además de la activa


# --------------------------------------------------------------------- Dataclasses

@dataclass
class UpdateInfo:
    """Compat con la API previa. Semántica adaptada al canal-release:
    - `local_commit`   : versión instalada (`YYYY.MM.DD[.N]`), no commit.
    - `remote_commit`  : versión del canal (idem).
    - `remote_commit_full`: commit hash publicado (para dismiss).
    - `commits_behind` : 1 si hay update, 0 si no (compat con UI).
    - `repo_root`      : install_root de esta PC.
    """
    has_update: bool
    local_commit: str
    remote_commit: str
    remote_summary: str
    repo_root: str
    can_apply: bool
    reason_blocked: str = ""
    needs_bootstrap: bool = False
    remote_commit_full: str = ""
    commits_behind: int = 0
    # Campos nuevos, opcionales (no rompen consumers viejos).
    download_url: str = ""
    sha256: str = ""
    size_bytes: int = 0
    filename: str = ""
    min_supported_version: str = ""


@dataclass
class UpdateResult:
    ok: bool
    message: str
    needs_restart: bool = False
    restart_exe: str = ""
    restart_cmd: list[str] | None = None
    quit_app: bool = False
    new_version: str = ""
    new_version_dir: str = ""


# --------------------------------------------------------------------- Helpers

def _skip_auto_update() -> bool:
    return str(os.environ.get("ARGA_SKIP_AUTO_UPDATE", "")).strip().lower() in (
        "1", "true", "yes", "si", "on",
    )


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _local_appdata_root() -> str:
    for env in ("LOCALAPPDATA", "APPDATA"):
        v = str(os.environ.get(env) or "").strip()
        if v:
            return v
    return os.path.expanduser("~")


def _install_root() -> Path:
    """Instalación por-usuario. En dev cae a la raíz del repo (no persiste)."""
    if _cfg is not None:
        try:
            return Path(_cfg.install_root())
        except Exception:
            pass
    if _is_frozen():
        return Path(_local_appdata_root()) / APP_NAME
    # Dev: usa la raíz del repo (útil si alguien fuerza canal en dev).
    return Path(__file__).resolve().parents[1]


def _install_state_path() -> Path:
    return _install_root() / INSTALL_JSON


def _load_install_state() -> dict:
    p = _install_state_path()
    try:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_install_state(**patch) -> None:
    state = _load_install_state()
    state.update({k: v for k, v in patch.items() if v is not None})
    p = _install_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _exe_dir() -> Path:
    try:
        return Path(sys.executable).resolve().parent
    except Exception:
        return _install_root() / "app" / "current"


def _current_app_dir() -> Path:
    """Directorio de la versión activa. En frozen = dirname(exe)."""
    if _is_frozen():
        return _exe_dir()
    # Dev fallback: `app/current` si existe, si no repo root (solo diagnóstico).
    guess = _install_root() / "app" / "current"
    if guess.is_dir():
        return guess
    return Path(__file__).resolve().parents[1]


def _read_local_manifest() -> dict:
    m = _current_app_dir() / MANIFEST_NAME
    try:
        if m.is_file():
            return json.loads(m.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _local_version() -> tuple[str, str]:
    """(version, commit) del manifest local. Vacío si no hay."""
    data = _read_local_manifest()
    return (
        str(data.get("version") or "").strip(),
        str(data.get("git_commit") or "").strip(),
    )


def _version_key(ver: str) -> tuple[int, ...]:
    """Convierte '2026.08.13[.N]' a tupla comparable. Vacío = (0,)."""
    parts = [p for p in (ver or "").strip().split(".") if p.isdigit()]
    return tuple(int(p) for p in parts) if parts else (0,)


def _version_gt(a: str, b: str) -> bool:
    return _version_key(a) > _version_key(b)


def entry_mode() -> str:
    """Compat: 'python' | 'exe_in_repo' | 'exe_standalone'."""
    if not _is_frozen():
        return "python"
    exe = Path(sys.executable).resolve()
    for base in (exe.parent, *exe.parent.parents):
        if (base / "main.py").is_file() and (base / ".git").is_dir():
            return "exe_in_repo"
    return "exe_standalone"


# --------------------------------------------------------------------- Fetch

def _fetch_json(url: str, timeout: int) -> dict:
    headers = {
        "User-Agent": f"{APP_NAME}-Updater",
        "Accept": "application/json",
    }
    token = str(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _read_channel_latest(url: str) -> tuple[dict, str]:
    """Devuelve (latest_dict, error_msg). Soporta http(s) y rutas locales/UNC."""
    if not url:
        return {}, "ARGA_NEST_CHANNEL_URL vacío y sin default configurado."
    parsed = url.lower()
    try:
        if parsed.startswith(("http://", "https://")):
            data = _fetch_json(url, LATEST_TIMEOUT_SEC)
            return data, ""
        # UNC / local
        p = Path(url)
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8")), ""
        return {}, f"Canal no accesible: {url}"
    except urllib.error.HTTPError as exc:
        return {}, f"HTTP {exc.code} al leer canal: {exc.reason}"
    except urllib.error.URLError as exc:
        return {}, f"No se pudo contactar canal: {exc.reason}"
    except Exception as exc:
        return {}, f"Error leyendo canal ({url}): {exc}"


# --------------------------------------------------------------------- Check

def check_for_updates() -> UpdateInfo:
    if _skip_auto_update():
        return UpdateInfo(False, "", "", "", str(_install_root()), False,
                          "ARGA_SKIP_AUTO_UPDATE activo")

    if not _is_frozen():
        # En dev (`python main.py`) el update no aplica.
        local_ver, _ = _local_version()
        return UpdateInfo(False, local_ver or "dev", "", "", str(_install_root()),
                          False,
                          "Modo desarrollo: el canal solo aplica al .exe.")

    latest, err = _read_channel_latest(CHANNEL_URL)
    local_ver, local_commit = _local_version()
    if err:
        return UpdateInfo(False, local_ver, "", "", str(_install_root()),
                          False, err, needs_bootstrap=(not local_ver))
    remote_ver = str(latest.get("version") or "").strip()
    if not remote_ver:
        return UpdateInfo(False, local_ver, "", "", str(_install_root()),
                          False, "latest.json sin campo 'version'.",
                          needs_bootstrap=(not local_ver))

    dl_url = str(latest.get("url") or "").strip()
    sha256 = str(latest.get("sha256") or "").strip()
    size = int(latest.get("size_bytes") or 0)
    filename = str(latest.get("filename") or "").strip()
    notes = str(latest.get("notes") or "").strip()
    remote_commit = str(latest.get("commit") or "").strip()
    min_supp = str(latest.get("min_supported_version") or "").strip()

    has_update = _version_gt(remote_ver, local_ver) if local_ver else True

    # Dismiss: si el usuario rechazó este mismo release, no volver a molestar.
    state = _load_install_state()
    dismissed = str(state.get("last_dismissed_version") or "").strip()
    if has_update and dismissed and dismissed == remote_ver:
        has_update = False

    # Update forzado si local < min_supported_version.
    forced = False
    if min_supp and local_ver and _version_gt(min_supp, local_ver):
        has_update = True
        forced = True

    can_apply = True
    reason = ""
    if has_update:
        if not dl_url:
            can_apply = False
            reason = "latest.json no tiene 'url' del zip publicado."
        elif not sha256:
            can_apply = False
            reason = "latest.json no tiene 'sha256' — publicación incompleta."
        elif forced:
            reason = (
                f"Versión mínima soportada = {min_supp}. Se requiere actualizar."
            )

    return UpdateInfo(
        has_update=has_update,
        local_commit=local_ver or "?",
        remote_commit=remote_ver,
        remote_summary=notes,
        repo_root=str(_install_root()),
        can_apply=can_apply,
        reason_blocked=reason,
        needs_bootstrap=(not local_ver),
        remote_commit_full=remote_commit,
        commits_behind=1 if has_update else 0,
        download_url=dl_url,
        sha256=sha256,
        size_bytes=size,
        filename=filename,
        min_supported_version=min_supp,
    )


def dismiss_available_update(info: UpdateInfo) -> None:
    """Guarda que el usuario rechazó esta versión (por versión, no por commit)."""
    ver = (info.remote_commit or "").strip()
    if ver:
        _save_install_state(last_dismissed_version=ver)


# --------------------------------------------------------------------- Apply

def _sha256_stream(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _download(url: str, dst: Path, progress: Callable[[str, float], None] | None,
              expected_size: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    headers = {"User-Agent": f"{APP_NAME}-Updater"}
    token = str(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token and "github.com" in url.lower():
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SEC) as resp:
        total = int(resp.headers.get("Content-Length") or expected_size or 0)
        written = 0
        last_pct = -1.0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if total > 0 and progress:
                    pct = 0.25 + 0.55 * min(1.0, written / total)
                    if pct - last_pct > 0.01:
                        try:
                            progress(f"Descargando… {written // (1 << 20)} MB", pct)
                        except Exception:
                            pass
                        last_pct = pct
    tmp.rename(dst)


def _extract_zip(zip_path: Path, dst_dir: Path,
                 progress: Callable[[str, float], None] | None) -> None:
    if dst_dir.exists():
        shutil.rmtree(dst_dir, ignore_errors=True)
    dst_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, mode="r") as zf:
        members = zf.infolist()
        n = len(members)
        for i, m in enumerate(members):
            zf.extract(m, dst_dir)
            if progress and n:
                pct = 0.80 + 0.15 * ((i + 1) / n)
                try:
                    progress(f"Extrayendo {i+1}/{n}", pct)
                except Exception:
                    pass


def _prune_old_versions(app_dir: Path, keep: int, active: str) -> None:
    """Conserva las `keep` versiones más nuevas + la activa; borra el resto."""
    if not app_dir.is_dir():
        return
    versions: list[Path] = []
    for p in app_dir.iterdir():
        if p.is_dir() and p.name != "current" and (p / MANIFEST_NAME).is_file():
            versions.append(p)
    versions.sort(key=lambda p: _version_key(p.name.split("-", 1)[0]))
    to_keep = set(v.name for v in versions[-keep:])
    to_keep.add(active)
    for v in versions:
        if v.name in to_keep:
            continue
        try:
            shutil.rmtree(v, ignore_errors=True)
        except Exception:
            pass


def apply_update(
    info: UpdateInfo,
    *,
    progress: Callable[[str, float], None] | None = None,
    parent_pid: int | None = None,
) -> UpdateResult:
    if not info.has_update:
        return UpdateResult(False, "No hay actualización pendiente.")
    if not info.can_apply:
        return UpdateResult(False, info.reason_blocked or "Actualización no disponible.")
    if not _is_frozen():
        return UpdateResult(
            False,
            "Este canal es para el .exe. En dev usa `git pull` y `python main.py`.",
        )
    if not info.download_url or not info.sha256 or not info.filename:
        return UpdateResult(False, "latest.json incompleto: falta url/sha256/filename.")

    root = _install_root()
    updates_dir = root / "updates"
    app_dir = root / "app"
    updates_dir.mkdir(parents=True, exist_ok=True)
    app_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        try:
            progress("Preparando descarga…", 0.10)
        except Exception:
            pass

    zip_path = updates_dir / info.filename
    try:
        _download(info.download_url, zip_path, progress, info.size_bytes)
    except Exception as exc:
        return UpdateResult(False, f"Error descargando release: {exc}")

    if progress:
        try:
            progress("Verificando integridad…", 0.82)
        except Exception:
            pass
    actual_sha = _sha256_stream(zip_path)
    if actual_sha.lower() != info.sha256.lower():
        try:
            zip_path.unlink()
        except Exception:
            pass
        return UpdateResult(
            False,
            "sha256 no coincide. La descarga puede estar corrupta o el canal fue alterado.\n"
            f"esperado: {info.sha256}\nactual  : {actual_sha}",
        )

    # Nombre de la carpeta versionada: 'YYYY.MM.DD-<commit_short>'
    commit_short = (info.remote_commit_full or "")[:8] or "nogit000"
    new_dir_name = f"{info.remote_commit}-{commit_short}"
    new_dir = app_dir / new_dir_name
    try:
        _extract_zip(zip_path, new_dir, progress)
    except Exception as exc:
        try:
            shutil.rmtree(new_dir, ignore_errors=True)
        except Exception:
            pass
        return UpdateResult(False, f"Error extrayendo release: {exc}")

    # Sentinel .ok: solo se materializa si la extracción terminó completa.
    try:
        (new_dir / ".ok").write_text(
            json.dumps(
                {
                    "version": info.remote_commit,
                    "commit": info.remote_commit_full,
                    "extracted_at_utc": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        return UpdateResult(False, f"No se pudo marcar la versión como lista: {exc}")

    _save_install_state(
        pending_switch=new_dir_name,
        pending_switch_dir=str(new_dir.resolve()),
        pending_since_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        channel_url=CHANNEL_URL,
    )

    # Lanzar el ps1 que hace el swap + relanza el .exe.
    result = _launch_apply_switch(new_dir, parent_pid=parent_pid, progress=progress)
    # Best-effort: purga versiones viejas (deja la activa + N más recientes).
    try:
        _prune_old_versions(app_dir, KEEP_PREVIOUS_VERSIONS, active=new_dir_name)
    except Exception:
        pass
    return result


def _launch_apply_switch(
    new_dir: Path,
    *,
    parent_pid: int | None,
    progress: Callable[[str, float], None] | None,
) -> UpdateResult:
    ps1_candidates = [
        _current_app_dir() / APPLY_SWITCH_PS1,
        _current_app_dir() / "tools" / "arga_apply_switch.ps1",
        _current_app_dir() / "arga_apply_switch.ps1",
    ]
    ps1 = next((p for p in ps1_candidates if p.is_file()), None)
    if ps1 is None:
        # Sin ps1 el swap tiene que hacerlo el launcher; dejamos pending y avisamos.
        return UpdateResult(
            True,
            "Descarga y verificación OK. Cierra la aplicación para aplicar la "
            "nueva versión en el próximo arranque.",
            needs_restart=True,
            quit_app=True,
            new_version=str(new_dir.name),
            new_version_dir=str(new_dir.resolve()),
        )

    install_root = str(_install_root().resolve())
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1),
        "-ParentPid",
        str(int(parent_pid or os.getpid())),
        "-InstallRoot",
        install_root,
        "-NewVersionDir",
        str(new_dir.resolve()),
    ]
    if progress:
        try:
            progress("Cerrando para aplicar versión nueva…", 0.98)
        except Exception:
            pass
    try:
        subprocess.Popen(
            cmd,
            cwd=install_root,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            close_fds=True,
        )
    except Exception as exc:
        return UpdateResult(
            True,
            "Descarga verificada, pero no se pudo lanzar el swap automático. "
            f"Reinicia manualmente para aplicar la nueva versión.\n\n{exc}",
            needs_restart=True,
            quit_app=True,
            new_version=str(new_dir.name),
            new_version_dir=str(new_dir.resolve()),
        )
    return UpdateResult(
        True,
        "Nueva versión descargada. La aplicación se cerrará, aplicará la "
        "actualización y volverá a abrirse automáticamente.",
        needs_restart=True,
        quit_app=True,
        new_version=str(new_dir.name),
        new_version_dir=str(new_dir.resolve()),
    )


def launch_restart(result: UpdateResult) -> None:
    """Compat: la UI llama esto tras aceptar reinicio. Con canal-release el
    proceso de swap ya está en marcha (subprocess ps1); aquí sólo aseguramos
    el relanzamiento manual si no se lanzó el ps1.
    """
    if result.restart_exe and os.path.isfile(result.restart_exe):
        try:
            subprocess.Popen([result.restart_exe], close_fds=True)
        except Exception:
            pass
        return
    if result.restart_cmd:
        try:
            subprocess.Popen(result.restart_cmd, close_fds=True)
        except Exception:
            pass
