import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "main.py"
ICON_PNG = ROOT / "assets" / "branding" / "logo_icon1.png"
SPLASH_JPEG = ROOT / "grupo_arga_cover.jpeg"
MACRO = ROOT / "generador_verde.FCMacro"
CPP_ENGINE_PS1 = ROOT / "modules" / "nesting_engine" / "build_cpp_engine.ps1"
CPP_ENGINE_PYD = ROOT / "modules" / "nesting_engine" / "algorithm_cpp.pyd"

# Módulos que PyInstaller no siempre detecta (imports dinámicos / rutas legacy).
HIDDEN_IMPORTS = (
    # Legacy path (interface/ en sys.path)
    "config",
    "postgres_connector",
    "utils_nesting",
    "nesting_workspace",
    "nesting_modals",
    "nesting_canvas",
    "responsive_layout",
    "reporte_pdf_nesting",
    # Largos + material requerido (NESTEO DE LARGOS / MRL)
    "catalogo_largos",
    "lista_largos_material_requerido",
    "interface.largos_nesting_service",
    "modules.lista_largos_importer",
    # Qt — ventana principal
    "interface.qt.main_window",
    "interface.qt.theme",
    "interface.qt.thread_bridge",
    "interface.qt.progress_dialog",
    "interface.qt.layout_helpers",
    "interface.qt.ui_mixins",
    "interface.qt.export_paths",
    "interface.qt.nesting_canvas",
    "interface.qt.nesting_graphics",
    "interface.qt.visualizer",
    "interface.qt.cad_graphics_view",
    "interface.qt.cad_dimension_items",
    "interface.qt.cad_snap",
    "interface.qt.dxf_part_loader",
    "interface.qt.dxf_part_geometry",
    "interface.qt.curve_refine",
    "interface.qt.dxf_hifi_path",
    "interface.qt.mpl_utils",
    "interface.qt.visor_diag",
    # Qt — tabs
    "interface.qt.tabs.tab_files",
    "interface.qt.tabs.tab_parts",
    "interface.qt.tabs.tab_sheets",
    "interface.qt.tabs.tab_nesting",
    "interface.qt.tabs.tab_nesting_ui",
    # Qt — diálogos y widgets (largos + nesting)
    "interface.qt.dialogs.nesting_modals",
    "interface.qt.dialogs.lote_editor",
    "interface.qt.dialogs.largos_nesting_modal",
    "interface.qt.widgets.herinox_switch",
    "interface.qt.widgets.largos_tira_canvas",
    "interface.qt.widgets.largos_perfil_draw",
  # Soporte UI / metadatos
    "interface.autodxf_metadata",
    "interface.material_colors",
    # Motor nesting + placas
    "modules.nesting_engine",
    "modules.nesting_engine.manager",
    "modules.nesting_engine.algorithm_bridge",
    "modules.nesting_engine.algorithm_cpp",
    "modules.nesting_engine.nest_optimization",
    "modules.nesting_engine.cu_largos_nesting",
    "modules.nesting_engine.sheet_integrity",
    "modules.nesting_engine.efficiency_metrics",
    "modules.nesting_engine.rtz_overlays",
    "modules.nesting_engine.geometry_parser",
    "modules.nesting_engine.exporter",
    "modules.nesting_engine.api_client",
    "modules.consulta_herinox_bridge",
    "modules.sheets_manager",
    "modules.processed_layers",
    "modules.scanner",
    "modules.nest_exporter",
    "modules.plates_inventory",
    "modules.plasma_compensator",
    "modules.herinox_sync",
    "pandas",
    "openpyxl",
)

COLLECT_SUBMODULES = (
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shapely",
    "matplotlib.backends.backend_qtagg",
)

# Archivos que deben existir antes de empaquetar (ARGA NESTING SUITE actual).
CRITICAL_SUITE_FILES = (
    MAIN_PY,
    ROOT / "catalogo_largos.py",
    ROOT / "lista_largos_material_requerido.py",
    ROOT / "interface" / "largos_nesting_service.py",
    ROOT / "interface" / "qt" / "dialogs" / "largos_nesting_modal.py",
    ROOT / "interface" / "qt" / "widgets" / "largos_tira_canvas.py",
    ROOT / "interface" / "qt" / "widgets" / "largos_perfil_draw.py",
    ROOT / "interface" / "qt" / "widgets" / "herinox_switch.py",
    ROOT / "modules" / "nesting_engine" / "algorithm_bridge.py",
    ROOT / "modules" / "nesting_engine" / "nest_optimization.py",
    ROOT / "modules" / "nesting_engine" / "cu_largos_nesting.py",
    ROOT / "modules" / "Plates.xlsx",
)

SMOKE_IMPORT_MODULES = (
    "catalogo_largos",
    "lista_largos_material_requerido",
    "interface.largos_nesting_service",
    "interface.qt.dialogs.largos_nesting_modal",
    "interface.qt.widgets.largos_perfil_draw",
    "modules.nesting_engine.algorithm_bridge",
    "modules.nesting_engine.manager",
)


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
        "PySide6",
        "matplotlib",
        "ezdxf",
        "shapely",
        "psycopg2-binary",
        "openpyxl",
        "pandas",
        "pybind11",
        "cmake",
    ):
        _pip_install(pkg)


