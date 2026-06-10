import os
from pathlib import Path
from typing import Iterable, List, Set, Tuple

# Definimos el directorio raíz
ROOT_DIR = Path('.')

# Obtenemos el nombre de la carpeta principal de forma dinámica
CURRENT_FOLDER_NAME = ROOT_DIR.resolve().name

# Construimos el nombre del archivo de salida
OUTPUT_FILENAME = f'Contexto_{CURRENT_FOLDER_NAME}.txt'

# Carpetas que normalmente no aportan al contexto fuente
IGNORED_DIRS: Set[str] = {
    '.git',
    '__pycache__',
    'venv',
    '.venv',
    'env',
    'node_modules',
    '.mypy_cache',
    '.pytest_cache',
    '.idea',
    '.vscode',
    'dist',
    'build',
    '.next',
    '.nuxt',
    'coverage',
}

# Extensiones de archivos que sí conviene consolidar
VALID_EXTENSIONS: Set[str] = {
    '.py', '.html', '.css', '.js', '.ts', '.jsx', '.tsx',
    '.json', '.md', '.txt', '.yml', '.yaml', '.toml', '.ini', '.cfg',
    '.sql', '.xml', '.csv', '.bat', '.ps1', '.sh', '.env', '.example'
}

# Archivos importantes sin extensión o con nombres especiales
VALID_FILENAMES: Set[str] = {
    'dockerfile',
    'makefile',
    'requirements.txt',
    'pyproject.toml',
    'poetry.lock',
    'package.json',
    'package-lock.json',
    'pnpm-lock.yaml',
    'yarn.lock',
    '.env',
    '.env.example',
    '.gitignore',
    '.dockerignore',
    '.editorconfig',
}

# Archivos generados que no conviene volver a incrustar para evitar ruido o recursión
EXCLUDED_OUTPUT_FILES: Set[str] = {
    OUTPUT_FILENAME.lower(),
}


def should_ignore_dir(dirname: str) -> bool:
    return dirname in IGNORED_DIRS


def is_meaningful_code_file(path: Path) -> bool:
    """Define si un archivo debe aparecer en la sección de contenido consolidado."""
    name_lower = path.name.lower()

    if name_lower in EXCLUDED_OUTPUT_FILES:
        return False

    # Nombres especiales (incluye .env.example y archivos sin extensión tipo Dockerfile)
    if name_lower in VALID_FILENAMES:
        return True

    suffixes = [s.lower() for s in path.suffixes]
    if any(s in VALID_EXTENSIONS for s in suffixes):
        return True

    # Último sufijo, por compatibilidad con nombres convencionales
    if path.suffix.lower() in VALID_EXTENSIONS:
        return True

    return False


def safe_read_text(path: Path) -> str:
    encodings = ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252')
    last_error = None
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except Exception as exc:
            last_error = exc
    return f"[Error al leer el archivo: {last_error}]"


def collect_project_files(root_dir: Path) -> Tuple[List[Path], List[Path]]:
    """
    Regresa:
    - all_tree_files: todo lo visible en el árbol (salvo carpetas ignoradas)
    - content_files: solo los archivos significativos a consolidar
    """
    all_tree_files: List[Path] = []
    content_files: List[Path] = []

    for current_root, dirs, files in os.walk(root_dir):
        dirs[:] = sorted(d for d in dirs if not should_ignore_dir(d))
        current_root_path = Path(current_root)

        for filename in sorted(files, key=str.lower):
            full_path = current_root_path / filename
            rel_path = full_path.relative_to(root_dir)
            all_tree_files.append(rel_path)

            if is_meaningful_code_file(full_path):
                content_files.append(rel_path)

    return all_tree_files, content_files


def build_tree(root_dir: Path, files_in_tree: Iterable[Path]) -> str:
    files_by_dir = {}
    dirs_set = {Path('.')}

    for rel_file in files_in_tree:
        parent = rel_file.parent if rel_file.parent != Path('') else Path('.')
        files_by_dir.setdefault(parent, []).append(rel_file.name)

        current = parent
        while current != Path('.'):
            dirs_set.add(current)
            current = current.parent if current.parent != Path('') else Path('.')
        dirs_set.add(Path('.'))

    lines: List[str] = []

    def walk_dir(current_dir: Path, level: int) -> None:
        indent = ' ' * 4 * level
        if current_dir == Path('.'):
            lines.append('📁 ./')
        else:
            lines.append(f"{indent}📂 {current_dir.name}/")

        file_indent = ' ' * 4 * (level + 1)
        for filename in sorted(files_by_dir.get(current_dir, []), key=str.lower):
            lines.append(f"{file_indent}📄 {filename}")

        child_dirs = sorted(
            [d for d in dirs_set if d.parent == current_dir and d != current_dir],
            key=lambda p: str(p).lower(),
        )
        for child in child_dirs:
            walk_dir(child, level + 1)

    walk_dir(Path('.'), 0)
    return '\n'.join(lines)


def write_context_file(root_dir: Path = ROOT_DIR, output_filename: str = OUTPUT_FILENAME) -> None:
    all_tree_files, content_files = collect_project_files(root_dir)
    output_path = root_dir / output_filename

    with output_path.open('w', encoding='utf-8') as f_out:
        f_out.write("=========================================================\n")
        f_out.write("MAPA DEL PROYECTO (ÁRBOL DE DIRECTORIOS)\n")
        f_out.write("=========================================================\n\n")
        f_out.write(build_tree(root_dir, all_tree_files) + "\n\n")

        f_out.write("=========================================================\n")
        f_out.write("CÓDIGO FUENTE DE LOS ARCHIVOS\n")
        f_out.write("=========================================================\n\n")

        included_set = set()
        for rel_path in content_files:
            included_set.add(rel_path.as_posix())
            full_path = root_dir / rel_path
            f_out.write("\n" + "=" * 80 + "\n")
            f_out.write(f"--- ARCHIVO: {rel_path.as_posix()} ---\n")
            f_out.write("=" * 80 + "\n\n")
            f_out.write(safe_read_text(full_path))
            f_out.write("\n")

        # Validación final para blindar el resultado
        expected_set = {p.as_posix() for p in content_files}
        missing_in_dump = sorted(expected_set - included_set)

        f_out.write("\n" + "=" * 80 + "\n")
        f_out.write("RESUMEN DE VALIDACIÓN\n")
        f_out.write("=" * 80 + "\n\n")
        f_out.write(f"Archivos visibles en el árbol: {len(all_tree_files)}\n")
        f_out.write(f"Archivos consolidados con contenido: {len(content_files)}\n")
        f_out.write(f"Archivos omitidos intencionalmente: {', '.join(sorted(EXCLUDED_OUTPUT_FILES))}\n")

        if missing_in_dump:
            f_out.write("\n⚠ FALTARON ARCHIVOS ESPERADOS EN EL VOLCADO:\n")
            for item in missing_in_dump:
                f_out.write(f" - {item}\n")
        else:
            f_out.write("\n✅ Validación correcta: todos los archivos significativos detectados fueron volcados.\n")

    print(f"✅ Contexto generado en: {output_path}")
    print(f"📁 Archivos en árbol: {len(all_tree_files)}")
    print(f"📄 Archivos consolidados: {len(content_files)}")


if __name__ == '__main__':
    write_context_file()