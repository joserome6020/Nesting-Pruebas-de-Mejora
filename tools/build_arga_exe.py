import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "main.py"
ICON_PNG = ROOT / "assets" / "branding" / "logo_icon1.png"
SPLASH_JPEG = ROOT / "grupo_arga_cover.jpeg"
MACRO = ROOT / "generador_verde.FCMacro"


def _run(cmd, cwd=None):
    print(f"[RUN] {' '.join(str(x) for x in cmd)}")
    subprocess.check_call(cmd, cwd=str(cwd or ROOT))


def _release_dist_exe(exe_path: Path) -> bool:
    """
    Libera la ruta destino del .exe para que PyInstaller pueda sobrescribirla.
    Si el ejecutable esta en uso, intenta renombrarlo en lugar de borrarlo.
    """
    if not exe_path.exists():
        return True
    try:
        exe_path.unlink()
        return True
    except Exception:
        pass

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = exe_path.with_name(f"{exe_path.stem}.prev_{stamp}{exe_path.suffix}")
    try:
        if backup.exists():
            backup.unlink()
    except Exception:
        pass
    try:
        exe_path.rename(backup)
        print(f"[WARN] EXE en uso; renombrado a: {backup}")
        return True
    except Exception as exc:
        print(
            f"[ERROR] No se pudo liberar {exe_path}. "
            f"Cierra ArgaNestingSuite.exe y vuelve a compilar. Detalle: {exc}"
        )
        return False


def _build_ico_file() -> Path | None:
    """Crea un .ico robusto para Windows desde branding disponible."""
    src = ICON_PNG if ICON_PNG.exists() else (SPLASH_JPEG if SPLASH_JPEG.exists() else None)
    if src is None:
        print("[WARN] No se encontró imagen de branding para icono.")
        return None
    out = ROOT / "build" / "arga_icon.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.open(src).convert("RGBA")
        sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(out, format="ICO", sizes=sizes)
        print(f"[OK] Icono .ico generado: {out}")
        return out
    except Exception as e:
        print(f"[WARN] No se pudo generar .ico desde {src}: {e}")
        return None


def _pip_install(package):
    _run([sys.executable, "-m", "pip", "install", "--upgrade", package])


def ensure_build_dependencies():
    for pkg in (
        "pip",
        "setuptools",
        "wheel",
        "pyinstaller",
        "pillow",
        "customtkinter",
        "matplotlib",
        "ezdxf",
        "shapely",
        "psycopg2-binary",
    ):
        _pip_install(pkg)


def build_exe(name: str, onefile: bool = True):
    if not MAIN_PY.exists():
        raise FileNotFoundError(f"No existe main.py: {MAIN_PY}")

    if (ROOT / "build").exists():
        shutil.rmtree(ROOT / "build", ignore_errors=True)
    if onefile:
        onefile_exe = ROOT / "dist" / f"{name}.exe"
        if not _release_dist_exe(onefile_exe):
            raise PermissionError(f"No se pudo liberar la ruta del ejecutable: {onefile_exe}")
    else:
        if (ROOT / "dist" / name).exists():
            shutil.rmtree(ROOT / "dist" / name, ignore_errors=True)

    # Generar icono DESPUÉS de limpiar build, para que no se elimine antes del --icon.
    icon_ico = _build_ico_file()

    add_datas = []
    if SPLASH_JPEG.exists():
        add_datas += ["--add-data", f"{SPLASH_JPEG};."]
    if MACRO.exists():
        add_datas += ["--add-data", f"{MACRO};."]
    if (ROOT / "modules").exists():
        add_datas += ["--add-data", f"{ROOT / 'modules'};modules"]
    if (ROOT / "interface").exists():
        add_datas += ["--add-data", f"{ROOT / 'interface'};interface"]
    if (ROOT / "assets").exists():
        add_datas += ["--add-data", f"{ROOT / 'assets'};assets"]

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noconsole",
        "--onefile" if onefile else "--onedir",
        "--name",
        name,
        "--paths",
        str(ROOT),
        "--paths",
        str(ROOT / "interface"),
        "--paths",
        str(ROOT / "modules"),
        "--hidden-import",
        "config",
        "--hidden-import",
        "tab_files",
        "--hidden-import",
        "tab_parts",
        "--hidden-import",
        "tab_sheets",
        "--hidden-import",
        "tab_nesting",
        "--hidden-import",
        "nesting_canvas",
        "--hidden-import",
        "responsive_layout",
        "--hidden-import",
        "postgres_connector",
        "--hidden-import",
        "utils_nesting",
        "--hidden-import",
        "nesting_modals",
        "--hidden-import",
        "modules.nesting_engine.manager",
        "--hidden-import",
        "modules.nesting_engine.algorithm",
        "--hidden-import",
        "modules.consulta_herinox_bridge",
        "--hidden-import",
        "modules.nesting_engine.efficiency_metrics",
        "--hidden-import",
        "modules.nesting_engine.rtz_overlays",
        "--hidden-import",
        "modules.nesting_engine.geometry_parser",
        "--hidden-import",
        "modules.nesting_engine.exporter",
        "--hidden-import",
        "reporte_pdf_nesting",
        "--hidden-import",
        "modules.sheets_manager",
        "--hidden-import",
        "modules.processed_layers",
        "--hidden-import",
        "modules.scanner",
        "--hidden-import",
        "modules.nest_exporter",
    ]
    if icon_ico and icon_ico.exists():
        cmd += ["--icon", str(icon_ico)]
    cmd += add_datas
    cmd += [str(MAIN_PY)]

    _run(cmd, cwd=ROOT)
    exe_path = (ROOT / "dist" / f"{name}.exe") if onefile else (ROOT / "dist" / name / f"{name}.exe")
    print(f"[OK] EXE generado: {exe_path}")
    if not onefile:
        print("[WARN] Modo onedir: no mover solo el .exe; debe mantenerse junto a su carpeta completa.")
    return exe_path