def _ensure_build_import_path():
    for p in (ROOT, ROOT / "interface"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def validate_suite_manifest():
    """Falla temprano si faltan piezas del ARGA NESTING SUITE actual."""
    missing = [str(p.relative_to(ROOT)) for p in CRITICAL_SUITE_FILES if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Faltan archivos críticos del suite antes de empaquetar:\n  - "
            + "\n  - ".join(missing)
        )
    print(f"[OK] Manifiesto del suite: {len(CRITICAL_SUITE_FILES)} archivos críticos presentes.")


def smoke_test_imports():
    """Importa módulos clave (largos, Qt, motor) en el intérprete de build."""
    _ensure_build_import_path()
    for mod in SMOKE_IMPORT_MODULES:
        importlib.import_module(mod)
        print(f"[OK] import {mod}")
    try:
        from modules.nesting_engine.algorithm_bridge import engine_name

        print(f"[OK] motor nesting: {engine_name()}")
    except Exception as exc:
        print(f"[WARN] motor C++ no disponible en smoke test: {exc}")


def ensure_cpp_engine(skip: bool = False, allow_missing: bool = False) -> Path | None:
    """Compila algorithm_cpp.pyd si hace falta (requerido para nesting en el EXE)."""
    if CPP_ENGINE_PYD.exists() and skip:
        print(f"[OK] Motor C++ existente: {CPP_ENGINE_PYD}")
        return CPP_ENGINE_PYD

    if skip:
        if CPP_ENGINE_PYD.exists():
            return CPP_ENGINE_PYD
        msg = (
            f"[ERROR] --skip-cpp y no existe {CPP_ENGINE_PYD.name}. "
            "El nesting no funcionará en otras PCs sin el motor C++."
        )
        if allow_missing:
            print(f"[WARN] {msg}")
            return None
        raise FileNotFoundError(msg)

    if not CPP_ENGINE_PS1.exists():
        raise FileNotFoundError(f"No se encontró script de build C++: {CPP_ENGINE_PS1}")

    _run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(CPP_ENGINE_PS1),
            "-PythonExe",
            sys.executable,
        ],
        cwd=ROOT,
    )
    if not CPP_ENGINE_PYD.exists():
        raise FileNotFoundError(
            f"No se generó {CPP_ENGINE_PYD} tras build_cpp_engine.ps1"
        )
    print(f"[OK] Motor C++ listo: {CPP_ENGINE_PYD}")
    return CPP_ENGINE_PYD


def _pyinstaller_collect_args() -> list[str]:
    args: list[str] = []
    for mod in HIDDEN_IMPORTS:
        args += ["--hidden-import", mod]
    for pkg in COLLECT_SUBMODULES:
        args += ["--collect-submodules", pkg]
    return args


def _pyinstaller_data_args() -> list[str]:
    args: list[str] = []
    data_pairs = [
        (SPLASH_JPEG, "."),
        (MACRO, "."),
        (ROOT / "modules", "modules"),
        (ROOT / "interface", "interface"),
        (ROOT / "assets", "assets"),
        (ROOT / "inventario_remanentes.csv", "."),
    ]
    for src, dest in data_pairs:
        if src.exists():
            args += ["--add-data", f"{src};{dest}"]
        elif src.name == "inventario_remanentes.csv":
            print("[WARN] inventario_remanentes.csv no encontrado; se omite del bundle.")
    return args


def _pyinstaller_binary_args(cpp_pyd: Path | None) -> list[str]:
    if cpp_pyd is None or not cpp_pyd.exists():
        return []
    return ["--add-binary", f"{cpp_pyd};modules/nesting_engine"]


def build_exe(name: str, onefile: bool = True, cpp_pyd: Path | None = None):
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

    icon_ico = _build_ico_file()

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
    ]
    cmd += _pyinstaller_collect_args()
    cmd += _pyinstaller_data_args()
    cmd += _pyinstaller_binary_args(cpp_pyd)
    if icon_ico and icon_ico.exists():
        cmd += ["--icon", str(icon_ico)]
    cmd += [str(MAIN_PY)]

    _run(cmd, cwd=ROOT)
    exe_path = (ROOT / "dist" / f"{name}.exe") if onefile else (ROOT / "dist" / name / f"{name}.exe")
    print(f"[OK] EXE generado: {exe_path}")
    if not onefile:
        print("[WARN] Modo onedir: no mover solo el .exe; debe mantenerse junto a su carpeta completa.")
    return exe_path


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


