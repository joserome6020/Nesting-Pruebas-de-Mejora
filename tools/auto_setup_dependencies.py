import argparse
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# UI principal: PySide6 (Qt). No incluye customtkinter (solo legacy Tk).
BASE_PACKAGES = [
    "PySide6",
    "pillow",
    "psycopg2-binary",
    "fastapi",
    "uvicorn",
    "pydantic",
    "reportlab",
    "ezdxf",
    "matplotlib",
    "numpy",
    "shapely",
    "pandas",
    "openpyxl",
    "xlrd",
]

# Interfaz Tk antigua (interface/main_window.py, tab_*.py en raíz, etc.).
LEGACY_TK_PACKAGES = ["customtkinter"]

# Dependencias de build / motor C++ (opcionales con --include-optional).
OPTIONAL_BUILD_PACKAGES = [
    "pyinstaller",
    "setuptools",
    "wheel",
    "pybind11",
    "cmake",
]

# Mapeo import_name -> package_name (pip).
IMPORT_TO_PACKAGE = {
    "PIL": "pillow",
    "psycopg2": "psycopg2-binary",
    "cv2": "opencv-python",
    "fitz": "PyMuPDF",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "sklearn": "scikit-learn",
    "OpenGL": "PyOpenGL",
}

NON_PIP_IMPORTS = {
    "FreeCAD",
    "FreeCADGui",
    "ImportGui",
    "importDXF",
    "Part",
    "algorithm_cpp",
}

# Solo se instala con --include-legacy-tk (UI Tk antigua).
LEGACY_ONLY_IMPORTS = {"customtkinter"}

ROOT_ENTRY_FILES = (
    "main.py",
    "api_server.py",
    "config.py",
    "catalogo_largos.py",
    "lista_largos_material_requerido.py",
    "reporte_pdf_nesting.py",
    "reporte_pdf_lista_largos.py",
)

INTERFACE_QT_BRIDGE_FILES = (
    "interface/postgres_connector.py",
    "interface/nesting_workspace.py",
    "interface/utils_nesting.py",
    "interface/autodxf_metadata.py",
    "interface/material_colors.py",
    "interface/largos_nesting_service.py",
)

# Archivos Tk legacy — no se escanean salvo --include-legacy-tk.
LEGACY_TK_REL_PATHS = (
    "interface/main_window.py",
    "interface/tab_files.py",
    "interface/tab_nesting.py",
    "interface/nesting_canvas.py",
    "interface/nesting_modals.py",
    "interface/nesting_lote_editor.py",
    "interface/responsive_layout.py",
    "tab_parts.py",
    "tab_sheets.py",
    "modules/visualizer.py",
)