def _ensure_build_import_path():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def sync_repo_plates_from_herinox(phase: str) -> bool:
    """Sincroniza modules/Plates.xlsx contra Herinox antes o despues del empaquetado."""
    _ensure_build_import_path()
    from modules.plates_inventory import repo_plates_path, sync_herinox_and_align_dist

    repo_plates = repo_plates_path(ROOT)
    try:
        resultado, _ = sync_herinox_and_align_dist(ROOT, mirror_to_dist=False)
        if resultado.ok:
            print(
                f"[OK] [{phase}] Plates.xlsx sincronizado con Herinox via {resultado.source} "
                f"(coincidencias={resultado.matched_codes}, filas_actualizadas={resultado.updated_rows})"
            )
            return True
        print(f"[WARN] [{phase}] Sync Herinox omitido: {resultado.message}")
    except Exception as exc:
        print(f"[WARN] [{phase}] No se pudo sincronizar Plates.xlsx con Herinox: {exc}")
    return repo_plates.exists()


def align_plates_inventory_after_build(exe_path: Path):
    """
    Espeja modules/Plates.xlsx hacia dist/modules/Plates.xlsx para que repo y
    runtime del EXE queden exactamente iguales.
    """
    _ensure_build_import_path()
    from modules.plates_inventory import (
        dist_plates_path,
        mirror_repo_to_dist,
        plates_files_are_identical,
        repo_plates_path,
    )

    repo_plates = repo_plates_path(ROOT)
    dist_plates = dist_plates_path(ROOT)

    try:
        mirrored = mirror_repo_to_dist(ROOT)
        if mirrored:
            print(f"[OK] Inventario espejado repo->dist: {mirrored}")
    except Exception as exc:
        print(f"[WARN] No se pudo espejar Plates.xlsx a dist: {exc}")
        if repo_plates.exists() and not dist_plates.exists():
            try:
                dist_plates.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo_plates, dist_plates)
                print(f"[OK] Plates.xlsx sembrado en runtime: {dist_plates}")
            except Exception as seed_exc:
                print(f"[WARN] No se pudo sembrar Plates.xlsx en runtime: {seed_exc}")

    if plates_files_are_identical(ROOT):
        print(f"[OK] Inventario alineado: {repo_plates} == {dist_plates}")
    else:
        print(
            f"[WARN] Inventario repo/dist no quedo identico tras build. "
            f"repo={repo_plates} dist={dist_plates}"
        )


def associate_extensions(exe_path: Path):
    if os.name != "nt":
        print("[WARN] Asociación automática solo soportada en Windows.")
        return
    exe = str(exe_path.resolve())
    cmd = f"\"{exe}\" \"%1\""
    # Requiere permisos del usuario para HKCU (no admin).
    _run(["reg", "add", r"HKCU\Software\Classes\.arganest", "/ve", "/d", "ArgaNesting.Workspace", "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\.navanest", "/ve", "/d", "ArgaNesting.Workspace", "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\ArgaNesting.Workspace", "/ve", "/d", "Arga Nest Workspace", "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\ArgaNesting.Workspace\DefaultIcon", "/ve", "/d", exe, "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\ArgaNesting.Workspace\shell\open\command", "/ve", "/d", cmd, "/f"])
    print("[OK] Asociación .arganest/.navanest configurada para el usuario actual.")


def refresh_windows_icon_cache():
    if os.name != "nt":
        return
    # Refresco no destructivo de íconos para que Windows aplique el nuevo icono/progid.
    try:
        _run(["ie4uinit.exe", "-show"])
    except Exception:
        pass
    try:
        _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "$sig='[DllImport(\"shell32.dll\")]public static extern void SHChangeNotify(int wEventId,uint uFlags,IntPtr dwItem1,IntPtr dwItem2);';"
                "$t=Add-Type -MemberDefinition $sig -Name Win32 -Namespace PInvoke -PassThru;"
                "$t::SHChangeNotify(0x08000000,0,[IntPtr]::Zero,[IntPtr]::Zero)",
            ]
        )
    except Exception:
        pass
    print("[OK] Solicitud de refresh de icon cache enviada a Windows.")


