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
BRANDING_DIR = ROOT / "assets" / "branding"
# Icono app / .exe / barra de tareas
ICON_APP_PNG = BRANDING_DIR / "arga_nesting_logo.png"
# Iconos de archivos workspace por material
ICON_FILE_STEEL_PNG = BRANDING_DIR / "arga_archivo_nesteo_2.png"
ICON_FILE_CU_PNG = BRANDING_DIR / "arga_archivo_nesteo_cu.png"
ICON_FILE_MIX_PNG = BRANDING_DIR / "arga_archivo_nesteo_3.png"
ICON_FILE_PNG = ICON_FILE_STEEL_PNG  # alias legacy
ICON_PNG_FALLBACK = BRANDING_DIR / "logo_icon1.png"
APP_EXE_NAME = "ARGA NESTING SUITE"
SPLASH_JPEG = ROOT / "grupo_arga_cover.jpeg"
MACRO = ROOT / "generador_verde.FCMacro"
CPP_ENGINE_PS1 = ROOT / "modules" / "nesting_engine" / "build_cpp_engine.ps1"
CPP_ENGINE_PYD = ROOT / "modules" / "nesting_engine" / "algorithm_cpp.pyd"
CPP_ENGINE_CPP_DIR = ROOT / "modules" / "nesting_engine" / "cpp"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
BRANDING_PNG_PRESERVE = (
    "arga_nesting_logo.png",
    "arga_archivo_nesteo_2.png",
    "arga_archivo_nesteo_cu.png",
    "arga_archivo_nesteo_3.png",
)

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
            f"Cierra '{APP_EXE_NAME}.exe' y vuelve a compilar. Detalle: {exc}"
        )
        return False


def _fit_rgba_square(img: Image.Image) -> Image.Image:
    """Escala la imagen al cuadrado sin estirar (padding transparente)."""
    img = img.convert("RGBA")
    w, h = img.size
    side = max(w, h)
    if w == h:
        return img
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)
    return canvas


def _png_to_ico(src: Path, out: Path) -> Path | None:
    """Genera un .ico multi-tamaño desde un PNG, respetando proporción."""
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        # Windows ICO es cuadrado: pad transparente en vez de estirar.
        img = _fit_rgba_square(Image.open(src))
        img.save(out, format="ICO", sizes=ICO_SIZES)
        print(f"[OK] Icono .ico generado: {out} (desde {src.name}, sin estirar)")
        return out
    except Exception as e:
        print(f"[WARN] No se pudo generar .ico desde {src}: {e}")
        return None


def _build_app_ico() -> Path | None:
    """Icono embebido en el .exe y barra de tareas (arga_nesting_logo)."""
    src = (
        ICON_APP_PNG
        if ICON_APP_PNG.exists()
        else (ICON_PNG_FALLBACK if ICON_PNG_FALLBACK.exists() else None)
    )
    if src is None and SPLASH_JPEG.exists():
        src = SPLASH_JPEG
    if src is None:
        print("[WARN] No se encontró imagen de branding para icono de app.")
        return None
    return _png_to_ico(src, ROOT / "build" / "arga_app.ico")


def _build_file_ico() -> Path | None:
    """Icono acero (.arganest) — arga_archivo_nesteo_2."""
    src = ICON_FILE_STEEL_PNG if ICON_FILE_STEEL_PNG.exists() else ICON_APP_PNG
    if not src.exists():
        print("[WARN] No se encontró imagen para icono de archivos .arganest.")
        return None
    return _png_to_ico(src, ROOT / "build" / "arga_archivo_nesteo.ico")


def _build_file_ico_cu() -> Path | None:
    """Icono cobre (.arganestcu)."""
    src = ICON_FILE_CU_PNG if ICON_FILE_CU_PNG.exists() else ICON_FILE_STEEL_PNG
    if not src.exists():
        return None
    return _png_to_ico(src, ROOT / "build" / "arga_archivo_nesteo_cu.ico")


def _build_file_ico_mix() -> Path | None:
    """Icono mixto acero+cobre (.arganestmix)."""
    src = ICON_FILE_MIX_PNG if ICON_FILE_MIX_PNG.exists() else ICON_FILE_STEEL_PNG
    if not src.exists():
        return None
    return _png_to_ico(src, ROOT / "build" / "arga_archivo_nesteo_mix.ico")


def _build_all_file_icos() -> dict[str, Path]:
    """Genera los 3 iconos de workspace; retorna mapa key→path existentes."""
    out: dict[str, Path] = {}
    for key, builder in (
        ("steel", _build_file_ico),
        ("cu", _build_file_ico_cu),
        ("mix", _build_file_ico_mix),
    ):
        p = builder()
        if p and p.exists():
            out[key] = p
    return out


