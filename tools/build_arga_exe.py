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
NEST_CORE_PS1 = ROOT / "native" / "build_arga_nest_core.ps1"
NEST_CORE_PYD = ROOT / "modules" / "nesting_engine" / "arga_nest_core.pyd"
NEST_CORE_DIR = ROOT / "native" / "ArgaNestCore"
NEST_WORKER_EXE = ROOT / "native" / "bin" / "ArgaNestWorker.exe"
FIXTURA_AMADA_DIR = ROOT / "FIXTURA AMADA"
FIXTURA_AMADA_DXFS = (
    FIXTURA_AMADA_DIR / "Fixtura 2.DXF",
    FIXTURA_AMADA_DIR / "FICSTURA MEJORADA CORTE BUENO .25IN.DXF",
)
STEP_EXPORT_CONFIG = ROOT / "_config" / "step_export_folders.json"
NEST_RUNTIME_CONFIG = ROOT / "_config" / "nest_runtime.json"
CUT_GAPS_CONFIG = ROOT / "_config" / "cut_gaps_table.json"
AMADA_BARRENOS_CONFIG = ROOT / "_config" / "amada_barrenos_catalog.json"
CYPTUBE_BRIDGE_CONFIG = ROOT / "_config" / "cyptube_bridge.json"
NEST_ENGINE_CONFIG_JSON = ROOT / "configuracion_nesting.json"
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
    "reporte_pdf_lista_largos",
    # Largos + material requerido (NESTEO DE LARGOS / MRL)
    "catalogo_largos",
    "lista_largos_material_requerido",
    "interface.largos_nesting_service",
    "modules.lista_largos_importer",
    # Bootstrap / native ANS C++
    "modules.win_dll_bootstrap",
    "modules.nesting_engine.arga_nest_core",
    "modules.nesting_engine.arga_nest_core_bridge",
    "modules.nesting_engine.arga_nest_worker_client",
    "modules.nesting_engine.algorithm_cpp",
    "modules.nesting_engine.compact_lite",
    "modules.nesting_engine.ai_ranker",
    "modules.nesting_engine.ai_heuristic",
    "modules.nesting_engine.ai_telemetry",
    "modules.nesting_engine.venom_ai",
    "modules.nesting_engine.nest_cuda",
    "modules.nesting_engine.step_export_prefs",
    "modules.nesting_engine.nest_engine_config",
    "modules.nesting_engine.engine_registry",
    "modules.nesting_engine.giga_cal11_galv",
    "modules.nesting_engine.engines.giga_cal11_galv",
    "modules.nesting_engine.nest_runtime_contract",
    "modules.nesting_engine.nest_runtime_prefs",
    "modules.tank_step_feedstock",
    "modules.tank_step_feedstock.discover",
    "modules.tank_step_feedstock.pipeline",
    "modules.tank_step_feedstock.plate_dxf",
    "modules.nesting_engine.nest_remote_client",
    "modules.nesting_engine.nest_engine_job",
    "modules.nesting_engine.nest_executor",
    "modules.nesting_engine.cut_gaps_table",
    "modules.nesting_engine.exporter",
    "modules.dxf_export",
    "modules.dxf_export.amada_fixture",
    "modules.dxf_export.amada_esp",
    "modules.dxf_export.cobre",
    "modules.dxf_export.cobre_nest",
    "modules.dxf_export.cyptube_vertical",
    "modules.dxf_export.cyptube_bridge",
    "modules.dxf_export.dispatcher",
    "modules.dxf_export.laser",
    "modules.dxf_export.plasma",
    "modules.ls_ready_paso1",
    "modules.ls_ready_paso1.bridge",
    "modules.dxf_mark",
    "modules.dxf_mark.pipeline",
    "modules.cobre_step_fuentes",
    "modules.herinox_catalog_cache",
    "modules.app_auto_update",
    # Qt — ventana principal + extras
    "interface.qt.main_window",
    "interface.qt.theme",
    "interface.qt.thread_bridge",
    "interface.qt.progress_dialog",
    "interface.qt.export_progress_dialog",
    "interface.qt.layout_helpers",
    "interface.qt.ui_scale",
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
    "interface.qt.dxf_qt_renderer",
    "interface.qt.dxf_ezdxf_draw",
    "interface.qt.curve_refine",
    "interface.qt.dxf_hifi_path",
    "interface.qt.mpl_utils",
    "interface.qt.visor_diag",
    # Qt — tabs + mixins
    "interface.qt.tabs.tab_files",
    "interface.qt.tabs.tab_parts",
    "interface.qt.tabs.tab_sheets",
    "interface.qt.tabs.tab_nesting",
    "interface.qt.tabs.tab_nesting_ui",
    "interface.qt.tabs._mixin_nesting_calc",
    "interface.qt.tabs._mixin_plate_mgmt",
    "interface.qt.tabs._mixin_export",
    "interface.qt.tabs._mixin_lote_edit",
    "interface.qt.tabs._mixin_transfer",
    # Qt — diálogos y widgets (largos + nesting)
    "interface.qt.dialogs.nesting_modals",
    "interface.qt.dialogs.lote_editor",
    "interface.qt.dialogs.largos_nesting_modal",
    "interface.qt.dialogs.password_prompt",
    "interface.qt.dialogs.step_viewer",
    "interface.qt.dialogs.crear_steps",
    "interface.qt.dialogs.nest_sim_lab",
    "interface.qt.dialogs.nest_sim_timeline",
    "interface.qt.dialogs.support_inbox",
    # CAD (OCCT)/engine — Crear STEPs / Ver STEP (sys.path runtime + frozen)
    "engine",
    "engine.step_paths",
    "engine.dxf_to_step",
    "engine.occt_runtime",
    "engine.local_staging",
    "engine.step_io",
    "interface.qt.widgets.herinox_switch",
    "interface.qt.widgets.largos_tira_canvas",
    "interface.qt.widgets.largos_perfil_draw",
    "interface.qt.widgets.cad_nav_overlay",
    "interface.qt.widgets.nesting_ribbon",
    # Soporte UI / metadatos
    "interface.autodxf_metadata",
    "interface.parts_catalog",
    "interface.material_colors",
    # Motor nesting + placas
    "modules.nesting_engine",
    "modules.nesting_engine.manager",
    "modules.nesting_engine.algorithm_bridge",
    "modules.nesting_engine.nest_optimization",
    "modules.nesting_engine.cu_largos_nesting",
    "modules.nesting_engine.cu_amada_validacion",
    "modules.nesting_engine.sheet_integrity",
    "modules.nesting_engine.efficiency_metrics",
    "modules.nesting_engine.rtz_overlays",
    "modules.nesting_engine.geometry_parser",
    "modules.nesting_engine.api_client",
    "modules.consulta_herinox_bridge",
    "modules.herinox_sync",
    "modules.arga_gauge_snap",
    "modules.sheets_manager",
    "modules.processed_layers",
    "modules.scanner",
    "modules.nest_exporter",
    "despachador_nocturno",
    "freecad_runner",
    "modules.plates_inventory",
    "modules.plasma_compensator",
    "modules.plasma_occt_offset",
    "modules.plasma_offset_clipper",
    "modules.plasma_offset2d",
    "modules.plasma_dxf_export",
    "OCP",
    "pyclipr",
    "pandas",
    "openpyxl",
    "reportlab",
    "ezdxf",
    "numpy",
)

