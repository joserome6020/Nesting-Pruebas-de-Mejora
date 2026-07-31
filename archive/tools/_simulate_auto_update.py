"""Simulación en seco del flujo de auto-actualización (no hace pull ni compila)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import app_auto_update as au


def _hr(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def _check_prereqs() -> dict[str, bool | str]:
    git = shutil.which("git")
    py = shutil.which("python") or shutil.which("py")
    ps1 = (ROOT / "tools" / "arga_apply_update.ps1").is_file()
    build = (ROOT / "tools" / "build_arga_exe.py").is_file()
    return {
        "git": bool(git),
        "git_path": git or "",
        "python": bool(py),
        "python_path": py or "",
        "apply_ps1": ps1,
        "build_script": build,
        "repo_git": (ROOT / ".git").is_dir(),
    }


def _simulate_mode(label: str, *, frozen: bool, executable: Path) -> None:
    print(f"\n--- Escenario: {label} ---")
    saved_frozen = getattr(sys, "frozen", False)
    saved_exe = sys.executable
    try:
        if frozen:
            sys.frozen = True  # type: ignore[attr-defined]
        else:
            if hasattr(sys, "frozen"):
                delattr(sys, "frozen")
        sys.executable = str(executable)

        mode = au.entry_mode()
        linked = au._find_linked_repository_root()
        update_root = au._resolve_update_root()
        local = au._read_local_commit(update_root)

        print(f"  entry_mode       : {mode}")
        print(f"  sys.executable   : {executable}")
        print(f"  linked_repo      : {linked}")
        print(f"  update_root      : {update_root}")
        print(f"  local_commit     : {(local[:12] + '...') if local else '?'}")

        python_exe = ""
        if mode == "python":
            python_exe = str(Path(sys.executable).resolve())

        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(update_root / "tools" / "arga_apply_update.ps1"),
            "-ParentPid", "<PID_APP>",
            "-ProjectRoot", str(update_root),
            "-ExePath", str(update_root / "dist" / "ArgaNestingSuite.exe"),
            "-LaunchMode", mode,
        ]
        if python_exe:
            cmd.extend(["-PythonExe", python_exe])
        print(f"  comando_post_pull: {' '.join(cmd)}")
    finally:
        sys.executable = saved_exe
        if saved_frozen:
            sys.frozen = True  # type: ignore[attr-defined]
        elif hasattr(sys, "frozen"):
            delattr(sys, "frozen")


def _simulate_startup_dialog(info: au.UpdateInfo) -> None:
    _hr("PASO 1 — Arranque (~3.5 s después de abrir la app)")
    print("  La app lanza un hilo en background -> check_for_updates()")
    print(f"  repo_root      : {info.repo_root}")
    print(f"  local_commit   : {info.local_commit}")
    print(f"  remote_commit  : {info.remote_commit or '(no leído)'}")
    print(f"  resumen remoto : {info.remote_summary or '-'}")
    print(f"  has_update     : {info.has_update}")
    print(f"  can_apply      : {info.can_apply}")
    print(f"  needs_bootstrap: {info.needs_bootstrap}")
    if info.reason_blocked:
        print(f"  bloqueado      : {info.reason_blocked}")

    if not info.has_update:
        print("\n  -> Resultado: NO se muestra dialogo (ya estas al dia).")
        return

    print("\n  -> Resultado: QMessageBox «Actualizar proyecto, compilar el .exe...?»")
    print("     Si el usuario pulsa Si -> apply_update()")


def _simulate_apply_flow(info: au.UpdateInfo, *, dry_run: bool = True) -> None:
    _hr("PASO 2 — Si el usuario acepta (simulación)")
    root = Path(info.repo_root)

    steps = [
        ("2a", "_ensure_repository()", "Verifica clone Git (clona solo si falta)"),
        ("2b", "git fetch + git pull --ff-only origin main", "Descarga cambios"),
        ("2c", "Lanza arga_apply_update.ps1", "Espera cierre PID -> build -> shortcut -> abre .exe"),
        ("2d", "QApplication.quit()", "Cierra la app actual"),
    ]
    for num, action, desc in steps:
        print(f"  [{num}] {action}")
        print(f"       {desc}")

    if not info.can_apply:
        print("\n  Simulacion detenida: can_apply=False")
        return

    # Comprobar si pull fallaría por cambios locales
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        dirty = bool(str(st.stdout or "").strip())
        if dirty:
            print("\n  AVISO en ESTA PC: hay cambios locales sin commit.")
            print("    git pull --ff-only podría fallar hasta hacer commit/stash.")
            print("    En PCs de piso (solo .exe, sin edits) esto no ocurre.")
    except Exception as exc:
        print(f"\n  (no se pudo comprobar git status: {exc})")

    if dry_run:
        print("\n  [DRY-RUN] No se ejecutó pull ni PowerShell.")
        fake_pid = os.getpid()
        result_cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(root / "tools" / "arga_apply_update.ps1"),
            "-ParentPid", str(fake_pid),
            "-ProjectRoot", str(root),
            "-ExePath", str(root / "dist" / "ArgaNestingSuite.exe"),
            "-LaunchMode", au.entry_mode(),
        ]
        if au.entry_mode() == "python":
            result_cmd.extend(["-PythonExe", sys.executable])
        print("  Comando real que se lanzaría:")
        print("  " + " \\\n    ".join(result_cmd))


def main() -> int:
    _hr("SIMULACIÓN AUTO-UPDATE — ARGA NESTING SUITE")
    print(f"PC actual : {os.environ.get('COMPUTERNAME', '?')}")
    print(f"Proyecto  : {ROOT}")

    prereqs = _check_prereqs()
    _hr("Prerrequisitos")
    for k, v in prereqs.items():
        print(f"  {k}: {v}")

    _hr("Modos de arranque (3 escenarios)")
    _simulate_mode(
        "Desarrollo — python main.py",
        frozen=False,
        executable=Path(sys.executable),
    )
    _simulate_mode(
        "Producción — dist\\ArgaNestingSuite.exe dentro del clone",
        frozen=True,
        executable=ROOT / "dist" / "ArgaNestingSuite.exe",
    )
    desktop_exe = Path.home() / "Desktop" / "ArgaNestingSuite.exe"
    _simulate_mode(
        "Piso — .exe suelto en escritorio (sin .git al lado)",
        frozen=True,
        executable=desktop_exe,
    )

    state_path = au._install_state_path()
    if state_path.is_file():
        print(f"\n  install.json ({state_path}):")
        print(json.dumps(json.loads(state_path.read_text(encoding="utf-8")), indent=2))

    _hr("Check real contra GitHub")
    info = au.check_for_updates()
    _simulate_startup_dialog(info)
    _simulate_apply_flow(info, dry_run=True)

    _hr("FIN")
    if info.has_update:
        print("Hay update disponible — en la app real aparecería el diálogo.")
    else:
        print("Sin update pendiente — comportamiento normal al arrancar (silencioso).")
        print("Para probar el diálogo real hace falta un commit nuevo en origin/main.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