def _copy_file_icos_to_dist(dist_dir: Path, icos: dict[str, Path] | None = None) -> dict[str, Path]:
    icos = icos or _build_all_file_icos()
    name_map = {
        "steel": "arga_archivo_nesteo.ico",
        "cu": "arga_archivo_nesteo_cu.ico",
        "mix": "arga_archivo_nesteo_mix.ico",
    }
    copied: dict[str, Path] = {}
    dist_dir.mkdir(parents=True, exist_ok=True)
    for key, src in icos.items():
        dst = dist_dir / name_map[key]
        shutil.copy2(src, dst)
        copied[key] = dst
        print(f"[OK] Icono workspace copiado a dist: {dst.name}")
    return copied


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
        kept_branding = _preserve_branding_in_build(ROOT / "build")
        shutil.rmtree(ROOT / "build", ignore_errors=True)
        _restore_branding_in_build(ROOT / "build", kept_branding)
    if onefile:
        onefile_exe = ROOT / "dist" / f"{name}.exe"
        if not _release_dist_exe(onefile_exe):
            raise PermissionError(f"No se pudo liberar la ruta del ejecutable: {onefile_exe}")
    else:
        if (ROOT / "dist" / name).exists():
            shutil.rmtree(ROOT / "dist" / name, ignore_errors=True)

    app_ico = _build_app_ico()
    file_icos = _build_all_file_icos()

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
    if app_ico and app_ico.exists():
        cmd += ["--icon", str(app_ico)]
    cmd += [str(MAIN_PY)]

    _run(cmd, cwd=ROOT)
    exe_path = (ROOT / "dist" / f"{name}.exe") if onefile else (ROOT / "dist" / name / f"{name}.exe")
    _copy_file_icos_to_dist(exe_path.parent, file_icos)
    if app_ico and app_ico.exists():
        shutil.copy2(app_ico, exe_path.parent / "arga_app.ico")
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
    git_commit = ""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if out.returncode == 0:
            git_commit = str(out.stdout or "").strip()
    except Exception:
        pass

    manifest = {
        "app": "ARGA NESTING SUITE",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "git_branch": "main",
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
            f"Copiar '{APP_EXE_NAME}.exe' al directorio destino.",
            "Incluir arga_archivo_nesteo.ico / _cu.ico / _mix.ico junto al exe.",
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
        ("arga_archivo_nesteo.ico", (dist / "arga_archivo_nesteo.ico").is_file()),
        ("arga_archivo_nesteo_cu.ico", (dist / "arga_archivo_nesteo_cu.ico").is_file()),
        ("arga_archivo_nesteo_mix.ico", (dist / "arga_archivo_nesteo_mix.ico").is_file()),
        ("icon_handler/ArgaIconHandler.dll", (dist / "icon_handler" / "ArgaIconHandler.dll").is_file()),
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


NATIVE_ICON_HANDLER_CLSID = "{A31F8C2E-9B74-4D6A-8E15-2C70F4A9D813}"
# CLSID del intento .NET/SharpShell (no reactivar)
LEGACY_DOTNET_ICON_HANDLER_CLSID = "{B6E2C9A1-4D7F-4E8A-9C31-7A2F0D91E5B4}"


def _local_arga_nesting_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ArgaNesting"


def _native_icon_handler_dir() -> Path:
    return ROOT / "tools" / "arga_icon_handler_native"


def _native_icon_handler_dll() -> Path:
    return _native_icon_handler_dir() / "bin" / "ArgaIconHandler.dll"


def _reg_default_value(key_path: str) -> str | None:
    """Lee el valor (default) de una clave HKCU/HKLM vía reg.exe. None si no existe."""
    if os.name != "nt":
        return None
    try:
        proc = subprocess.run(
            ["reg", "query", key_path, "/ve"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        if "REG_SZ" in line:
            parts = line.split("REG_SZ", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None


def native_icon_handler_installed() -> bool:
    """True si esta PC ya tiene DLL+icos+IconHandler nativo registrado (no hace falta rehacerlo)."""
    if os.name != "nt":
        return False
    local = _local_arga_nesting_dir()
    required = (
        local / "ArgaIconHandler.dll",
        local / "arga_archivo_nesteo.ico",
        local / "arga_archivo_nesteo_cu.ico",
        local / "arga_archivo_nesteo_mix.ico",
    )
    if not all(p.is_file() and p.stat().st_size > 0 for p in required):
        return False
    clsid = NATIVE_ICON_HANDLER_CLSID
    ih = _reg_default_value(
        r"HKCU\Software\Classes\ArgaNesting.Workspace\ShellEx\IconHandler"
    )
    if not ih or ih.upper() != clsid.upper():
        return False
    server = _reg_default_value(rf"HKCU\Software\Classes\CLSID\{clsid}\InProcServer32")
    if not server:
        return False
    # Ruta registrada debe existir (evita stubs rotos)
    return Path(server).is_file()


def build_native_icon_handler() -> Path | None:
    """Compila ArgaIconHandler.dll (IExtractIcon nativo x64)."""
    build_ps1 = _native_icon_handler_dir() / "build.ps1"
    if not build_ps1.is_file():
        print(f"[WARN] No hay build.ps1 del Icon Handler nativo: {build_ps1}")
        return None
    try:
        _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(build_ps1),
            ],
            cwd=_native_icon_handler_dir(),
        )
    except Exception as exc:
        print(f"[WARN] Falló build de Icon Handler nativo: {exc}")
        return None
    dll = _native_icon_handler_dll()
    if dll.is_file():
        print(f"[OK] Icon Handler nativo compilado: {dll}")
        return dll
    print("[WARN] Build Icon Handler nativo OK pero no se encontró el DLL.")
    return None


def _deploy_icon_assets(dist_dir: Path, local_icon_dir: Path) -> Path:
    """Copia DLL + 3 icos a LocalAppData\\ArgaNesting y dist\\icon_handler."""
    local_icon_dir.mkdir(parents=True, exist_ok=True)
    handler_dir = dist_dir / "icon_handler"
    handler_dir.mkdir(parents=True, exist_ok=True)

    dll = build_native_icon_handler()
    if dll is None or not dll.is_file():
        # Reusa DLL ya compilado si existe
        dll = _native_icon_handler_dll() if _native_icon_handler_dll().is_file() else None

    deployed_dll = local_icon_dir / "ArgaIconHandler.dll"
    if dll is not None and dll.is_file():
        shutil.copy2(dll, deployed_dll)
        shutil.copy2(dll, handler_dir / "ArgaIconHandler.dll")

    for ico_name in (
        "arga_archivo_nesteo.ico",
        "arga_archivo_nesteo_cu.ico",
        "arga_archivo_nesteo_mix.ico",
    ):
        src_ico = dist_dir / ico_name
        if src_ico.is_file():
            shutil.copy2(src_ico, local_icon_dir / ico_name)
            shutil.copy2(src_ico, handler_dir / ico_name)

    _run(
        [
            "reg",
            "add",
            r"HKCU\Software\ArgaNesting",
            "/v",
            "IconDir",
            "/t",
            "REG_SZ",
            "/d",
            str(local_icon_dir.resolve()),
            "/f",
        ]
    )
    return deployed_dll


def register_native_icon_handler(dll_path: Path) -> bool:
    """Registra Icon Handler nativo (CLSID Approved vía UAC una vez)."""
    if not dll_path.is_file():
        print("[WARN] No hay ArgaIconHandler.dll para registrar.")
        return False

    clsid = NATIVE_ICON_HANDLER_CLSID
    dll = str(dll_path.resolve())

    # Registro HKCU (InProcServer32) + IconHandler en ProgID/ext — sin admin
    _run(
        [
            "reg",
            "add",
            rf"HKCU\Software\Classes\CLSID\{clsid}",
            "/ve",
            "/d",
            "Arga Nest Workspace Icon Handler",
            "/f",
        ]
    )
    _run(
        [
            "reg",
            "add",
            rf"HKCU\Software\Classes\CLSID\{clsid}\InProcServer32",
            "/ve",
            "/d",
            dll,
            "/f",
        ]
    )
    _run(
        [
            "reg",
            "add",
            rf"HKCU\Software\Classes\CLSID\{clsid}\InProcServer32",
            "/v",
            "ThreadingModel",
            "/t",
            "REG_SZ",
            "/d",
            "Apartment",
            "/f",
        ]
    )
    for key in (
        r"HKCU\Software\Classes\ArgaNesting.Workspace\ShellEx\IconHandler",
        r"HKCU\Software\Classes\.arganest\ShellEx\IconHandler",
        r"HKCU\Software\Classes\.navanest\ShellEx\IconHandler",
    ):
        _run(["reg", "add", key, "/ve", "/d", clsid, "/f"])

    # Approved + HKLM CLSID requieren admin (UAC). Script temporal para evitar quoting frágil.
    elev_ps1 = Path(os.environ.get("TEMP", ".")) / "arga_register_icon_handler.ps1"
    elev_ps1.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$dll = '{dll.replace(chr(39), chr(39) + chr(39))}'",
                f"$clsid = '{clsid}'",
                f"$legacy = '{LEGACY_DOTNET_ICON_HANDLER_CLSID}'",
                "New-Item -Path \"HKLM:\\Software\\Classes\\CLSID\\$clsid\" -Force | Out-Null",
                "Set-ItemProperty -Path \"HKLM:\\Software\\Classes\\CLSID\\$clsid\" -Name '(default)' -Value 'Arga Nest Workspace Icon Handler' -Force",
                "New-Item -Path \"HKLM:\\Software\\Classes\\CLSID\\$clsid\\InProcServer32\" -Force | Out-Null",
                "Set-ItemProperty -Path \"HKLM:\\Software\\Classes\\CLSID\\$clsid\\InProcServer32\" -Name '(default)' -Value $dll -Force",
                "Set-ItemProperty -Path \"HKLM:\\Software\\Classes\\CLSID\\$clsid\\InProcServer32\" -Name 'ThreadingModel' -Value 'Apartment' -Force",
                "$p = 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Shell Extensions\\Approved'",
                "New-Item -Path $p -Force | Out-Null",
                "Set-ItemProperty -Path $p -Name $clsid -Value 'Arga Nest Workspace Icon Handler' -Force",
                "Remove-Item -Path \"HKLM:\\Software\\Classes\\CLSID\\$legacy\" -Recurse -Force -ErrorAction SilentlyContinue",
                "Remove-ItemProperty -Path $p -Name $legacy -Force -ErrorAction SilentlyContinue",
            ]
        ),
        encoding="utf-8",
    )
    try:
        print("[INFO] Se pedirá permiso de administrador para Approved (Icon Handler)…")
        _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"Start-Process powershell -Verb RunAs -Wait -ArgumentList "
                f"'-NoProfile','-ExecutionPolicy','Bypass','-File','{elev_ps1}'",
            ]
        )
    except Exception as exc:
        print(f"[WARN] Registro elevado del Icon Handler falló: {exc}")
        print(
            "[WARN] El handler quedó en HKCU; si Explorer no lo carga, registra Approved en HKLM."
        )

    print(f"[OK] Icon Handler nativo registrado ({clsid}) -> {dll}")
    return True