COLLECT_SUBMODULES = (
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shapely",
    "OCP",
    "matplotlib.backends.backend_qtagg",
    "modules.nesting_engine",
    "modules.tank_step_feedstock",
    "modules.dxf_export",
    "modules.dxf_mark",
    "interface.qt",
    "engine",
)

# Archivos que deben existir antes de empaquetar (ARGA NESTING SUITE actual).
CRITICAL_SUITE_FILES = (
    MAIN_PY,
    ROOT / "config.py",
    ROOT / "catalogo_largos.py",
    ROOT / "lista_largos_material_requerido.py",
    ROOT / "interface" / "largos_nesting_service.py",
    ROOT / "interface" / "qt" / "main_window.py",
    ROOT / "interface" / "qt" / "dialogs" / "largos_nesting_modal.py",
    ROOT / "interface" / "qt" / "dialogs" / "step_viewer.py",
    ROOT / "interface" / "qt" / "dialogs" / "crear_steps.py",
    ROOT / "interface" / "qt" / "dialogs" / "support_inbox.py",
    ROOT / "CAD (OCCT)" / "engine" / "__init__.py",
    ROOT / "CAD (OCCT)" / "engine" / "step_paths.py",
    ROOT / "interface" / "qt" / "widgets" / "largos_tira_canvas.py",
    ROOT / "interface" / "qt" / "widgets" / "largos_perfil_draw.py",
    ROOT / "interface" / "qt" / "widgets" / "herinox_switch.py",
    ROOT / "modules" / "win_dll_bootstrap.py",
    ROOT / "modules" / "nesting_engine" / "algorithm_bridge.py",
    ROOT / "modules" / "nesting_engine" / "arga_nest_core_bridge.py",
    ROOT / "modules" / "nesting_engine" / "arga_nest_worker_client.py",
    ROOT / "modules" / "nesting_engine" / "nest_optimization.py",
    ROOT / "modules" / "nesting_engine" / "cu_largos_nesting.py",
    ROOT / "modules" / "nesting_engine" / "compact_lite.py",
    ROOT / "modules" / "nesting_engine" / "exporter.py",
    ROOT / "modules" / "nesting_engine" / "cut_gaps_table.py",
    ROOT / "modules" / "dxf_export" / "amada_fixture.py",
    ROOT / "modules" / "dxf_export" / "amada_esp.py",
    ROOT / "modules" / "dxf_export" / "cyptube_vertical.py",
    ROOT / "modules" / "dxf_export" / "cyptube_bridge.py",
    ROOT / "modules" / "processed_layers.py",
    ROOT / "modules" / "ls_ready_paso1" / "bridge.py",
    ROOT / "modules" / "ls_ready_paso1" / "UF1_clasificador" / "run_ls_ready_flow.py",
    ROOT / "modules" / "ls_ready_paso1" / "UF2_clasificador" / "run_ls_ready_flow.py",
    ROOT / "modules" / "plasma_occt_offset.py",
    ROOT / "modules" / "plasma_offset_clipper.py",
    NEST_CORE_PS1,
    FIXTURA_AMADA_DXFS[0],
    FIXTURA_AMADA_DXFS[1],
    STEP_EXPORT_CONFIG,
    NEST_RUNTIME_CONFIG,
    CUT_GAPS_CONFIG,
    AMADA_BARRENOS_CONFIG,
    CYPTUBE_BRIDGE_CONFIG,
)