def seed_persistent_sidecars(exe_path: Path):
    """
    Deja junto al .exe los archivos editables que otras PCs necesitan en primer arranque.
    No sobrescribe si el usuario ya los tiene.
    """
    dist_root = exe_path.parent
    seeds = (
        (ROOT / "modules" / "Plates.xlsx", dist_root / "modules" / "Plates.xlsx"),
        (ROOT / "inventario_remanentes.csv", dist_root / "inventario_remanentes.csv"),
    )
    for src, dst in seeds:
        if not src.is_file():
            print(f"[WARN] Sin semilla: {src.name}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_file():
            print(f"[OK] Sidecar existente (sin tocar): {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"[OK] Sidecar sembrado: {dst}")


def write_build_manifest(exe_path: Path, cpp_pyd: Path | None) -> Path:
    manifest = {
        "app": "ARGA NESTING SUITE",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "exe": str(exe_path.resolve()),
        "cpp_engine": bool(cpp_pyd and cpp_pyd.is_file()),
        "hidden_imports": len(HIDDEN_IMPORTS),
        "collect_submodules": list(COLLECT_SUBMODULES),
        "features": [
            "nesting_placas_cpp",
            "nesting_largos_mrl",
            "nesteo_largos_switches",
            "export_material_requerido_ldg",
            "herinox_catalogo_largos",
        ],
        "deploy_notes": [
            "Copiar ArgaNestingSuite.exe + carpeta modules/ (Plates.xlsx) al mismo directorio.",
            "Opcional: inventario_remanentes.csv junto al exe.",
            "Requiere red a PostgreSQL nesting (5433) y Herinox (5439) en modo servidor.",
            "FreeCAD en C:\\Program Files\\FreeCAD 1.0\\ para STEP (si aplica).",
        ],
    }
    out = exe_path.parent / "arga_build_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Manifiesto de build: {out}")
    return out


def print_deploy_checklist(exe_path: Path):
    dist = exe_path.parent
    checks = [
        ("Ejecutable", exe_path.is_file()),
        ("modules/Plates.xlsx", (dist / "modules" / "Plates.xlsx").is_file()),
        ("arga_build_manifest.json", (dist / "arga_build_manifest.json").is_file()),
    ]
    print("\n=== CHECKLIST DESPLIEGUE (copiar esta carpeta dist/ a otras PCs) ===")
    ok_all = True
    for label, ok in checks:
        print(f"  [{'OK' if ok else 'FALTA'}] {label}")
        ok_all = ok_all and ok
    print(f"  Carpeta: {dist.resolve()}")
    if ok_all:
        print("[OK] Paquete listo para probar en otras PCs.")
    else:
        print("[WARN] Faltan archivos sidecar; revisa el build.")
    print("=" * 72)


def associate_extensions(exe_path: Path):
    if os.name != "nt":
        print("[WARN] Asociación automática solo soportada en Windows.")
        return
    exe = str(exe_path.resolve())
    cmd = f"\"{exe}\" \"%1\""
    _run(["reg", "add", r"HKCU\Software\Classes\.arganest", "/ve", "/d", "ArgaNesting.Workspace", "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\.navanest", "/ve", "/d", "ArgaNesting.Workspace", "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\ArgaNesting.Workspace", "/ve", "/d", "Arga Nest Workspace", "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\ArgaNesting.Workspace\DefaultIcon", "/ve", "/d", exe, "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\ArgaNesting.Workspace\shell\open\command", "/ve", "/d", cmd, "/f"])
    print("[OK] Asociación .arganest/.navanest configurada para el usuario actual.")


def refresh_windows_icon_cache():
    if os.name != "nt":
        return
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
            f"$S.Description='ARGA NESTING SUITE';"
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
    if dist_dir.exists():
        p_onefile = dist_dir / f"{name}.exe"
        p_onedir = dist_dir / name
        p_runtime_historial = dist_dir / "historial_jobs.json"
        p_runtime_temp = dist_dir / "TEMP_PROCESSED"
        if p_onedir.exists():
            shutil.rmtree(p_onedir, ignore_errors=True)
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
        description="Construye ARGA NESTING SUITE (.exe) con nesting de largos, MRL y motor C++."
    )
    parser.add_argument("--name", default="ArgaNestingSuite", help="Nombre del ejecutable.")
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="No actualiza/instala dependencias de Python.",
    )
    parser.add_argument(
        "--skip-cpp",
        action="store_true",
        help="No recompila algorithm_cpp.pyd (usa el existente si hay).",
    )
    parser.add_argument(
        "--allow-no-cpp",
        action="store_true",
        help="Permite terminar sin motor C++ (nesting de placas no funcionará).",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Omite smoke test de imports antes de PyInstaller.",
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

    validate_suite_manifest()

    if not args.skip_deps:
        ensure_build_dependencies()

    if not args.skip_smoke:
        smoke_test_imports()

    cpp_pyd = ensure_cpp_engine(skip=args.skip_cpp, allow_missing=args.allow_no_cpp)
    sync_repo_plates_from_herinox("pre-build")
    exe_path = build_exe(args.name, onefile=not args.onedir, cpp_pyd=cpp_pyd)
    sync_repo_plates_from_herinox("post-build")
    align_plates_inventory_after_build(exe_path)
    seed_persistent_sidecars(exe_path)
    write_build_manifest(exe_path, cpp_pyd)
    print_deploy_checklist(exe_path)
    if args.associate_arganest:
        associate_extensions(exe_path)
    if args.create_shortcuts:
        create_shortcuts(exe_path, app_name=args.name)
    if args.refresh_icon_cache:
        refresh_windows_icon_cache()


if __name__ == "__main__":
    main()