def associate_extensions(exe_path: Path):
    if os.name != "nt":
        print("[WARN] Asociación automática solo soportada en Windows.")
        return
    exe = str(exe_path.resolve())
    cmd = f"\"{exe}\" \"%1\""
    dist = exe_path.parent
    # Asegura sidecars de iconos (acero/cobre/mixto) — misma extensión .arganest
    needed = {
        "steel": dist / "arga_archivo_nesteo.ico",
        "cu": dist / "arga_archivo_nesteo_cu.ico",
        "mix": dist / "arga_archivo_nesteo_mix.ico",
    }
    if not all(p.is_file() for p in needed.values()):
        _copy_file_icos_to_dist(dist)

    # Iconos + DLL en ruta SIN espacios (DefaultIcon / handler fallan con paths con espacios).
    local_icon_dir = _local_arga_nesting_dir()
    deployed_dll = _deploy_icon_assets(dist, local_icon_dir)
    steel_local = local_icon_dir / "arga_archivo_nesteo.ico"
    icon_reg = f"{steel_local.resolve()},0" if steel_local.is_file() else f"\"{exe}\",0"

    # Una sola extensión / ProgID
    _run(["reg", "add", r"HKCU\Software\Classes\.arganest", "/ve", "/d", "ArgaNesting.Workspace", "/f"])
    _run(["reg", "add", r"HKCU\Software\Classes\.navanest", "/ve", "/d", "ArgaNesting.Workspace", "/f"])
    _run(
        [
            "reg",
            "add",
            r"HKCU\Software\Classes\ArgaNesting.Workspace",
            "/ve",
            "/d",
            "Arga Nest Workspace",
            "/f",
        ]
    )
    # DefaultIcon de respaldo = acero (si el handler no carga, nunca hoja en blanco)
    _run(
        [
            "reg",
            "add",
            r"HKCU\Software\Classes\ArgaNesting.Workspace\DefaultIcon",
            "/ve",
            "/d",
            icon_reg,
            "/f",
        ]
    )
    _run(
        [
            "reg",
            "add",
            r"HKCU\Software\Classes\ArgaNesting.Workspace\shell\open\command",
            "/ve",
            "/d",
            cmd,
            "/f",
        ]
    )

    # Quitar ShellEx del handler .NET legacy antes de registrar el nativo
    for key in (
        r"HKCU\Software\Classes\.arganest\ShellEx",
        r"HKCU\Software\Classes\.navanest\ShellEx",
        r"HKCU\Software\Classes\ArgaNesting.Workspace\ShellEx",
        rf"HKCU\Software\Classes\CLSID\{LEGACY_DOTNET_ICON_HANDLER_CLSID}",
    ):
        try:
            _run(["reg", "delete", key, "/f"])
        except Exception:
            pass

    # Limpia asociaciones experimentales por extensión (si existían)
    for ext in (".arganestcu", ".arganestmix"):
        try:
            _run(["reg", "delete", rf"HKCU\Software\Classes\{ext}", "/f"])
        except Exception:
            pass
    for progid in ("ArgaNesting.WorkspaceCu", "ArgaNesting.WorkspaceMix"):
        try:
            _run(["reg", "delete", rf"HKCU\Software\Classes\{progid}", "/f"])
        except Exception:
            pass

    if deployed_dll.is_file():
        register_native_icon_handler(deployed_dll)
        print(f"[OK] .arganest/.navanest con Icon Handler nativo (fallback: {icon_reg}).")
    else:
        print(f"[OK] .arganest/.navanest asociados (icono fijo: {icon_reg}; sin DLL nativa).")


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