SMOKE_IMPORT_MODULES = (
    "catalogo_largos",
    "lista_largos_material_requerido",
    "interface.largos_nesting_service",
    "interface.qt.dialogs.largos_nesting_modal",
    "interface.qt.dialogs.support_inbox",
    "interface.qt.dialogs.crear_steps",
    "interface.qt.dialogs.step_viewer",
    "interface.qt.ui_scale",
    "interface.qt.widgets.largos_perfil_draw",
    "engine",
    "engine.step_paths",
    "modules.win_dll_bootstrap",
    "modules.nesting_engine.algorithm_bridge",
    "modules.nesting_engine.arga_nest_core_bridge",
    "modules.nesting_engine.arga_nest_worker_client",
    "modules.nesting_engine.nest_runtime_prefs",
    "modules.nesting_engine.nest_executor",
    "modules.nesting_engine.cut_gaps_table",
    "modules.nesting_engine.giga_cal11_galv",
    "modules.nesting_engine.manager",
    "modules.plasma_occt_offset",
    "modules.plasma_offset_clipper",
    "modules.dxf_export.amada_fixture",
    "modules.dxf_export.amada_esp",
    "modules.dxf_export.cobre_nest",
    "modules.dxf_export.cyptube_vertical",
    "modules.dxf_export.cyptube_bridge",
    "modules.nest_exporter",
    "modules.processed_layers",
    "modules.ls_ready_paso1",
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
    for p in (ROOT, ROOT / "interface", ROOT / "CAD (OCCT)"):
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


def _find_nest_core_pyd() -> Path | None:
    """Localiza arga_nest_core*.pyd junto al paquete nesting_engine."""
    engine_dir = ROOT / "modules" / "nesting_engine"
    exact = engine_dir / "arga_nest_core.pyd"
    if exact.is_file():
        return exact
    matches = sorted(engine_dir.glob("arga_nest_core*.pyd"))
    return matches[0] if matches else None


def _find_venom_core_pyds() -> list[Path]:
    engine_dir = ROOT / "modules" / "nesting_engine"
    return sorted(engine_dir.glob("venom_core*.pyd"))


def _find_worker_exe() -> Path | None:
    candidates = [
        NEST_WORKER_EXE,
        NEST_CORE_DIR / "build" / "Release" / "ArgaNestWorker.exe",
        NEST_CORE_DIR / "build" / "ArgaNestWorker.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _nest_core_usable() -> bool:
    pyd = _find_nest_core_pyd()
    if pyd is None or not pyd.is_file():
        return False
    _ensure_build_import_path()
    try:
        from modules.nesting_engine import arga_nest_core_bridge as bridge

        return bool(bridge.core_available())
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


def _nest_core_sources_newer_than_pyd() -> bool:
    pyd = _find_nest_core_pyd()
    if pyd is None or not pyd.is_file():
        return True
    pyd_mtime = pyd.stat().st_mtime
    search_roots = (NEST_CORE_DIR, ROOT / "native" / "ArgaNestWorker", CPP_ENGINE_CPP_DIR)
    for base in search_roots:
        if not base.is_dir():
            continue
        for pattern in ("*.cpp", "*.hpp", "*.h", "*.cu", "CMakeLists.txt"):
            for path in base.rglob(pattern):
                if "build" in path.parts:
                    continue
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


def _needs_nest_core_rebuild(*, force_core: bool, skip_core: bool) -> bool:
    if skip_core:
        return False
    if force_core:
        return True
    if _find_nest_core_pyd() is None:
        return True
    if not _nest_core_usable():
        print("[INFO] arga_nest_core.pyd no carga con este Python; se recompilará.")
        return True
    if _find_worker_exe() is None:
        print("[INFO] Falta ArgaNestWorker.exe; se recompilará ArgaNestCore.")
        return True
    if _nest_core_sources_newer_than_pyd():
        print("[INFO] Fuentes ArgaNestCore más recientes que el .pyd; se recompilará.")
        return True
    return False


def ensure_ans_closed_for_release() -> None:
    """Impide --release con ANS / FreeCAD abiertos (bloquean .pyd y dejan zip incompleto)."""
    try:
        import psutil  # type: ignore
    except Exception:
        # Fallback sin psutil: tasklist + filtrado grosero.
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        blob = (out.stdout or "").lower()
        hits = []
        for needle in (
            "arga nesting suite.exe",
            "freecad.exe",
            "freecadcmd.exe",
        ):
            if needle in blob:
                hits.append(needle)
        # main.py: solo avisar si hay python con cmdline (no fiable vía tasklist)
        if hits:
            raise RuntimeError(
                "Cierra el ANS / FreeCAD antes del Release. Procesos detectados: "
                + ", ".join(hits)
            )
        print("[OK] ANS cerrado (chequeo tasklist; psutil no disponible).")
        return

    blockers: list[str] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmd = " ".join(proc.info.get("cmdline") or []).lower()
        except (psutil.Error, TypeError):
            continue
        if name in {"arga nesting suite.exe", "freecad.exe", "freecadcmd.exe"}:
            blockers.append(f"{name} pid={proc.info.get('pid')}")
            continue
        if "main.py" in cmd and "new arga nesting suite" in cmd.replace("/", "\\"):
            blockers.append(f"python main.py pid={proc.info.get('pid')}")
    if blockers:
        raise RuntimeError(
            "Cierra el ANS / FreeCAD antes del Release:\n  - " + "\n  - ".join(blockers)
        )
    print("[OK] ANS cerrado (ningún proceso Suite/FreeCAD activo).")


def validate_suite_manifest():
    """Falla temprano si faltan piezas del ARGA NESTING SUITE actual."""
    missing = [str(p.relative_to(ROOT)) for p in CRITICAL_SUITE_FILES if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Faltan archivos críticos del suite antes de empaquetar:\n  - "
            + "\n  - ".join(missing)
        )
    print(f"[OK] Manifiesto del suite: {len(CRITICAL_SUITE_FILES)} archivos críticos presentes.")


def smoke_test_imports(*, require_core: bool = True):
    """Importa módulos clave (largos, Qt, motor legacy + ArgaNestCore) en el intérprete de build."""
    _ensure_build_import_path()
    for mod in SMOKE_IMPORT_MODULES:
        importlib.import_module(mod)
        print(f"[OK] import {mod}")
    try:
        from modules.nesting_engine.algorithm_bridge import engine_name

        print(f"[OK] motor nesting legacy: {engine_name()}")
    except Exception as exc:
        raise RuntimeError(
            "El motor C++ legacy no está disponible. Compila algorithm_cpp.pyd antes de empaquetar."
        ) from exc
    try:
        from modules.nesting_engine import arga_nest_core_bridge as bridge

        st = bridge.core_status()
        print(
            f"[OK] ArgaNestCore: active={st.get('active')} version={st.get('version')} "
            f"worker={((st.get('worker') or {}).get('exe_exists'))}"
        )
        if require_core and not bridge.core_available():
            raise RuntimeError(st.get("load_error") or "arga_nest_core no cargó")
    except Exception as exc:
        if require_core:
            raise RuntimeError(
                "ArgaNestCore no está disponible. Compila via native/build_arga_nest_core.ps1."
            ) from exc
        print(f"[WARN] ArgaNestCore smoke omitido/falló: {exc}")
    from modules.dxf_export.amada_fixture import _fixture_dir, AMADA_FIXTURE_CATALOG

    fx_dir = _fixture_dir()
    for spec in AMADA_FIXTURE_CATALOG:
        path = fx_dir / spec.filename
        if not path.is_file():
            raise FileNotFoundError(f"Falta DXF de fixtura Amada: {path}")
    print(f"[OK] Fixturas Amada ({len(AMADA_FIXTURE_CATALOG)}) en {fx_dir}")


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
        print(f"[OK] Motor C++ legacy existente{note}: {CPP_ENGINE_PYD}")
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
    print(f"[OK] Motor C++ legacy listo: {CPP_ENGINE_PYD}")
    return CPP_ENGINE_PYD


def ensure_arga_nest_core(
    skip: bool = False,
    allow_missing: bool = False,
    force_core: bool = False,
    enable_cuda: bool = True,
) -> tuple[Path | None, Path | None]:
    """Compila arga_nest_core.pyd + ArgaNestWorker.exe (main.py los activa por defecto)."""
    existing_pyd = _find_nest_core_pyd()
    existing_worker = _find_worker_exe()
    if (
        existing_pyd is not None
        and _nest_core_usable()
        and existing_worker is not None
        and not _nest_core_sources_newer_than_pyd()
        and (skip or not force_core)
    ):
        note = " (--force-core para recompilar)" if not skip else ""
        print(f"[OK] ArgaNestCore existente{note}: {existing_pyd}")
        print(f"[OK] ArgaNestWorker existente: {existing_worker}")
        return existing_pyd, existing_worker

    if skip:
        if existing_pyd is not None and existing_worker is not None:
            print(f"[OK] --skip-core pyd={existing_pyd.name}")
            print(f"[OK] --skip-core worker={existing_worker.name}")
            return existing_pyd, existing_worker
        msg = "[ERROR] --skip-core y faltan arga_nest_core.pyd y/o ArgaNestWorker.exe."
        if allow_missing:
            print(f"[WARN] {msg}")
            return existing_pyd, existing_worker
        raise FileNotFoundError(msg)

    if not NEST_CORE_PS1.exists():
        raise FileNotFoundError(f"No se encontró script de build ArgaNestCore: {NEST_CORE_PS1}")

    ps_cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(NEST_CORE_PS1),
        "-PythonExe",
        sys.executable,
    ]
    if force_core:
        ps_cmd.append("-Clean")
    if enable_cuda:
        ps_cmd.append("-EnableCuda")
    else:
        ps_cmd.append("-DisableCuda")

    try:
        _run(ps_cmd, cwd=ROOT)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Falló la compilación de ArgaNestCore / ArgaNestWorker. "
            "Revisa CMake/MSVC y native/build_arga_nest_core.ps1."
        ) from exc

    pyd = _find_nest_core_pyd()
    worker = _find_worker_exe()
    if pyd is None or not pyd.is_file():
        raise FileNotFoundError("No se generó arga_nest_core.pyd tras build_arga_nest_core.ps1")
    if worker is None or not worker.is_file():
        raise FileNotFoundError("No se generó ArgaNestWorker.exe tras build_arga_nest_core.ps1")
    if not _nest_core_usable():
        raise RuntimeError(
            f"{pyd.name} se generó pero no carga con {sys.executable}. "
            "Reintenta con --force-core."
        )
    print(f"[OK] ArgaNestCore listo: {pyd}")
    print(f"[OK] ArgaNestWorker listo: {worker}")
    return pyd, worker


