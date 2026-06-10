import argparse
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Paquetes base críticos del proyecto (validados por uso real en runtime).
BASE_PACKAGES = [
    "customtkinter",
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
    "PySide6",
    "openpyxl",
    "xlrd",
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

# Módulos que se importan en runtime específico y no se instalan con pip directamente.
NON_PIP_IMPORTS = {
    "FreeCAD",
    "FreeCADGui",
    "ImportGui",
    "importDXF",
    "Part",
}


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


def _collect_top_level_imports(scan_paths: list[Path]) -> set[str]:
    imports: set[str] = set()
    for base in scan_paths:
        if not base.exists():
            continue
        for py_file in base.rglob("*.py"):
            if ".venv" in py_file.parts or "__pycache__" in py_file.parts:
                continue
            try:
                code = py_file.read_text(encoding="utf-8")
                tree = ast.parse(code, filename=str(py_file))
            except Exception:
                # Ignora archivos con encoding no estándar o sintaxis temporal rota.
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


def resolve_required_packages(include_optional: bool) -> list[str]:
    scan_paths = [
        ROOT,
        ROOT / "modules",
        ROOT / "interface",
        ROOT / "tools",
    ]
    discovered_imports = _collect_top_level_imports(scan_paths)
    local_modules = _discover_local_top_modules()

    external_imports = sorted(
        imp
        for imp in discovered_imports
        if imp
        and not _is_stdlib(imp)
        and imp not in local_modules
        and imp not in NON_PIP_IMPORTS
        and not imp.startswith("_")
    )

    packages = {_to_package_name(mod) for mod in external_imports}
    packages.update(BASE_PACKAGES)

    if include_optional:
        packages.update({"pyinstaller", "setuptools", "wheel"})

    # Evita intentar instalar nombres que no existen en pip.
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

        if not _is_installed(probe_name):
            missing.append(package)

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
        description="Analiza imports del proyecto e instala dependencias faltantes automáticamente."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo muestra qué instalaría, sin instalar.",
    )
    parser.add_argument(
        "--include-optional",
        action="store_true",
        help="Incluye dependencias opcionales de build (pyinstaller, wheel, setuptools).",
    )
    args = parser.parse_args()

    packages = resolve_required_packages(include_optional=args.include_optional)
    return install_missing(packages, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