def create_shortcuts(exe_path: Path, app_name: str = APP_EXE_NAME):
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
    icon_loc = str(exe_path)
    app_ico_sidecar = exe_path.parent / "arga_app.ico"
    if app_ico_sidecar.is_file():
        icon_loc = str(app_ico_sidecar)
    for target_lnk in [*desktop_targets, start_menu]:
        target_lnk.parent.mkdir(parents=True, exist_ok=True)
        # Escapar comillas simples para PowerShell
        lnk_ps = str(target_lnk).replace("'", "''")
        exe_ps = str(exe_path).replace("'", "''")
        icon_ps = icon_loc.replace("'", "''")
        cwd_ps = str(exe_path.parent).replace("'", "''")
        ps = (
            "$W=New-Object -ComObject WScript.Shell;"
            f"$S=$W.CreateShortcut('{lnk_ps}');"
            f"$S.TargetPath='{exe_ps}';"
            f"$S.IconLocation='{icon_ps},0';"
            f"$S.WorkingDirectory='{cwd_ps}';"
            f"$S.Description='{APP_EXE_NAME}';"
            "$S.Save();"
        )
        _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
    creados = " | ".join(str(p) for p in [*desktop_targets, start_menu])
    print(f"[OK] Accesos directos creados: {creados}")


