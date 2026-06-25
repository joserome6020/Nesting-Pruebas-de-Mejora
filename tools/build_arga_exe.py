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
CPP_ENGINE_CPP_DIR = ROOT / "modules" / "nesting_engine" / "cpp"

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
    "reporte_pdf_nesteo_largos_piso",
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


def _find_vswhere() -> Path | None:
    for base in (
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
    ):
        candidate = Path(base) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if candidate.is_file():
            return candidate
    return None


def _msvc_available() -> bool:
    if shutil.which("cl"):
        return True
    for base in (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft Visual Studio"
        / "2022"
        / "BuildTools",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft Visual Studio"
        / "2022"
        / "BuildTools",
    ):
        if (base / "VC" / "Auxiliary" / "Build" / "vcvars64.bat").is_file():
            return True
    vswhere = _find_vswhere()
    if not vswhere:
        return False
    try:
        out = subprocess.check_output(
            [str(vswhere), "-latest", "-products", "*", "-format", "json"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out) if out.strip() else []
        items = data if isinstance(data, list) else [data]
        for inst in items:
            path = Path(str(inst.get("installationPath", "")))
            if (path / "VC" / "Auxiliary" / "Build" / "vcvars64.bat").is_file():
                return True
    except Exception:
        pass
    return False


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


AUTO_SETUP_SCRIPT = ROOT / "tools" / "auto_setup_dependencies.py"


def ensure_build_dependencies():
    """Instala dependencias pip del suite + herramientas de build (PyInstaller, cmake, etc.)."""
    if AUTO_SETUP_SCRIPT.is_file():
        _run([sys.executable, str(AUTO_SETUP_SCRIPT), "--include-optional"])
        return
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
        _run([sys.executable, "-m", "pip", "install", "--upgrade", pkg])


def _ensure_build_import_path():
    for p in (ROOT, ROOT / "interface"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _cpp_engine_usable() -> bool:
    """True si algorithm_cpp.pyd existe y carga con el Python del build."""
    if not CPP_ENGINE_PYD.is_file():
        return False
    _ensure_build_import_path()
    try:
        from modules.nesting_engine.algorithm_bridge import engine_name

        engine_name()
        return True
    except Exception:
        return False


def _cpp_sources_newer_than_pyd() -> bool:
    """True si cambió código C++/CMake desde la última compilación local."""
    if not CPP_ENGINE_PYD.is_file():
        return True
    pyd_mtime = CPP_ENGINE_PYD.stat().st_mtime
    for pattern in ("*.cpp", "*.hpp", "*.h", "CMakeLists.txt"):
        for path in CPP_ENGINE_CPP_DIR.rglob(pattern):
            if path.is_file() and path.stat().st_mtime > pyd_mtime:
                return True
    return False


def _needs_cpp_rebuild(*, force_cpp: bool, skip_cpp: bool) -> bool:
    if skip_cpp:
        return False
    if force_cpp:
        return True
    if not CPP_ENGINE_PYD.is_file():
        return True
    if not _cpp_engine_usable():
        print("[INFO] algorithm_cpp.pyd no carga con este Python; se recompilará.")
        return True
    if _cpp_sources_newer_than_pyd():
        print("[INFO] Fuentes C++ más recientes que algorithm_cpp.pyd; se recompilará.")
        return True
    return False


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
        raise RuntimeError(
            "El motor C++ no está disponible. Compila algorithm_cpp.pyd antes de empaquetar."
        ) from exc


def ensure_cpp_engine(
    skip: bool = False,
    allow_missing: bool = False,
    force_cpp: bool = False,
    install_msvc: bool = False,
) -> Path | None:
    """Compila algorithm_cpp.pyd si hace falta (requerido para nesting en el EXE)."""
    if (
        CPP_ENGINE_PYD.exists()
        and _cpp_engine_usable()
        and not _cpp_sources_newer_than_pyd()
        and (skip or not force_cpp)
    ):
        note = " (--force-cpp para recompilar)" if not skip else ""
        print(f"[OK] Motor C++ existente{note}: {CPP_ENGINE_PYD}")
        return CPP_ENGINE_PYD

    if skip:
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

    ps_cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(CPP_ENGINE_PS1),
        "-PythonExe",
        sys.executable,
    ]
    if install_msvc:
        ps_cmd.append("-InstallMsvc")

    try:
        _run(ps_cmd, cwd=ROOT)
    except subprocess.CalledProcessError as exc:
        hint = ""
        if not install_msvc:
            hint = (
                " Reintenta con --install-msvc o instala Visual Studio 2022 Build Tools (C++)."
            )
        raise RuntimeError(
            f"Falló la compilación del motor C++ (algorithm_cpp.pyd).{hint}"
        ) from exc

    if not CPP_ENGINE_PYD.exists():
        raise FileNotFoundError(
            f"No se generó {CPP_ENGINE_PYD} tras build_cpp_engine.ps1"
        )
    if not _cpp_engine_usable():
        raise RuntimeError(
            f"{CPP_ENGINE_PYD.name} se generó pero no carga con {sys.executable}. "
            "Revisa MSVC/Python y reintenta con --force-cpp."
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


def verify_build_artifacts(exe_path: Path, cpp_pyd: Path | None):
    """Falla si el paquete dist/ no refleja el proyecto actual."""
    if not exe_path.is_file():
        raise FileNotFoundError(f"No se generó el ejecutable: {exe_path}")
    if cpp_pyd is None or not cpp_pyd.is_file():
        raise RuntimeError("Build incompleto: falta algorithm_cpp.pyd en el motor de nesting.")
    manifest = exe_path.parent / "arga_build_manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not data.get("cpp_engine"):
            raise RuntimeError("Manifiesto de build sin motor C++ empaquetado.")
    print(f"[OK] Artefactos verificados: {exe_path.name} + motor C++ + sidecars.")


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
    """Verifica conexión a Herinox antes o después del empaquetado."""
    _ensure_build_import_path()
    from modules.plates_inventory import refresh_plates_from_herinox

    try:
        resultado, (empresa, proveedor) = refresh_plates_from_herinox(ROOT)
        if resultado.ok:
            print(
                f"[OK] [{phase}] Inventario Herinox verificado via {resultado.source} "
                f"(placas={resultado.matched_codes}, empresa={len(empresa)}, proveedor={len(proveedor)})"
            )
            return True
        print(f"[WARN] [{phase}] Herinox no disponible: {resultado.message}")
    except Exception as exc:
        print(f"[WARN] [{phase}] No se pudo verificar inventario Herinox: {exc}")
    return False


def align_plates_inventory_after_build(exe_path: Path):
    """Legacy hook post-build (inventario ya no usa Plates.xlsx)."""
    _ = exe_path
    print("[OK] Inventario de placas: fuente directa PostgreSQL/API Herinox (sin Plates.xlsx).")


def seed_persistent_sidecars(exe_path: Path):
    """
    Deja junto al .exe los archivos editables que otras PCs necesitan en primer arranque.
    No sobrescribe si el usuario ya los tiene.
    """
    dist_root = exe_path.parent
    seeds = (
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
            "Copiar ArgaNestingSuite.exe al directorio destino.",
            "Opcional: inventario_remanentes.csv junto al exe.",
            "Requiere red a PostgreSQL nesting (5433) y Herinox (5439) para inventario de placas.",
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
        help="No compila algorithm_cpp.pyd (requiere que ya exista).",
    )
    parser.add_argument(
        "--force-cpp",
        action="store_true",
        help="Recompila algorithm_cpp.pyd aunque ya exista.",
    )
    parser.add_argument(
        "--install-msvc",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Si falta MSVC, instala Visual Studio 2022 Build Tools vía winget (por defecto: sí).",
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

    needs_cpp = _needs_cpp_rebuild(force_cpp=args.force_cpp, skip_cpp=args.skip_cpp)
    install_msvc = args.install_msvc
    if needs_cpp and not _msvc_available():
        if install_msvc:
            print(
                "[INFO] No se detectó compilador C++ (MSVC). "
                "Se instalarán Visual Studio 2022 Build Tools automáticamente..."
            )
        else:
            raise RuntimeError(
                "No hay compilador C++ (MSVC) y --no-install-msvc está activo. "
                "Instala Build Tools manualmente o quita --no-install-msvc."
            )

    cpp_pyd = ensure_cpp_engine(
        skip=args.skip_cpp,
        allow_missing=args.allow_no_cpp,
        force_cpp=args.force_cpp,
        install_msvc=install_msvc,
    )
    if cpp_pyd is None and not args.allow_no_cpp:
        raise RuntimeError(
            "No se puede empaquetar sin algorithm_cpp.pyd. "
            "Quita --allow-no-cpp o compila el motor C++."
        )

    if not args.skip_smoke:
        smoke_test_imports()
    sync_repo_plates_from_herinox("pre-build")
    exe_path = build_exe(args.name, onefile=not args.onedir, cpp_pyd=cpp_pyd)
    sync_repo_plates_from_herinox("post-build")
    align_plates_inventory_after_build(exe_path)
    seed_persistent_sidecars(exe_path)
    write_build_manifest(exe_path, cpp_pyd)
    verify_build_artifacts(exe_path, cpp_pyd)
    print_deploy_checklist(exe_path)
    if args.associate_arganest:
        associate_extensions(exe_path)
    if args.create_shortcuts:
        create_shortcuts(exe_path, app_name=args.name)
    if args.refresh_icon_cache:
        refresh_windows_icon_cache()


if __name__ == "__main__":
    main()