def _pyinstaller_collect_args() -> list[str]:
    args: list[str] = []
    for mod in HIDDEN_IMPORTS:
        args += ["--hidden-import", mod]
    for pkg in COLLECT_SUBMODULES:
        args += ["--collect-submodules", pkg]
    return args


def _pyinstaller_data_args() -> list[str]:
    args: list[str] = []
    # Recursos empaquetados del bundle. Los mutables van a `defaults/<rel>`
    # dentro del bundle (_MEIPASS/defaults/…) — de ahí los siembra
    # `config.asegurar_archivo_persistente` la primera vez al data_dir del
    # usuario. Nunca se leen directamente en runtime, siempre pasando por
    # el data_dir persistente.
    data_pairs = [
        (SPLASH_JPEG, "."),
        (MACRO, "."),
        (ROOT / "modules", "modules"),
        (ROOT / "interface", "interface"),
        (ROOT / "CAD (OCCT)" / "engine", "engine"),
        (ROOT / "assets", "assets"),
        (ROOT / "_config", "_config"),
        # Plantillas dentro del bundle (canónicas para bootstrap del data_dir).
        (ROOT / "inventario_remanentes.csv", "defaults"),
        (NEST_ENGINE_CONFIG_JSON, "defaults"),
        (STEP_EXPORT_CONFIG, "defaults/_config"),
        (NEST_RUNTIME_CONFIG, "defaults/_config"),
        (CUT_GAPS_CONFIG, "defaults/_config"),
        (AMADA_BARRENOS_CONFIG, "defaults/_config"),
        (CYPTUBE_BRIDGE_CONFIG, "defaults/_config"),
    ]
    for src, dest in data_pairs:
        if src.exists():
            args += ["--add-data", f"{src};{dest}"]
        elif src.name == "inventario_remanentes.csv":
            print("[WARN] inventario_remanentes.csv no encontrado; se omite del bundle.")
        elif src.name == "configuracion_nesting.json":
            print("[WARN] configuracion_nesting.json no encontrado; se omite del bundle.")
        elif src.name == "step_export_folders.json":
            print("[WARN] _config/step_export_folders.json no encontrado; se omite del bundle.")
        elif src.name == "nest_runtime.json":
            print("[WARN] _config/nest_runtime.json no encontrado; se omite del bundle.")
        elif src.name == "cut_gaps_table.json":
            print("[WARN] _config/cut_gaps_table.json no encontrado; se omite del bundle.")
        elif src.name == "amada_barrenos_catalog.json":
            print("[WARN] _config/amada_barrenos_catalog.json no encontrado; se omite del bundle.")
        elif src.name == "cyptube_bridge.json":
            print("[WARN] _config/cyptube_bridge.json no encontrado; se omite del bundle.")
    # Solo los DXF de fixtura productivos (no scripts _sim).
    for dxf in FIXTURA_AMADA_DXFS:
        if dxf.is_file():
            args += ["--add-data", f"{dxf};FIXTURA AMADA"]
        else:
            print(f"[WARN] Fixtura Amada ausente: {dxf.name}")
    return args