def _preserve_branding_in_build(build_dir: Path) -> list[tuple[str, bytes]]:
    """Guarda en memoria los PNG de branding que viven en build/ antes de limpiar."""
    kept: list[tuple[str, bytes]] = []
    if not build_dir.is_dir():
        return kept
    for name in BRANDING_PNG_PRESERVE:
        p = build_dir / name
        if p.is_file():
            kept.append((name, p.read_bytes()))
    return kept


def _restore_branding_in_build(build_dir: Path, kept: list[tuple[str, bytes]]) -> None:
    if not kept:
        return
    build_dir.mkdir(parents=True, exist_ok=True)
    for name, data in kept:
        (build_dir / name).write_bytes(data)


def cleanup_previous_builds(name: str):
    dist_dir = ROOT / "dist"
    build_dir = ROOT / "build"
    spec_path = ROOT / f"{name}.spec"
    kept_branding = _preserve_branding_in_build(build_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    _restore_branding_in_build(build_dir, kept_branding)
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
    parser.add_argument(
        "--name",
        default=APP_EXE_NAME,
        help=f"Nombre del ejecutable (default: '{APP_EXE_NAME}').",
    )
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
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Asocia .arganest/.navanest e Icon Handler. "
            "Por defecto: solo si aún no está instalado en esta PC "
            "(como un instalador; --associate-arganest fuerza, --no-associate-arganest omite)."
        ),
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
    do_associate = args.associate_arganest
    if do_associate is None:
        if native_icon_handler_installed():
            print(
                "[OK] Icon Handler .arganest ya instalado en esta PC; "
                "se omite asociación (usa --associate-arganest para forzar)."
            )
            do_associate = False
        else:
            print(
                "[INFO] Icon Handler no detectado en esta PC; "
                "se asociará .arganest / carátulas (una sola vez)."
            )
            do_associate = True
    if do_associate:
        associate_extensions(exe_path)
    if args.create_shortcuts:
        create_shortcuts(exe_path, app_name=args.name)
    if args.refresh_icon_cache:
        refresh_windows_icon_cache()


if __name__ == "__main__":
    main()
