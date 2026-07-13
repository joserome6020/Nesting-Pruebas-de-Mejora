"""Actualización automática: sincroniza con origin/main (pull ff-only) y reinicia la app.

- Modo Python (desarrollo): solo git pull; no compila .exe.
- Modo .exe: pull + compilación vía tools/arga_apply_update.ps1.
- Solo avisa si origin/main va commits por delante del HEAD local.
- Si el usuario rechaza, no vuelve a preguntar por el mismo commit remoto.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GITHUB_REPO = "joserome6020/Nesting-Pruebas-de-Mejora"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_REPO}.git"
GITHUB_BRANCH = "main"
EXE_NAME = "ArgaNestingSuite.exe"
BUILD_SCRIPT = Path("tools") / "build_arga_exe.py"
MANIFEST_NAME = "arga_build_manifest.json"
APPLY_PS1 = Path("tools") / "arga_apply_update.ps1"


@dataclass
class UpdateInfo:
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


@dataclass
class UpdateResult:
    ok: bool
    message: str
    needs_restart: bool = False
    restart_exe: str = ""
    restart_cmd: list[str] | None = None
    quit_app: bool = False


def _skip_auto_update() -> bool:
    return str(os.environ.get("ARGA_SKIP_AUTO_UPDATE", "")).strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _install_dir() -> Path:
    base = str(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or "").strip()
    if not base:
        base = str(Path.home())
    return Path(base) / "ArgaNestingSuite"


def _canonical_repo_path() -> Path:
    return _install_dir() / "repository"


def _install_state_path() -> Path:
    return _install_dir() / "install.json"


def _load_install_state() -> dict:
    try:
        p = _install_state_path()
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_install_state(*, repo_root: Path, exe_path: str) -> None:
    _install_dir().mkdir(parents=True, exist_ok=True)
    state = _load_install_state()
    state["repo_root"] = str(repo_root.resolve())
    state["active_exe"] = str(exe_path)
    _install_state_path().write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_install_state(state: dict) -> None:
    _install_dir().mkdir(parents=True, exist_ok=True)
    _install_state_path().write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _mark_remote_synced(remote_commit: str) -> None:
    commit = str(remote_commit or "").strip()
    if not commit:
        return
    state = _load_install_state()
    state["last_synced_remote"] = commit
    state.pop("last_dismissed_remote", None)
    _write_install_state(state)


def dismiss_available_update(info: UpdateInfo) -> None:
    """Usuario rechazó: no volver a preguntar por el mismo commit remoto."""
    commit = str(info.remote_commit_full or "").strip()
    if not commit:
        return
    state = _load_install_state()
    state["last_dismissed_remote"] = commit
    _write_install_state(state)


def _working_tree_dirty(root: Path) -> bool:
    if not _repo_has_git(root):
        return False
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return bool(str(out.stdout or "").strip())
    except Exception:
        return False


def _commits_behind_remote(root: Path, branch: str = GITHUB_BRANCH) -> int:
    if not _repo_has_git(root):
        return 0
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if out.returncode != 0:
            return 0
        return max(0, int(str(out.stdout or "0").strip() or "0"))
    except Exception:
        return 0


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def entry_mode() -> str:
    """python | exe_in_repo | exe_standalone"""
    if not _is_frozen():
        return "python"
    if _find_linked_repository_root() is not None:
        return "exe_in_repo"
    return "exe_standalone"


def _find_linked_repository_root() -> Path | None:
    """Raíz del clone Git vinculado (main.py o .exe dentro del proyecto)."""
    if _is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        for base in (exe_dir, *exe_dir.parents):
            if (base / ".git").is_dir() and (base / "main.py").is_file():
                return base
            if (base / BUILD_SCRIPT).is_file() and (base / "main.py").is_file():
                return base
        return None

    root = Path(__file__).resolve().parents[1]
    if (root / ".git").is_dir() and (root / "main.py").is_file():
        return root
    return None


def _embedded_project_root() -> Path | None:
    return _find_linked_repository_root()


def _resolve_update_root() -> Path:
    linked = _find_linked_repository_root()
    if linked is not None:
        return linked

    state = _load_install_state()
    saved = str(state.get("repo_root") or "").strip()
    if saved:
        p = Path(saved)
        if (p / ".git").is_dir():
            return p

    canonical = _canonical_repo_path()
    if (canonical / ".git").is_dir():
        return canonical

    return canonical


def _project_root() -> Path:
    return _resolve_update_root()


def _git_available() -> bool:
    try:
        out = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.returncode == 0
    except Exception:
        return False


def _repo_has_git(root: Path) -> bool:
    return (root / ".git").is_dir()


def _read_manifest_commit(path: Path) -> str:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(data.get("git_commit") or "").strip()
    except Exception:
        pass
    return ""


def _read_local_commit(root: Path) -> str:
    linked = _find_linked_repository_root()
    if linked is not None and _repo_has_git(linked):
        root = linked

    if _repo_has_git(root):
        try:
            out = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if out.returncode == 0:
                return str(out.stdout or "").strip()
        except Exception:
            pass

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        commit = _read_manifest_commit(exe_dir / MANIFEST_NAME)
        if commit:
            return commit

    return _read_manifest_commit(root / "dist" / MANIFEST_NAME)


def _fetch_remote_via_ls_remote() -> tuple[str, str, str]:
    try:
        out = subprocess.run(
            [
                "git",
                "ls-remote",
                GITHUB_REPO_URL,
                f"refs/heads/{GITHUB_BRANCH}",
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if out.returncode != 0:
            err = (out.stderr or out.stdout or "").strip()
            hint = (
                "Git no pudo leer el remoto (repo privado). "
                "Autentique Git una vez en esta PC: GitHub Desktop, "
                "`gh auth login` o Credential Manager."
            )
            return "", "", f"{hint}\n{err}" if err else hint
        line = str(out.stdout or "").strip().split("\n", 1)[0].strip()
        if not line:
            return "", "", "Remoto vacío."
        commit = line.split()[0].strip()
        return commit, "", ""
    except Exception as exc:
        return "", "", str(exc)


def _fetch_remote_commit_via_git(root: Path) -> tuple[str, str, str]:
    if not _repo_has_git(root) or not _git_available():
        return "", "", ""
    try:
        fetch = subprocess.run(
            ["git", "fetch", "origin", GITHUB_BRANCH],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if fetch.returncode != 0:
            err = (fetch.stderr or fetch.stdout or "").strip()
            hint = (
                "No se pudo contactar origin (repo privado). "
                "Verifique Git autenticado (Credential Manager, SSH o `gh auth login`)."
            )
            return "", "", f"{hint}\n{err}" if err else hint
        rev = subprocess.run(
            ["git", "rev-parse", f"origin/{GITHUB_BRANCH}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if rev.returncode != 0:
            return "", "", "No se encontró origin/main tras git fetch."
        commit = str(rev.stdout or "").strip()
        summary = ""
        log = subprocess.run(
            ["git", "log", "-1", "--pretty=%s", f"origin/{GITHUB_BRANCH}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if log.returncode == 0:
            summary = str(log.stdout or "").strip()
        return commit, summary, ""
    except Exception as exc:
        return "", "", str(exc)


def _fetch_remote_commit_api() -> tuple[str, str]:
    token = str(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ArgaNestingSuite-Updater",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    commit = str(data.get("sha") or "").strip()
    summary = str((data.get("commit") or {}).get("message") or "").split("\n", 1)[0].strip()
    return commit, summary


def _migrate_user_files_to_repo(repo_root: Path) -> None:
    """Copia JSON/CSV persistentes junto al .exe viejo hacia la raíz del repo."""
    if not getattr(sys, "frozen", False):
        return
    src_dir = Path(sys.executable).resolve().parent
    if src_dir.resolve() == repo_root.resolve():
        return
    names = (
        "historial_jobs.json",
        "inventario_remanentes.csv",
        "herinox_sync.local.json",
    )
    for name in names:
        src = src_dir / name
        dst = repo_root / name
        try:
            if src.is_file() and not dst.is_file():
                dst.write_bytes(src.read_bytes())
        except Exception:
            pass


def _ensure_repository(
    progress: Callable[[str, float], None] | None = None,
) -> tuple[Path, str]:
    def _prog(msg: str, pct: float) -> None:
        if progress:
            try:
                progress(msg, pct)
            except Exception:
                pass

    root = _resolve_update_root()
    if _repo_has_git(root):
        return root, ""

    if not _git_available():
        return root, "Git no está instalado o no está en el PATH."

    _prog("Clonando proyecto (primera actualización automática)…", 0.08)
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists() and any(root.iterdir()):
        return root, f"La carpeta {root} existe pero no es un clone Git. Elimínela o reinstale."

    try:
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--branch",
                GITHUB_BRANCH,
                "--single-branch",
                GITHUB_REPO_URL,
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if clone.returncode != 0:
            err = (clone.stderr or clone.stdout or "").strip()
            return root, f"git clone falló:\n{err}"
    except Exception as exc:
        return root, f"Error al clonar: {exc}"

    _migrate_user_files_to_repo(root)
    return root, ""


def check_for_updates() -> UpdateInfo:
    if _skip_auto_update():
        return UpdateInfo(False, "", "", "", "", False, "ARGA_SKIP_AUTO_UPDATE activo")

    root = _resolve_update_root()
    local = _read_local_commit(root)
    needs_bootstrap = not _repo_has_git(root)

    remote, summary, git_err = _fetch_remote_commit_via_git(root)
    if not remote:
        remote, _, git_err = _fetch_remote_via_ls_remote()

    if not remote:
        try:
            remote, summary = _fetch_remote_commit_api()
            git_err = ""
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and not git_err:
                git_err = (
                    "Repo privado sin acceso API. Se requiere Git autenticado en esta PC."
                )
            elif not git_err:
                git_err = str(exc)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if not git_err:
                git_err = str(exc)

    if not remote:
        return UpdateInfo(
            False,
            local[:12] if local else "?",
            "",
            "",
            str(root),
            False,
            git_err or "No se pudo leer el commit remoto.",
            needs_bootstrap=needs_bootstrap,
        )

    commits_behind = _commits_behind_remote(root) if _repo_has_git(root) else 0
    has_update = commits_behind > 0
    if not _repo_has_git(root):
        has_update = True

    state = _load_install_state()
    dismissed = str(state.get("last_dismissed_remote") or "").strip()
    if has_update and dismissed and dismissed == remote:
        has_update = False

    can_apply = _git_available()
    blocked = ""
    if has_update and not can_apply:
        blocked = (
            "Hay una versión nueva, pero falta Git en esta PC. "
            "Instale Git for Windows y autentíquelo una sola vez."
        )
    elif has_update and _repo_has_git(root) and _working_tree_dirty(root):
        can_apply = False
        blocked = (
            "Hay cambios locales sin commit. Guarde su trabajo o haga commit "
            "antes de actualizar para que el proyecto quede igual al remoto."
        )

    return UpdateInfo(
        has_update=has_update,
        local_commit=local[:12] if local else "?",
        remote_commit=remote[:12],
        remote_summary=summary,
        repo_root=str(root),
        can_apply=can_apply,
        reason_blocked=blocked,
        needs_bootstrap=needs_bootstrap,
        remote_commit_full=remote,
        commits_behind=commits_behind,
    )


def _git_pull(root: Path, progress: Callable[[str, float], None] | None) -> UpdateResult | None:
    def _prog(msg: str, pct: float) -> None:
        if progress:
            try:
                progress(msg, pct)
            except Exception:
                pass

    _prog("Descargando actualización…", 0.25)
    try:
        fetch = subprocess.run(
            ["git", "fetch", "origin", GITHUB_BRANCH],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if fetch.returncode != 0:
            err = (fetch.stderr or fetch.stdout or "").strip()
            return UpdateResult(False, f"git fetch falló:\n{err}")

        pull = subprocess.run(
            ["git", "pull", "--ff-only", "origin", GITHUB_BRANCH],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if pull.returncode != 0:
            err = (pull.stderr or pull.stdout or "").strip()
            return UpdateResult(
                False,
                f"git pull falló (¿cambios locales sin commit?):\n{err}",
            )
    except Exception as exc:
        return UpdateResult(False, f"Error al actualizar código: {exc}")
    return None


def _launch_build_and_restart(root: Path, parent_pid: int, progress) -> UpdateResult:
    def _prog(msg: str, pct: float) -> None:
        if progress:
            try:
                progress(msg, pct)
            except Exception:
                pass

    ps1 = root / APPLY_PS1
    if not ps1.is_file():
        return UpdateResult(
            False,
            "Código actualizado, pero falta tools/arga_apply_update.ps1 para compilar el .exe.",
        )

    new_exe = str((root / "dist" / EXE_NAME).resolve())
    _save_install_state(repo_root=root, exe_path=new_exe)

    mode = entry_mode()
    python_exe = ""
    if mode == "python":
        python_exe = str(Path(sys.executable).resolve())

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1),
        "-ParentPid",
        str(int(parent_pid)),
        "-ProjectRoot",
        str(root),
        "-ExePath",
        new_exe,
        "-LaunchMode",
        mode,
    ]
    if python_exe:
        cmd.extend(["-PythonExe", python_exe])

    _prog("Preparando compilación del .exe…", 0.85)
    try:
        subprocess.Popen(
            cmd,
            cwd=str(root),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            close_fds=True,
        )
    except Exception as exc:
        return UpdateResult(False, f"No se pudo lanzar el actualizador: {exc}")

    _prog("Listo. Cerrando para compilar…", 1.0)
    if mode == "python":
        extra = (
            "\n\nTambién puede volver a abrir con:\n"
            f'  python "{root / "main.py"}"'
        )
    else:
        extra = ""
    msg = (
        "Proyecto actualizado.\n\n"
        "La aplicación se cerrará, se compilará el .exe nuevo y se abrirá "
        "automáticamente."
        f"{extra}"
    )
    return UpdateResult(True, msg, needs_restart=True, quit_app=True)


def apply_update(
    info: UpdateInfo,
    *,
    progress: Callable[[str, float], None] | None = None,
    parent_pid: int | None = None,
) -> UpdateResult:
    if not info.can_apply:
        return UpdateResult(False, info.reason_blocked or "Actualización no disponible.")

    root, err = _ensure_repository(progress)
    if err:
        return UpdateResult(False, err)

    if _repo_has_git(root) and _working_tree_dirty(root):
        return UpdateResult(
            False,
            "No se puede actualizar: hay cambios locales sin commit.\n\n"
            "Guarde o haga commit de su trabajo e intente de nuevo.",
        )

    fail = _git_pull(root, progress)
    if fail is not None:
        return fail

    synced = _read_local_commit(root)
    if synced:
        _mark_remote_synced(synced)

    mode = entry_mode()
    if mode == "python":
        main_py = (root / "main.py").resolve()
        py_exe = str(Path(sys.executable).resolve())
        if progress:
            try:
                progress("Actualización descargada.", 1.0)
            except Exception:
                pass
        return UpdateResult(
            True,
            "Proyecto actualizado.\n\n"
            "Cierre la aplicación y vuelva a abrirla para usar la versión nueva.",
            needs_restart=True,
            restart_cmd=[py_exe, str(main_py)],
        )

    pid = int(parent_pid or os.getpid())
    return _launch_build_and_restart(root, pid, progress)


def launch_restart(result: UpdateResult) -> None:
    if result.restart_exe and os.path.isfile(result.restart_exe):
        subprocess.Popen(
            [result.restart_exe],
            cwd=os.path.dirname(result.restart_exe),
            close_fds=True,
        )
        return
    if result.restart_cmd:
        subprocess.Popen(
            result.restart_cmd,
            cwd=str(_project_root()),
            close_fds=True,
        )