def _run(cmd: list[str]) -> None:
    print(f"[RUN] {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=str(ROOT))


def _discover_local_top_modules() -> set[str]:
    local_modules: set[str] = set()
    for py_file in ROOT.rglob("*.py"):
        if any(part.startswith(".") for part in py_file.parts):
            continue
        if ".venv" in py_file.parts:
            continue
        rel = py_file.relative_to(ROOT)
        if rel.name == "__init__.py":
            if rel.parent.parts:
                local_modules.add(rel.parent.parts[0])
        else:
            local_modules.add(rel.stem)
            if len(rel.parts) > 1:
                local_modules.add(rel.parts[0])
    return local_modules


def _skip_scan_path(path: Path) -> bool:
    parts = set(path.parts)
    blocked = {"__pycache__", ".venv", "build", "_deps", "third_party", "node_modules"}
    return bool(parts & blocked)


def _iter_project_py_files(include_legacy_tk: bool) -> list[Path]:
    files: list[Path] = []

    def add(path: Path):
        if path.is_file() and path.suffix == ".py":
            files.append(path)

    qt_dir = ROOT / "interface" / "qt"
    if qt_dir.is_dir():
        for py_file in qt_dir.rglob("*.py"):
            if not _skip_scan_path(py_file.relative_to(ROOT)):
                add(py_file)

    modules_dir = ROOT / "modules"
    if modules_dir.is_dir():
        for py_file in modules_dir.rglob("*.py"):
            if not _skip_scan_path(py_file.relative_to(ROOT)):
                add(py_file)

    for name in ROOT_ENTRY_FILES:
        add(ROOT / name)

    for rel in INTERFACE_QT_BRIDGE_FILES:
        add(ROOT / rel)

    if include_legacy_tk:
        for rel in LEGACY_TK_REL_PATHS:
            add(ROOT / rel)

    return files


def _collect_top_level_imports(py_files: list[Path]) -> set[str]:
    imports: set[str] = set()
    for py_file in py_files:
        try:
            code = py_file.read_text(encoding="utf-8")
            tree = ast.parse(code, filename=str(py_file))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    imports.add(node.module.split(".")[0])
    return imports


def _is_stdlib(module_name: str) -> bool:
    std = getattr(sys, "stdlib_module_names", set())
    return module_name in std


def _is_installed(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _to_package_name(import_name: str) -> str:
    return IMPORT_TO_PACKAGE.get(import_name, import_name)


def resolve_required_packages(
    include_optional: bool,
    include_legacy_tk: bool,
) -> list[str]:
    py_files = _iter_project_py_files(include_legacy_tk=include_legacy_tk)
    discovered_imports = _collect_top_level_imports(py_files)
    local_modules = _discover_local_top_modules()

    external_imports = sorted(
        imp
        for imp in discovered_imports
        if imp
        and not _is_stdlib(imp)
        and imp not in local_modules
        and imp not in NON_PIP_IMPORTS
        and not imp.startswith("_")
        and (include_legacy_tk or imp not in LEGACY_ONLY_IMPORTS)
    )

    packages = {_to_package_name(mod) for mod in external_imports}
    packages.update(BASE_PACKAGES)

    if include_legacy_tk:
        packages.update(LEGACY_TK_PACKAGES)

    if include_optional:
        packages.update(OPTIONAL_BUILD_PACKAGES)

    blacklist = {"typing", "tkinter", "sqlite3", "email", "xml", "http"}
    packages = {p for p in packages if p and p not in blacklist}
    return sorted(packages, key=str.lower)


def install_missing(packages: list[str], dry_run: bool) -> int:
    missing: list[str] = []
    for package in packages:
        probe_name = package
        if package == "pillow":
            probe_name = "PIL"
        elif package == "psycopg2-binary":
            probe_name = "psycopg2"
        elif package == "PyYAML":
            probe_name = "yaml"
        elif package == "python-dotenv":
            probe_name = "dotenv"
        elif package == "scikit-learn":
            probe_name = "sklearn"
        elif package == "PyMuPDF":
            probe_name = "fitz"
        elif package == "PySide6":
            probe_name = "PySide6"
        elif package == "pyinstaller":
            probe_name = "PyInstaller"

        if not _is_installed(probe_name):
            missing.append(package)

    print(f"[INFO] UI principal: PySide6 (Qt)")
    print(f"[INFO] Archivos analizados: rutas Qt + modules + entradas del proyecto")
    print(f"[INFO] Paquetes detectados: {len(packages)}")
    print(f"[INFO] Paquetes faltantes: {len(missing)}")
    if missing:
        print("[INFO] Faltantes:")
        for pkg in missing:
            print(f"  - {pkg}")
    else:
        print("[OK] No faltan dependencias de Python.")
        return 0

    if dry_run:
        print("[DRY-RUN] No se instaló nada.")
        return 0

    _run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    _run([sys.executable, "-m", "pip", "install", *missing])
    print("[OK] Instalación completada.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Instala dependencias pip para ARGA NESTING SUITE (UI Qt/PySide6). "
            "Por defecto NO instala customtkinter (solo legacy Tk)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo muestra qué instalaría, sin instalar.",
    )
    parser.add_argument(
        "--include-optional",
        action="store_true",
        help="Incluye build EXE y motor C++ (pyinstaller, pybind11, cmake, wheel, setuptools).",
    )
    parser.add_argument(
        "--include-legacy-tk",
        action="store_true",
        help="Incluye customtkinter y escanea archivos Tk antiguos (interface/tab_*.py).",
    )
    args = parser.parse_args()

    packages = resolve_required_packages(
        include_optional=args.include_optional,
        include_legacy_tk=args.include_legacy_tk,
    )
    return install_missing(packages, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