def _pyinstaller_binary_args(
    cpp_pyd: Path | None,
    nest_core_pyd: Path | None = None,
    worker_exe: Path | None = None,
) -> list[str]:
    args: list[str] = []
    for pyd in (cpp_pyd, nest_core_pyd, *_find_venom_core_pyds()):
        if pyd is None or not pyd.is_file():
            continue
        # Evitar duplicar el mismo path si core == cpp (no debería).
        args += ["--add-binary", f"{pyd};modules/nesting_engine"]
    if worker_exe is not None and worker_exe.is_file():
        args += ["--add-binary", f"{worker_exe};native/bin"]
    # Dedup preservando orden
    seen: set[str] = set()
    uniq: list[str] = []
    i = 0
    while i < len(args):
        # pairs: --add-binary, value
        flag, val = args[i], args[i + 1]
        if val not in seen:
            seen.add(val)
            uniq += [flag, val]
        i += 2
    return uniq


def verify_build_artifacts(
    exe_path: Path,
    cpp_pyd: Path | None,
    nest_core_pyd: Path | None = None,
    worker_exe: Path | None = None,
    onefile: bool = False,
):
    """Falla si el paquete dist/ no refleja el proyecto actual."""
    if not exe_path.is_file():
        raise FileNotFoundError(f"No se generó el ejecutable: {exe_path}")
    if cpp_pyd is None or not cpp_pyd.is_file():
        raise RuntimeError("Build incompleto: falta algorithm_cpp.pyd en el motor de nesting.")
    if nest_core_pyd is None or not nest_core_pyd.is_file():
        raise RuntimeError("Build incompleto: falta arga_nest_core.pyd (main.py lo activa por defecto).")
    if worker_exe is None or not worker_exe.is_file():
        raise RuntimeError("Build incompleto: falta ArgaNestWorker.exe.")
    sidecar_worker = exe_path.parent / "ArgaNestWorker.exe"
    if not sidecar_worker.is_file():
        raise RuntimeError(f"Build incompleto: falta sidecar {sidecar_worker.name} junto al .exe.")
    fx_dir = exe_path.parent / "FIXTURA AMADA"
    for dxf in FIXTURA_AMADA_DXFS:
        if not (fx_dir / dxf.name).is_file():
            raise RuntimeError(f"Build incompleto: falta sidecar FIXTURA AMADA/{dxf.name}")
    if not onefile:
        # Onedir: los mutables viven como plantillas en <dist>/defaults/.
        defaults_root = exe_path.parent / "defaults"
        for rel in (
            "inventario_remanentes.csv",
            "configuracion_nesting.json",
            "_config/step_export_folders.json",
            "_config/nest_runtime.json",
            "_config/cut_gaps_table.json",
            "_config/amada_barrenos_catalog.json",
            "_config/cyptube_bridge.json",
        ):
            p = defaults_root / rel
            if not p.is_file():
                raise RuntimeError(
                    f"Build incompleto (onedir): falta plantilla defaults/{rel} "
                    "para bootstrap del data_dir del usuario."
                )
    manifest = exe_path.parent / "arga_build_manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not data.get("cpp_engine"):
            raise RuntimeError("Manifiesto de build sin motor C++ legacy empaquetado.")
        if not data.get("arga_nest_core"):
            raise RuntimeError("Manifiesto de build sin ArgaNestCore empaquetado.")
        if not data.get("arga_nest_worker"):
            raise RuntimeError("Manifiesto de build sin ArgaNestWorker empaquetado.")
    print(
        f"[OK] Artefactos verificados: {exe_path.name} + algorithm_cpp + "
        "arga_nest_core + worker + fixturas + sidecars."
    )


def build_exe(
    name: str,
    onefile: bool = True,
    cpp_pyd: Path | None = None,
    nest_core_pyd: Path | None = None,
    worker_exe: Path | None = None,
):
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
        "--paths",
        str(ROOT / "CAD (OCCT)"),
    ]
    cmd += _pyinstaller_collect_args()
    cmd += _pyinstaller_data_args()
    cmd += _pyinstaller_binary_args(cpp_pyd, nest_core_pyd, worker_exe)
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