def create_shortcuts(exe_path: Path, app_name: str = "ArgaNestingSuite"):
    if os.name != "nt":
        return
    # En muchas PCs corporativas el "Desktop" real está redirigido (OneDrive/idioma del SO).
    # Usamos la carpeta especial de Windows y además cubrimos rutas comunes como fallback.
    desktop_candidates = []
    try:
        ps_get_desktop = (
            "$W=New-Object -ComObject WScript.Shell;"
            "$W.SpecialFolders('Desktop')"
        )
        special_desktop = subprocess.check_output(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_get_desktop],
            cwd=str(ROOT),
            text=True,
        ).strip()
        if special_desktop:
            desktop_candidates.append(Path(special_desktop))
    except Exception:
        pass

    desktop_candidates.extend(
        [
            Path.home() / "Desktop",
            Path(os.environ.get("USERPROFILE", "")) / "Desktop",
            Path(os.environ.get("OneDrive", "")) / "Desktop",
            Path(os.environ.get("OneDrive", "")) / "Escritorio",
        ]
    )

    desktop_targets = []
    seen = set()
    for base in desktop_candidates:
        if not str(base).strip():
            continue
        key = str(base).lower()
        if key in seen:
            continue
        seen.add(key)
        desktop_targets.append(base / f"{app_name}.lnk")

    start_menu = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / f"{app_name}.lnk"
    )
    for target_lnk in [*desktop_targets, start_menu]:
        target_lnk.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            "$W=New-Object -ComObject WScript.Shell;"
            f"$S=$W.CreateShortcut('{str(target_lnk)}');"
            f"$S.TargetPath='{str(exe_path)}';"
            f"$S.IconLocation='{str(exe_path)},0';"
            f"$S.WorkingDirectory='{str(exe_path.parent)}';"
            f"$S.Description='GRUPO ARGA | NESTING SUITE V4.0';"
            "$S.Save();"
        )
        _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
    creados = " | ".join(str(p) for p in [*desktop_targets, start_menu])
    print(f"[OK] Accesos directos creados: {creados}")


def cleanup_previous_builds(name: str):
    dist_dir = ROOT / "dist"
    build_dir = ROOT / "build"
    spec_path = ROOT / f"{name}.spec"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    if spec_path.exists():
        try:
            spec_path.unlink()
        except Exception:
            pass
    # Limpieza de compilados previos del nombre objetivo.
    if dist_dir.exists():
        p_onefile = dist_dir / f"{name}.exe"
        p_onedir = dist_dir / name
        p_runtime_historial = dist_dir / "historial_jobs.json"
        p_runtime_temp = dist_dir / "TEMP_PROCESSED"
        if p_onedir.exists():
            shutil.rmtree(p_onedir, ignore_errors=True)
        # No borrar dist/modules: el inventario persistente del EXE vive ahí.
        # Tras el build se realinea con Herinox y se espeja desde modules/Plates.xlsx.
        if p_runtime_temp.exists():
            shutil.rmtree(p_runtime_temp, ignore_errors=True)
        if p_runtime_historial.exists():
            try:
                p_runtime_historial.unlink()
            except Exception:
                print(f"[WARN] No se pudo borrar {p_runtime_historial} (posiblemente en uso).")
        if p_onefile.exists() and not _release_dist_exe(p_onefile):
            print(f"[WARN] No se pudo liberar {p_onefile} antes del build.")


def main():
    parser = argparse.ArgumentParser(
        description="Construye ARGA NESTING SUITE (.exe) y opcionalmente asocia .arganest."
    )
    parser.add_argument("--name", default="ArgaNestingSuite", help="Nombre del ejecutable.")
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="No actualiza/instala dependencias de Python.",
    )
    parser.add_argument(
        "--associate-arganest",
        action="store_true",
        help="Asocia .arganest/.navanest para abrir con doble click.",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Genera carpeta onedir en lugar de un único .exe (--onefile por defecto).",
    )
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Solo limpia compilados previos (dist/build/spec) y termina.",
    )
    parser.add_argument(
        "--create-shortcuts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Crea accesos directos en Escritorio y Menú Inicio (por defecto: sí).",
    )
    parser.add_argument(
        "--refresh-icon-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Solicita a Windows refrescar caché de íconos (por defecto: sí).",
    )
    args = parser.parse_args()

    cleanup_previous_builds(args.name)
    if args.cleanup_only:
        print("[OK] Limpieza completada.")
        return

    if not args.skip_deps:
        ensure_build_dependencies()
    sync_repo_plates_from_herinox("pre-build")
    exe_path = build_exe(args.name, onefile=not args.onedir)
    sync_repo_plates_from_herinox("post-build")
    align_plates_inventory_after_build(exe_path)
    if args.associate_arganest:
        associate_extensions(exe_path)
    if args.create_shortcuts:
        create_shortcuts(exe_path, app_name=args.name)
    if args.refresh_icon_cache:
        refresh_windows_icon_cache()


if __name__ == "__main__":
    main()