def seed_persistent_sidecars(
    exe_path: Path,
    worker_exe: Path | None = None,
    onefile: bool = False,
):
    """
    Deja los archivos editables / nativos que la app necesita en primer arranque.

    - Mutables (inventario, configuracion_nesting, _config): son *plantillas*.
        - onedir → `<dist>/defaults/…`  (fuente canónica del release; nunca los
          lee el .exe en runtime, sirven de semilla al hacer bootstrap del
          data_dir en cada PC).
        - onefile → `<dist>/…` (junto al .exe) para compat con instalaciones
          legacy que aún esperan sidecars en esa ubicación.
    - Worker y Fixturas: son *inmutables del release*, siempre junto al .exe
      (los lee la app en runtime desde el bundle o su carpeta).
    """
    dist_root = exe_path.parent
    if onefile:
        seed_targets = {
            "inventario_remanentes.csv": dist_root / "inventario_remanentes.csv",
            "configuracion_nesting.json": dist_root / "configuracion_nesting.json",
            "_config/step_export_folders.json": dist_root / "_config" / "step_export_folders.json",
            "_config/nest_runtime.json": dist_root / "_config" / "nest_runtime.json",
            "_config/cut_gaps_table.json": dist_root / "_config" / "cut_gaps_table.json",
            "_config/amada_barrenos_catalog.json": dist_root / "_config" / "amada_barrenos_catalog.json",
            "_config/cyptube_bridge.json": dist_root / "_config" / "cyptube_bridge.json",
        }
    else:
        defaults_root = dist_root / "defaults"
        seed_targets = {
            "inventario_remanentes.csv": defaults_root / "inventario_remanentes.csv",
            "configuracion_nesting.json": defaults_root / "configuracion_nesting.json",
            "_config/step_export_folders.json": defaults_root / "_config" / "step_export_folders.json",
            "_config/nest_runtime.json": defaults_root / "_config" / "nest_runtime.json",
            "_config/cut_gaps_table.json": defaults_root / "_config" / "cut_gaps_table.json",
            "_config/amada_barrenos_catalog.json": defaults_root / "_config" / "amada_barrenos_catalog.json",
            "_config/cyptube_bridge.json": defaults_root / "_config" / "cyptube_bridge.json",
        }
    sources = {
        "inventario_remanentes.csv": ROOT / "inventario_remanentes.csv",
        "configuracion_nesting.json": NEST_ENGINE_CONFIG_JSON,
        "_config/step_export_folders.json": STEP_EXPORT_CONFIG,
        "_config/nest_runtime.json": NEST_RUNTIME_CONFIG,
        "_config/cut_gaps_table.json": CUT_GAPS_CONFIG,
        "_config/amada_barrenos_catalog.json": AMADA_BARRENOS_CONFIG,
        "_config/cyptube_bridge.json": CYPTUBE_BRIDGE_CONFIG,
    }
    for rel, dst in seed_targets.items():
        src = sources[rel]
        if not src.is_file():
            print(f"[WARN] Sin plantilla: {src.name}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_file():
            print(f"[OK] Plantilla existente (sin tocar): {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"[OK] Plantilla sembrada: {dst}")

    # Worker: siempre refrescar desde el build (binario de versión del producto).
    wsrc = worker_exe if worker_exe and worker_exe.is_file() else _find_worker_exe()
    if wsrc and wsrc.is_file():
        for dst in (
            dist_root / "ArgaNestWorker.exe",
            dist_root / "native" / "bin" / "ArgaNestWorker.exe",
        ):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(wsrc, dst)
            print(f"[OK] Worker sembrado: {dst}")
    else:
        print("[WARN] Sin ArgaNestWorker.exe para sembrar junto al .exe")

    # Icon Handler: siempre en dist/icon_handler (aunque .arganest ya esté
    # registrado en esta PC). Sin esto el checklist del release marca FALTA.
    try:
        local_icon = Path(os.environ.get("LOCALAPPDATA", "")) / "ArgaNesting"
        deployed = _deploy_icon_assets(dist_root, local_icon)
        if deployed.is_file() and (dist_root / "icon_handler" / "ArgaIconHandler.dll").is_file():
            print(f"[OK] Icon Handler sembrado: {dist_root / 'icon_handler' / 'ArgaIconHandler.dll'}")
        else:
            print("[WARN] Icon Handler no quedó en dist/icon_handler (DLL ausente).")
    except Exception as exc:
        print(f"[WARN] No se pudo sembrar Icon Handler en dist: {exc}")

    # Script de swap del updater: lo ejecuta PowerShell desde disco después de
    # que el .exe se cierre. Debe existir como archivo real (no dentro de
    # _MEIPASS) para poder ser invocado con `-File`.
    apply_switch_ps1 = ROOT / "tools" / "arga_apply_switch.ps1"
    if apply_switch_ps1.is_file():
        for dst in (
            dist_root / "arga_apply_switch.ps1",
            dist_root / "tools" / "arga_apply_switch.ps1",
        ):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(apply_switch_ps1, dst)
        print(f"[OK] arga_apply_switch.ps1 sembrado (tools/ + raíz de dist)")
    else:
        print("[WARN] Falta tools/arga_apply_switch.ps1 (updater no podrá hacer swap).")

    # Fixturas Amada: necesarios para export AMADA/FIXTURA en PCs sin el repo.
    fx_dst = dist_root / "FIXTURA AMADA"
    fx_dst.mkdir(parents=True, exist_ok=True)
    for dxf in FIXTURA_AMADA_DXFS:
        if not dxf.is_file():
            print(f"[WARN] Sin fixtura: {dxf.name}")
            continue
        dst = fx_dst / dxf.name
        shutil.copy2(dxf, dst)
        print(f"[OK] Fixtura sembrada: {dst.name}")


def _calendar_version(now: datetime | None = None) -> str:
    """Versión calendario YYYY.MM.DD (base para releases sin colisión de tag)."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y.%m.%d")


def write_build_manifest(
    exe_path: Path,
    cpp_pyd: Path | None,
    nest_core_pyd: Path | None = None,
    worker_exe: Path | None = None,
    onefile: bool = False,
) -> Path:
    git_commit = ""
    git_branch = ""
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
    try:
        out_b = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if out_b.returncode == 0:
            git_branch = str(out_b.stdout or "").strip()
    except Exception:
        pass

    core_ver = None
    try:
        _ensure_build_import_path()
        from modules.nesting_engine import arga_nest_core_bridge as bridge

        core_ver = (bridge.core_status() or {}).get("version")
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    manifest = {
        "app": "ARGA NESTING SUITE",
        "version": _calendar_version(now),
        "layout": "onefile" if onefile else "onedir",
        "built_at_utc": now.isoformat(),
        "git_commit": git_commit,
        "git_commit_short": (git_commit or "")[:8],
        "git_branch": git_branch or "main",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "exe": str(exe_path.resolve()),
        "cpp_engine": bool(cpp_pyd and cpp_pyd.is_file()),
        "arga_nest_core": bool(nest_core_pyd and nest_core_pyd.is_file()),
        "arga_nest_core_version": core_ver,
        "arga_nest_worker": bool(worker_exe and worker_exe.is_file()),
        "amada_fixtures": [p.name for p in FIXTURA_AMADA_DXFS if p.is_file()],
        "venom_core_pyds": [p.name for p in _find_venom_core_pyds()],
        "hidden_imports": len(HIDDEN_IMPORTS),
        "collect_submodules": list(COLLECT_SUBMODULES),
        "features": [
            "nesting_placas_cpp",
            "arga_nest_core",
            "arga_nest_worker",
            "nesting_largos_mrl",
            "nesteo_largos_switches",
            "export_material_requerido_ldg",
            "herinox_catalogo_largos",
            "amada_fixtura_catalogo",
            "compact_lite",
            "ai_ranker_l1",
            "step_export_prefs",
        ],
        "runtime_defaults": {
            "ARGA_NEST_CORE": "0",
            "ARGA_NEST_CUDA": "1",
            "ARGA_NEST_WORKER": "1",
            "ARGA_NEST_AI": "1",
            "ARGA_NEST_COMPACT": "1",
            "ARGA_NEST_VENOM": "0",
        },
        "deploy_notes": [
            f"Copiar '{APP_EXE_NAME}.exe' y sidecars de esta carpeta dist/ al destino.",
            "Incluir ArgaNestWorker.exe (o native/bin/ArgaNestWorker.exe) junto al exe.",
            "Incluir carpeta FIXTURA AMADA/ con los 2 DXF de fixtura.",
            "Incluir arga_archivo_nesteo.ico / _cu.ico / _mix.ico junto al exe.",
            "Opcional: inventario_remanentes.csv, configuracion_nesting.json, _config/.",
            "Requiere red a PostgreSQL nesting (5433) y Herinox (5439) para inventario de placas.",
            "FreeCAD en C:\\Program Files\\FreeCAD 1.0\\ para STEP (si aplica).",
            "CUDA es opcional: si no hay GPU/Toolkit, el core cae a CPU.",
        ],
    }
    out = exe_path.parent / "arga_build_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Manifiesto de build: {out}")
    return out


def _sha256_of_file(path: Path, chunk_size: int = 1 << 20) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _zip_directory(src_dir: Path, dst_zip: Path) -> int:
    """Zippa src_dir/* como raíz del zip (sin incluir src_dir en las paths).

    Retorna el número de entradas escritas. Usa ZIP_DEFLATED nivel 6.
    """
    import zipfile

    dst_zip.parent.mkdir(parents=True, exist_ok=True)
    if dst_zip.exists():
        dst_zip.unlink()
    n = 0
    with zipfile.ZipFile(
        dst_zip,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                p = Path(root) / name
                arc = p.relative_to(src_dir)
                zf.write(p, arcname=str(arc))
                n += 1
    return n


def _release_version_tuple(manifest_data: dict) -> tuple[str, str]:
    """Devuelve (version, commit_short) leyendo el manifest recién escrito."""
    ver = str(manifest_data.get("version") or _calendar_version())
    commit = str(manifest_data.get("git_commit_short") or manifest_data.get("git_commit") or "")
    commit = commit[:8] or "nogit000"
    return ver, commit


def write_release_artifacts(
    exe_path: Path,
    manifest_path: Path,
    notes: str = "",
    min_supported_version: str = "",
) -> tuple[Path, Path]:
    """
    Empaqueta el onedir en `dist/releases/ArgaNestingSuite-<ver>-<commit>.zip`
    y escribe `dist/releases/latest.json`.

    latest.json es lo que el updater consulta:
      { version, commit, sha256, size_bytes, filename, url,
        min_supported_version, layout, notes, published_at_utc }

    `url` queda vacía a propósito: `tools/publish_release.py` la escribe al
    subir el zip al canal (GitHub Releases / UNC). Así el manifest local es
    reproducible sin conocer el destino final.
    """
    dist_dir = exe_path.parent  # dist/<name>/
    if not dist_dir.is_dir():
        raise RuntimeError(f"No se encuentra la carpeta onedir: {dist_dir}")
    if not manifest_path.is_file():
        raise RuntimeError(f"No se encuentra el manifest: {manifest_path}")
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    version, commit_short = _release_version_tuple(manifest_data)

    releases_dir = ROOT / "dist" / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"ArgaNestingSuite-{version}-{commit_short}.zip"
    zip_path = releases_dir / zip_name

    print(f"[INFO] Empaquetando release: {zip_path}")
    started = time.perf_counter()
    entries = _zip_directory(dist_dir, zip_path)
    dur = time.perf_counter() - started
    print(f"[OK] Zip release: {zip_path} ({entries} archivos, {dur:.1f}s)")

    sha = _sha256_of_file(zip_path)
    size = zip_path.stat().st_size
    latest = {
        "app": "ARGA NESTING SUITE",
        "version": version,
        "commit": manifest_data.get("git_commit") or "",
        "commit_short": commit_short,
        "layout": manifest_data.get("layout") or "onedir",
        "filename": zip_name,
        "url": "",
        "sha256": sha,
        "size_bytes": int(size),
        "min_supported_version": str(min_supported_version or "").strip(),
        "published_at_utc": "",
        "notes": (notes or "").strip(),
    }
    latest_path = releases_dir / "latest.json"
    latest_path.write_text(json.dumps(latest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] latest.json: {latest_path}")
    print(f"[OK] sha256={sha}  size={size:,} bytes")
    print(
        "[NEXT] Publica el zip al canal (GitHub Releases / UNC) con "
        "`python tools/publish_release.py`; ese script escribe 'url' y "
        "'published_at_utc' en latest.json."
    )
    return zip_path, latest_path


def print_deploy_checklist(exe_path: Path, onefile: bool = False):
    dist = exe_path.parent
    checks = [
        ("Ejecutable", exe_path.is_file()),
        ("arga_build_manifest.json", (dist / "arga_build_manifest.json").is_file()),
        ("ArgaNestWorker.exe", (dist / "ArgaNestWorker.exe").is_file()),
        ("FIXTURA AMADA/Fixtura 2.DXF", (dist / "FIXTURA AMADA" / "Fixtura 2.DXF").is_file()),
        (
            "FIXTURA AMADA/FICSTURA…DXF",
            (dist / "FIXTURA AMADA" / "FICSTURA MEJORADA CORTE BUENO .25IN.DXF").is_file(),
        ),
        ("arga_archivo_nesteo.ico", (dist / "arga_archivo_nesteo.ico").is_file()),
        ("arga_archivo_nesteo_cu.ico", (dist / "arga_archivo_nesteo_cu.ico").is_file()),
        ("arga_archivo_nesteo_mix.ico", (dist / "arga_archivo_nesteo_mix.ico").is_file()),
        ("icon_handler/ArgaIconHandler.dll", (dist / "icon_handler" / "ArgaIconHandler.dll").is_file()),
        ("tools/arga_apply_switch.ps1", (dist / "tools" / "arga_apply_switch.ps1").is_file()),
    ]
    if not onefile:
        # En onedir, las plantillas viven en defaults/ para bootstrap del data_dir.
        checks.extend([
            (
                "defaults/inventario_remanentes.csv",
                (dist / "defaults" / "inventario_remanentes.csv").is_file(),
            ),
            (
                "defaults/configuracion_nesting.json",
                (dist / "defaults" / "configuracion_nesting.json").is_file(),
            ),
            (
                "defaults/_config/step_export_folders.json",
                (dist / "defaults" / "_config" / "step_export_folders.json").is_file(),
            ),
            (
                "defaults/_config/nest_runtime.json",
                (dist / "defaults" / "_config" / "nest_runtime.json").is_file(),
            ),
            (
                "defaults/_config/cut_gaps_table.json",
                (dist / "defaults" / "_config" / "cut_gaps_table.json").is_file(),
            ),
            (
                "defaults/_config/amada_barrenos_catalog.json",
                (dist / "defaults" / "_config" / "amada_barrenos_catalog.json").is_file(),
            ),
            (
                "defaults/_config/cyptube_bridge.json",
                (dist / "defaults" / "_config" / "cyptube_bridge.json").is_file(),
            ),
        ])
    layout = "onefile" if onefile else "onedir"
    print(f"\n=== CHECKLIST DESPLIEGUE ({layout}) ===")
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
        description=(
            "Construye ARGA NESTING SUITE (.exe) con motor legacy, ArgaNestCore, "
            "Worker, largos/MRL, fixturas Amada y sidecars de despliegue."
        )
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
        "--skip-core",
        action="store_true",
        help="No compila arga_nest_core.pyd / ArgaNestWorker (requiere que ya existan).",
    )
    parser.add_argument(
        "--force-core",
        action="store_true",
        help="Recompila ArgaNestCore + Worker aunque ya existan.",
    )
    parser.add_argument(
        "--enable-cuda",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Intenta CUDA al compilar ArgaNestCore (CMake cae a CPU si no hay Toolkit; default: sí).",
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
        help="Permite terminar sin motor C++ legacy (nesting de placas degradado).",
    )
    parser.add_argument(
        "--allow-no-core",
        action="store_true",
        help="Permite terminar sin ArgaNestCore/Worker (main.py caerá a legacy si CORE no carga).",
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
        "--onefile",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Empaqueta un único .exe (PyInstaller --onefile). Por defecto se "
            "genera --onedir: carpeta versionada que sirve de release y update "
            "atómico. Solo usar --onefile para casos legacy."
        ),
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Alias compat: fuerza --onedir (default). Sin efecto si no se pasa --onefile.",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help=(
            "Además del build, produce dist/releases/ArgaNestingSuite-<version>-<commit>.zip "
            "y latest.json listos para publicar (requiere --onedir; se ignora en onefile)."
        ),
    )
    parser.add_argument(
        "--release-notes",
        default="",
        help="Notas del release (una línea) que aparecen en latest.json.notes.",
    )
    parser.add_argument(
        "--release-min-version",
        default="",
        help=(
            "Versión mínima soportada que el updater forzará a actualizar. "
            "Formato YYYY.MM.DD[.N]. Vacío = no forzar."
        ),
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
    if args.release:
        ensure_ans_closed_for_release()

    if not args.skip_deps:
        ensure_build_dependencies()

    needs_cpp = _needs_cpp_rebuild(force_cpp=args.force_cpp, skip_cpp=args.skip_cpp)
    needs_core = _needs_nest_core_rebuild(force_core=args.force_core, skip_core=args.skip_core)
    install_msvc = args.install_msvc
    if (needs_cpp or needs_core) and not _msvc_available():
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

    nest_core_pyd, worker_exe = ensure_arga_nest_core(
        skip=args.skip_core,
        allow_missing=args.allow_no_core,
        force_core=args.force_core,
        enable_cuda=args.enable_cuda,
    )
    if (nest_core_pyd is None or worker_exe is None) and not args.allow_no_core:
        raise RuntimeError(
            "No se puede empaquetar sin arga_nest_core.pyd + ArgaNestWorker.exe. "
            "Quita --allow-no-core o compila native/build_arga_nest_core.ps1."
        )

    if not args.skip_smoke:
        smoke_test_imports(require_core=not args.allow_no_core)
    sync_repo_plates_from_herinox("pre-build")
    onefile_mode = bool(args.onefile)
    if args.onedir and onefile_mode:
        raise SystemExit("No se puede combinar --onefile con --onedir.")
    exe_path = build_exe(
        args.name,
        onefile=onefile_mode,
        cpp_pyd=cpp_pyd,
        nest_core_pyd=nest_core_pyd,
        worker_exe=worker_exe,
    )
    sync_repo_plates_from_herinox("post-build")
    align_plates_inventory_after_build(exe_path)
    seed_persistent_sidecars(exe_path, worker_exe=worker_exe, onefile=onefile_mode)
    manifest_path = write_build_manifest(
        exe_path, cpp_pyd, nest_core_pyd, worker_exe, onefile=onefile_mode
    )
    if not args.allow_no_core:
        verify_build_artifacts(
            exe_path, cpp_pyd, nest_core_pyd, worker_exe, onefile=onefile_mode
        )
    else:
        print("[WARN] Verificación estricta omitida (--allow-no-core).")
    print_deploy_checklist(exe_path, onefile=onefile_mode)
    if args.release:
        if onefile_mode:
            print("[WARN] --release ignorado en modo --onefile (el canal usa onedir).")
        else:
            write_release_artifacts(
                exe_path,
                manifest_path,
                notes=args.release_notes,
                min_supported_version=args.release_min_version,
            )
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
