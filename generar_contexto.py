import ast
import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    import tomllib
except Exception:
    tomllib = None


# =========================================================
# CONFIGURACIÓN GENERAL
# =========================================================
ROOT_DIR = Path('.')

OUTPUT_DIRNAME = 'Contexto_Rapido'
OVERVIEW_FILENAME = '00_CONTEXTO_GENERAL.txt'
KEYCODE_FILENAME = '01_CONTEXTO_CODIGO_CLAVE.txt'

# Si algún día quieres manifiesto json, puedes activarlo.
GENERATE_JSON_MANIFEST = False
MANIFEST_FILENAME = '02_CONTEXTO_MANIFIESTO.json'

# Cuántos archivos clave analizar a detalle para el contexto general
MAX_KEY_FILES = 25

# Cuántos archivos pequeños meter completos en el archivo de código clave
MAX_FULL_FILES = 8

# Cuántos archivos grandes meter como extracto estructural
MAX_LARGE_EXCERPTS = 6

# Si un archivo clave mide menos que esto, puede entrar completo al archivo 01
FULL_FILE_MAX_CHARS = 18_000

# Tamaño máximo del archivo 01_CONTEXTO_IA_CODIGO_CLAVE.txt
MAX_CHARS_IN_KEYCODE_FILE = 180_000

# Cuántos archivos relevantes mostrar por carpeta de primer nivel
TOP_FILES_PER_DIR = 5

# Límites de visualización
MAX_IMPORTS_SHOWN = 12
MAX_SIGNATURES_SHOWN = 18
MAX_FRAMEWORKS_SHOWN = 8
DOC_PREVIEW_CHARS = 450
HEAD_PREVIEW_CHARS = 3_500


# =========================================================
# FILTROS
# =========================================================
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
    'bin',
    'obj',
}

VALID_EXTENSIONS: Set[str] = {
    '.py', '.html', '.css', '.js', '.ts', '.jsx', '.tsx',
    '.json', '.md', '.txt', '.yml', '.yaml', '.toml', '.ini', '.cfg',
    '.sql', '.xml', '.csv', '.bat', '.ps1', '.sh', '.env', '.example',
    '.gs', '.cs', '.vb', '.csproj', '.sln'
}

VALID_FILENAMES: Set[str] = {
    'dockerfile',
    'makefile',
    'requirements.txt',
    'pyproject.toml',
    'package.json',
    '.env',
    '.env.example',
    '.gitignore',
    '.dockerignore',
    '.editorconfig',
}

LOCK_FILENAMES: Set[str] = {
    'package-lock.json',
    'pnpm-lock.yaml',
    'yarn.lock',
    'poetry.lock',
}

EXCLUDED_OUTPUT_FILES: Set[str] = {
    OVERVIEW_FILENAME.lower(),
    KEYCODE_FILENAME.lower(),
    MANIFEST_FILENAME.lower(),
}

EXACT_PRIORITY_FILENAMES: Dict[str, int] = {
    'readme.md': 160,
    'main.py': 150,
    'app.py': 145,
    'server.py': 145,
    'run.py': 140,
    'manage.py': 140,
    'index.js': 135,
    'index.ts': 135,
    'package.json': 140,
    'pyproject.toml': 140,
    'requirements.txt': 135,
    'dockerfile': 130,
    'docker-compose.yml': 125,
    'docker-compose.yaml': 125,
    '.env.example': 110,
    '.env': 95,
    'settings.py': 110,
    'config.py': 105,
}

PATH_PRIORITY_KEYWORDS: Dict[str, int] = {
    'api': 40,
    'route': 40,
    'routes': 42,
    'controller': 35,
    'controllers': 35,
    'model': 28,
    'models': 28,
    'schema': 28,
    'schemas': 28,
    'service': 28,
    'services': 28,
    'core': 30,
    'config': 28,
    'settings': 30,
    'database': 26,
    'db': 22,
    'repository': 22,
    'repositories': 22,
    'template': 20,
    'templates': 20,
    'static': 15,
    'view': 18,
    'views': 18,
    'ui': 18,
    'src': 14,
}

LOW_PRIORITY_KEYWORDS: Dict[str, int] = {
    'test': -45,
    'tests': -45,
    '__tests__': -45,
    'spec': -40,
    'migrations': -25,
    'migration': -25,
    'seed': -20,
    'seeds': -20,
    'sample': -20,
    'samples': -20,
    'example': -18,
    'examples': -18,
    'demo': -18,
    'mock': -20,
    'mocks': -20,
    'tmp': -25,
    'temp': -25,
    'logs': -30,
    'log': -25,
    'exports': -18,
    'generated': -22,
}


# =========================================================
# UTILIDADES BÁSICAS
# =========================================================
def normalize_ws(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + ' ...'


def safe_read_text(path: Path) -> str:
    encodings = ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252')
    last_error = None
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except Exception as exc:
            last_error = exc
    return f"[Error al leer el archivo: {last_error}]"


def count_lines(text: str) -> int:
    return text.count('\n') + (0 if not text else 1)


def top_level_dir(rel_path: Path) -> str:
    parts = rel_path.parts
    if len(parts) == 0:
        return '.'
    if len(parts) == 1:
        return '.'
    return parts[0]


def should_ignore_dir(dirname: str) -> bool:
    low = dirname.lower()
    if low == OUTPUT_DIRNAME.lower():
        return True
    return low in {d.lower() for d in IGNORED_DIRS}


def is_meaningful_code_file(path: Path) -> bool:
    name_lower = path.name.lower()

    if name_lower in EXCLUDED_OUTPUT_FILES:
        return False

    if name_lower in LOCK_FILENAMES:
        return False

    if name_lower.endswith('.min.js') or name_lower.endswith('.map'):
        return False

    if name_lower in VALID_FILENAMES:
        return True

    suffixes = [s.lower() for s in path.suffixes]
    if any(s in VALID_EXTENSIONS for s in suffixes):
        return True

    if path.suffix.lower() in VALID_EXTENSIONS:
        return True

    return False


def collect_project_files(root_dir: Path) -> Tuple[List[Path], List[Path]]:
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


# =========================================================
# RESÚMENES / PARSERS
# =========================================================
def extract_first_comment_or_doc(text: str, ext: str) -> str:
    if not text.strip():
        return ""

    if ext == '.py':
        try:
            tree = ast.parse(text)
            doc = ast.get_docstring(tree)
            if doc:
                return truncate_text(normalize_ws(doc), DOC_PREVIEW_CHARS)
        except Exception:
            pass

    block_comment = re.search(r'/\*([\s\S]*?)\*/', text)
    if block_comment:
        return truncate_text(normalize_ws(block_comment.group(1)), DOC_PREVIEW_CHARS)

    html_comment = re.search(r'<!--([\s\S]*?)-->', text)
    if html_comment:
        return truncate_text(normalize_ws(html_comment.group(1)), DOC_PREVIEW_CHARS)

    for line in text.splitlines():
        striped = line.strip()
        if striped.startswith('#'):
            return truncate_text(normalize_ws(striped.lstrip('#').strip()), DOC_PREVIEW_CHARS)
        if striped.startswith('//'):
            return truncate_text(normalize_ws(striped.lstrip('/').strip()), DOC_PREVIEW_CHARS)
        if striped:
            break

    if ext in {'.md', '.txt'}:
        return truncate_text(normalize_ws(text), DOC_PREVIEW_CHARS)

    return ""


def extract_framework_signals(text: str, rel_path: Path) -> List[str]:
    low = text.lower()
    signals: List[str] = []

    def add(label: str) -> None:
        if label not in signals:
            signals.append(label)

    if rel_path.suffix.lower() == '.py':
        add('Python')
    if rel_path.suffix.lower() in {'.js', '.ts', '.jsx', '.tsx'}:
        add('JavaScript/TypeScript')
    if rel_path.suffix.lower() == '.gs':
        add('Google Apps Script')
    if rel_path.suffix.lower() in {'.cs', '.csproj', '.sln'}:
        add('C#/.NET')
    if rel_path.suffix.lower() in {'.html', '.css'}:
        add('Frontend HTML/CSS')
    if rel_path.suffix.lower() == '.sql':
        add('SQL')

    if 'from flask' in low or 'flask(' in low or 'flask(__name__' in low:
        add('Flask')
    if 'fastapi' in low:
        add('FastAPI')
    if 'django' in low:
        add('Django')
    if 'express' in low:
        add('Express')
    if 'react' in low or rel_path.suffix.lower() in {'.jsx', '.tsx'}:
        add('React')
    if 'vue' in low:
        add('Vue')
    if 'tailwind' in low:
        add('Tailwind')
    if 'sqlalchemy' in low:
        add('SQLAlchemy')
    if 'tkinter' in low:
        add('Tkinter')
    if 'pandas' in low:
        add('Pandas')
    if 'numpy' in low:
        add('NumPy')
    if 'openpyxl' in low:
        add('OpenPyXL')
    if 'sqlite' in low:
        add('SQLite')
    if 'postgres' in low or 'psycopg' in low:
        add('PostgreSQL')

    return signals


def parse_python_file(text: str) -> Dict[str, object]:
    info = {
        'imports': [],
        'import_lines': [],
        'functions': [],
        'classes': [],
        'signature_lines': [],
        'routes': [],
        'main_guard': False,
    }

    try:
        tree = ast.parse(text)
    except Exception:
        return info

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.strip()
                if mod:
                    info['imports'].append(mod)
                    info['import_lines'].append(f"import {mod}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or '').strip()
            imported = ', '.join(a.name for a in node.names)
            if mod:
                info['imports'].append(mod)
                info['import_lines'].append(f"from {mod} import {imported}")
            else:
                info['import_lines'].append(f"from . import {imported}")
        elif isinstance(node, ast.ClassDef):
            info['classes'].append(node.name)
            info['signature_lines'].append(f"class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info['functions'].append(node.name)
            kind = 'async def' if isinstance(node, ast.AsyncFunctionDef) else 'def'
            info['signature_lines'].append(f"{kind} {node.name}(...)")

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            segment = ast.get_source_segment(text, node.test) or ''
            if '__name__' in segment and '__main__' in segment:
                info['main_guard'] = True

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_text = ast.get_source_segment(text, dec) or ''
                if re.search(r'\.(get|post|put|delete|patch|route)\s*\(', dec_text):
                    info['routes'].append(f"{dec_text} -> {node.name}")

    info['imports'] = unique_preserve_order(info['imports'])
    info['import_lines'] = unique_preserve_order(info['import_lines'])
    info['functions'] = unique_preserve_order(info['functions'])
    info['classes'] = unique_preserve_order(info['classes'])
    info['signature_lines'] = unique_preserve_order(info['signature_lines'])
    info['routes'] = unique_preserve_order(info['routes'])
    return info


def parse_js_ts_file(text: str) -> Dict[str, object]:
    info = {
        'imports': [],
        'import_lines': [],
        'functions': [],
        'classes': [],
        'signature_lines': [],
        'routes': [],
        'main_guard': False,
    }

    import_patterns = [
        r'^\s*import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',
        r'^\s*const\s+.*?=\s*require\([\'"]([^\'"]+)[\'"]\)',
        r'^\s*import\s+[\'"]([^\'"]+)[\'"]',
    ]

    for line in text.splitlines():
        for pattern in import_patterns:
            m = re.search(pattern, line)
            if m:
                module_name = m.group(1).strip()
                info['imports'].append(module_name)
                info['import_lines'].append(line.strip())
                break

    for m in re.finditer(r'^\s*(?:export\s+)?function\s+([A-Za-z_]\w*)', text, flags=re.MULTILINE):
        name = m.group(1)
        info['functions'].append(name)
        info['signature_lines'].append(f"function {name}(...)")

    for m in re.finditer(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>', text, flags=re.MULTILINE):
        name = m.group(1)
        info['functions'].append(name)
        info['signature_lines'].append(f"const {name} = (...) =>")

    for m in re.finditer(r'^\s*(?:export\s+default\s+)?class\s+([A-Za-z_]\w*)', text, flags=re.MULTILINE):
        name = m.group(1)
        info['classes'].append(name)
        info['signature_lines'].append(f"class {name}")

    for line in text.splitlines():
        if re.search(r'\b(?:router|app)\.(get|post|put|delete|patch|use)\s*\(', line):
            info['routes'].append(line.strip())

    if re.search(r'\bapp\.listen\s*\(', text):
        info['main_guard'] = True
    if re.search(r'\bcreateRoot\s*\(', text) or re.search(r'ReactDOM\.render\s*\(', text):
        info['main_guard'] = True

    info['imports'] = unique_preserve_order(info['imports'])
    info['import_lines'] = unique_preserve_order(info['import_lines'])
    info['functions'] = unique_preserve_order(info['functions'])
    info['classes'] = unique_preserve_order(info['classes'])
    info['signature_lines'] = unique_preserve_order(info['signature_lines'])
    info['routes'] = unique_preserve_order(info['routes'])
    return info


def parse_csharp_file(text: str) -> Dict[str, object]:
    info = {
        'imports': [],
        'import_lines': [],
        'functions': [],
        'classes': [],
        'signature_lines': [],
        'routes': [],
        'main_guard': False,
    }

    for m in re.finditer(r'^\s*using\s+([A-Za-z0-9_.]+)\s*;', text, flags=re.MULTILINE):
        mod = m.group(1)
        info['imports'].append(mod)
        info['import_lines'].append(f"using {mod};")

    for m in re.finditer(r'^\s*(?:public|private|internal|protected)?\s*(?:static\s+)?class\s+([A-Za-z_]\w*)', text, flags=re.MULTILINE):
        name = m.group(1)
        info['classes'].append(name)
        info['signature_lines'].append(f"class {name}")

    for m in re.finditer(r'^\s*(?:public|private|internal|protected)\s+(?:static\s+)?[A-Za-z0-9_<>,\[\]?]+\s+([A-Za-z_]\w*)\s*\(', text, flags=re.MULTILINE):
        name = m.group(1)
        info['functions'].append(name)
        info['signature_lines'].append(f"method {name}(...)")

    if re.search(r'\bstatic\s+void\s+Main\s*\(', text):
        info['main_guard'] = True

    info['imports'] = unique_preserve_order(info['imports'])
    info['import_lines'] = unique_preserve_order(info['import_lines'])
    info['functions'] = unique_preserve_order(info['functions'])
    info['classes'] = unique_preserve_order(info['classes'])
    info['signature_lines'] = unique_preserve_order(info['signature_lines'])
    return info


def parse_generic_file(rel_path: Path, text: str) -> Dict[str, object]:
    ext = rel_path.suffix.lower()

    if ext == '.py':
        return parse_python_file(text)
    if ext in {'.js', '.ts', '.jsx', '.tsx', '.gs'}:
        return parse_js_ts_file(text)
    if ext in {'.cs'}:
        return parse_csharp_file(text)

    return {
        'imports': [],
        'import_lines': [],
        'functions': [],
        'classes': [],
        'signature_lines': [],
        'routes': [],
        'main_guard': False,
    }


def unique_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        val = item.strip()
        if not val:
            continue
        if val not in seen:
            seen.add(val)
            result.append(val)
    return result


def summarize_requirements(text: str) -> str:
    pkgs = []
    for line in text.splitlines():
        striped = line.strip()
        if not striped or striped.startswith('#'):
            continue
        pkgs.append(striped)
    if not pkgs:
        return "Sin paquetes detectados."
    return "Paquetes: " + ', '.join(pkgs[:20])


def summarize_env(text: str) -> str:
    keys = []
    for line in text.splitlines():
        striped = line.strip()
        if not striped or striped.startswith('#') or '=' not in striped:
            continue
        key = striped.split('=', 1)[0].strip()
        if key:
            keys.append(key)
    keys = unique_preserve_order(keys)
    if not keys:
        return "Sin variables detectadas."
    return "Variables detectadas: " + ', '.join(keys[:20])


def summarize_package_json(text: str) -> str:
    try:
        data = json.loads(text)
    except Exception:
        return "No fue posible parsear package.json."

    parts = []
    name = data.get('name')
    if name:
        parts.append(f"name={name}")

    scripts = sorted((data.get('scripts') or {}).keys())
    if scripts:
        parts.append("scripts=" + ', '.join(scripts[:12]))

    deps = sorted((data.get('dependencies') or {}).keys())
    if deps:
        parts.append("dependencies=" + ', '.join(deps[:15]))

    dev_deps = sorted((data.get('devDependencies') or {}).keys())
    if dev_deps:
        parts.append("devDependencies=" + ', '.join(dev_deps[:12]))

    return ' | '.join(parts) if parts else "package.json sin secciones relevantes detectadas."


def summarize_pyproject(text: str) -> str:
    if tomllib is None:
        return "tomllib no disponible para parsear pyproject.toml."

    try:
        data = tomllib.loads(text)
    except Exception:
        return "No fue posible parsear pyproject.toml."

    parts = []

    project = data.get('project', {})
    if isinstance(project, dict):
        name = project.get('name')
        if name:
            parts.append(f"name={name}")
        deps = project.get('dependencies') or []
        if deps:
            parts.append("dependencies=" + ', '.join(deps[:15]))

    tool = data.get('tool', {})
    if isinstance(tool, dict) and tool:
        parts.append("tool sections=" + ', '.join(sorted(tool.keys())[:12]))

    build_system = data.get('build-system', {})
    if isinstance(build_system, dict) and build_system:
        reqs = build_system.get('requires') or []
        if reqs:
            parts.append("build-system=" + ', '.join(reqs[:10]))

    return ' | '.join(parts) if parts else "pyproject.toml sin secciones relevantes detectadas."


def summarize_sql(text: str) -> str:
    tables = re.findall(r'create\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z_][\w.]*)', text, flags=re.IGNORECASE)
    alters = re.findall(r'alter\s+table\s+([A-Za-z_][\w.]*)', text, flags=re.IGNORECASE)
    refs = unique_preserve_order(tables + alters)
    if refs:
        return "Tablas detectadas: " + ', '.join(refs[:20])
    return "Script SQL sin tablas detectadas claramente."


def infer_role(rel_path: Path) -> str:
    name = rel_path.name.lower()
    path_low = rel_path.as_posix().lower()
    ext = rel_path.suffix.lower()

    if name == 'readme.md':
        return 'documentación principal'
    if name in {'package.json', 'pyproject.toml', 'requirements.txt', 'dockerfile', 'docker-compose.yml', 'docker-compose.yaml'}:
        return 'configuración / dependencias'
    if name in {'.env', '.env.example'}:
        return 'variables de entorno'
    if name in {'main.py', 'app.py', 'server.py', 'run.py', 'manage.py', 'index.js', 'index.ts'}:
        return 'entrypoint'
    if 'route' in path_low or '/api/' in f'/{path_low}/' or path_low.endswith('/api'):
        return 'api / rutas'
    if 'model' in path_low or 'schema' in path_low:
        return 'modelo / esquema'
    if 'service' in path_low or 'core' in path_low:
        return 'lógica central'
    if 'config' in path_low or 'settings' in path_low:
        return 'configuración'
    if 'template' in path_low or ext == '.html':
        return 'ui / plantilla'
    if ext == '.css':
        return 'estilos'
    if ext == '.sql':
        return 'base de datos'
    if ext in {'.md', '.txt'}:
        return 'documentación'
    if ext in {'.json', '.toml', '.yaml', '.yml', '.ini', '.cfg', '.xml'}:
        return 'configuración / datos'
    if ext in {'.py', '.js', '.ts', '.jsx', '.tsx', '.gs', '.cs', '.vb'}:
        return 'archivo fuente'
    return 'archivo relevante'


def infer_purpose(rel_path: Path, role: str, doc_preview: str, parsed: Dict[str, object], frameworks: List[str]) -> str:
    if doc_preview:
        return doc_preview

    parts = []
    if role:
        parts.append(f"Rol: {role}")

    funcs = len(parsed.get('functions', []))
    classes = len(parsed.get('classes', []))
    routes = len(parsed.get('routes', []))

    if funcs:
        parts.append(f"funciones={funcs}")
    if classes:
        parts.append(f"clases={classes}")
    if routes:
        parts.append(f"rutas/endpoints={routes}")
    if frameworks:
        parts.append("stack=" + ', '.join(frameworks[:4]))

    if not parts:
        return f"Archivo {rel_path.name} sin metadatos suficientes para inferencia."
    return ' | '.join(parts)


def build_special_summary(rel_path: Path, text: str) -> str:
    name = rel_path.name.lower()
    ext = rel_path.suffix.lower()

    if name == 'package.json':
        return summarize_package_json(text)
    if name == 'pyproject.toml':
        return summarize_pyproject(text)
    if name == 'requirements.txt':
        return summarize_requirements(text)
    if name in {'.env', '.env.example'}:
        return summarize_env(text)
    if ext == '.sql':
        return summarize_sql(text)

    return ""


def score_file(rel_path: Path, text: str, parsed: Dict[str, object], role: str, frameworks: List[str]) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    name = rel_path.name.lower()
    path_low = rel_path.as_posix().lower()
    ext = rel_path.suffix.lower()
    chars = len(text)
    lines = count_lines(text)

    if name in EXACT_PRIORITY_FILENAMES:
        score += EXACT_PRIORITY_FILENAMES[name]
        reasons.append(f"nombre clave: {name}")

    for key, val in PATH_PRIORITY_KEYWORDS.items():
        if key in path_low:
            score += val
            reasons.append(f"path relevante: {key}")

    for key, val in LOW_PRIORITY_KEYWORDS.items():
        if key in path_low:
            score += val
            reasons.append(f"path secundario: {key}")

    if role == 'entrypoint':
        score += 55
        reasons.append('entrypoint')
    elif role == 'configuración / dependencias':
        score += 45
        reasons.append('configuración/dependencias')
    elif role == 'api / rutas':
        score += 40
        reasons.append('api/rutas')
    elif role == 'lógica central':
        score += 34
        reasons.append('lógica central')
    elif role == 'modelo / esquema':
        score += 28
        reasons.append('modelo/esquema')
    elif role == 'documentación principal':
        score += 60
        reasons.append('documentación principal')
    elif role == 'ui / plantilla':
        score += 18
        reasons.append('ui/plantilla')

    import_count = len(parsed.get('imports', []))
    function_count = len(parsed.get('functions', []))
    class_count = len(parsed.get('classes', []))
    route_count = len(parsed.get('routes', []))
    main_guard = bool(parsed.get('main_guard', False))

    if import_count:
        bonus = min(import_count * 2, 24)
        score += bonus
        reasons.append(f"imports={import_count}")

    if function_count:
        bonus = min(function_count * 2, 24)
        score += bonus
        reasons.append(f"funciones={function_count}")

    if class_count:
        bonus = min(class_count * 3, 24)
        score += bonus
        reasons.append(f"clases={class_count}")

    if route_count:
        bonus = min(route_count * 10, 40)
        score += bonus
        reasons.append(f"rutas={route_count}")

    if main_guard:
        score += 30
        reasons.append('bloque main/boot detectado')

    if frameworks:
        bonus = min(len(frameworks) * 6, 18)
        score += bonus
        reasons.append("frameworks=" + ', '.join(frameworks[:3]))

    if ext in {'.py', '.js', '.ts', '.jsx', '.tsx', '.gs', '.cs'}:
        score += 12

    if ext in {'.html', '.css'}:
        score += 8

    if ext in {'.md'}:
        score += 10

    if ext in {'.csv'}:
        score -= 12
        reasons.append('csv tiende a ser dato, no contexto central')

    if lines > 3500:
        score -= 30
        reasons.append('archivo muy grande')
    elif lines > 1800:
        score -= 15
        reasons.append('archivo grande')
    elif 30 <= lines <= 1200:
        score += 8

    if chars < 80:
        score -= 10

    return score, reasons


def analyze_file(root_dir: Path, rel_path: Path) -> Dict[str, object]:
    full_path = root_dir / rel_path
    text = safe_read_text(full_path)
    ext = rel_path.suffix.lower()

    parsed = parse_generic_file(rel_path, text)
    frameworks = extract_framework_signals(text, rel_path)
    role = infer_role(rel_path)
    doc_preview = extract_first_comment_or_doc(text, ext)
    special_summary = build_special_summary(rel_path, text)
    purpose = infer_purpose(rel_path, role, doc_preview, parsed, frameworks)

    score, reasons = score_file(rel_path, text, parsed, role, frameworks)

    return {
        'path': rel_path.as_posix(),
        'name': rel_path.name,
        'ext': ext,
        'role': role,
        'score': score,
        'score_reasons': reasons,
        'chars': len(text),
        'lines': count_lines(text),
        'text': text,
        'frameworks': frameworks,
        'doc_preview': doc_preview,
        'special_summary': special_summary,
        'purpose': purpose,
        'imports': parsed.get('imports', []),
        'import_lines': parsed.get('import_lines', []),
        'functions': parsed.get('functions', []),
        'classes': parsed.get('classes', []),
        'signature_lines': parsed.get('signature_lines', []),
        'routes': parsed.get('routes', []),
        'main_guard': parsed.get('main_guard', False),
        'top_dir': top_level_dir(rel_path),
    }


# =========================================================
# SELECCIÓN DE ARCHIVOS CLAVE
# =========================================================
def select_key_files(analyses: List[Dict[str, object]]) -> List[Dict[str, object]]:
    sorted_all = sorted(
        analyses,
        key=lambda x: (-int(x['score']), int(x['chars']), x['path'].lower())
    )

    selected: List[Dict[str, object]] = []
    selected_paths: Set[str] = set()

    def add_if_present_by_name(target_names: List[str]) -> None:
        for target in target_names:
            for item in sorted_all:
                if item['name'].lower() == target and item['path'] not in selected_paths:
                    selected.append(item)
                    selected_paths.add(item['path'])
                    break

    # Semillas obligatorias si existen
    add_if_present_by_name([
        'readme.md',
        'main.py',
        'app.py',
        'server.py',
        'run.py',
        'manage.py',
        'package.json',
        'pyproject.toml',
        'requirements.txt',
        'dockerfile',
        '.env.example',
    ])

    # Cobertura por carpeta de primer nivel
    best_by_dir: Dict[str, Dict[str, object]] = {}
    for item in sorted_all:
        td = str(item['top_dir'])
        if td not in best_by_dir:
            best_by_dir[td] = item

    for _, item in sorted(best_by_dir.items(), key=lambda kv: -int(kv[1]['score'])):
        if len(selected) >= MAX_KEY_FILES:
            break
        if item['path'] not in selected_paths:
            selected.append(item)
            selected_paths.add(item['path'])

    # Relleno general por score
    for item in sorted_all:
        if len(selected) >= MAX_KEY_FILES:
            break
        if item['path'] not in selected_paths:
            selected.append(item)
            selected_paths.add(item['path'])

    return selected[:MAX_KEY_FILES]


# =========================================================
# DETECCIÓN GLOBAL DE STACK
# =========================================================
def detect_global_stack(analyses: List[Dict[str, object]]) -> List[str]:
    found: List[str] = []
    for item in analyses:
        for fw in item.get('frameworks', []):
            if fw not in found:
                found.append(fw)
    return found[:MAX_FRAMEWORKS_SHOWN]


# =========================================================
# ÁRBOL RESUMIDO
# =========================================================
def summarize_tree(all_tree_files: List[Path], key_files: List[Dict[str, object]]) -> str:
    lines: List[str] = []

    total_visible = len(all_tree_files)
    lines.append(f"Archivos visibles considerados en árbol: {total_visible}")
    lines.append("")

    top_level_counts: Dict[str, int] = {}
    for rel in all_tree_files:
        td = top_level_dir(rel)
        top_level_counts[td] = top_level_counts.get(td, 0) + 1

    key_by_dir: Dict[str, List[Dict[str, object]]] = {}
    for item in key_files:
        td = str(item['top_dir'])
        key_by_dir.setdefault(td, []).append(item)

    ordered_dirs = sorted(
        top_level_counts.items(),
        key=lambda kv: (-kv[1], kv[0].lower())
    )

    for dir_name, qty in ordered_dirs:
        title = './' if dir_name == '.' else f"{dir_name}/"
        lines.append(f"- {title}  | archivos={qty}")

        if dir_name in key_by_dir:
            top_items = sorted(
                key_by_dir[dir_name],
                key=lambda x: (-int(x['score']), x['path'].lower())
            )[:TOP_FILES_PER_DIR]

            for item in top_items:
                lines.append(
                    f"    • {item['path']} | score={item['score']} | rol={item['role']}"
                )

    return '\n'.join(lines)


# =========================================================
# RENDER DE RESÚMENES
# =========================================================
def render_key_file_detail(item: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append("=" * 92)
    lines.append(f"ARCHIVO CLAVE: {item['path']}")
    lines.append("=" * 92)
    lines.append(f"Rol: {item['role']}")
    lines.append(f"Score: {item['score']}")
    lines.append(f"Tamaño: {item['chars']} chars | {item['lines']} líneas")
    lines.append(f"Carpeta top-level: {item['top_dir']}")
    lines.append(f"Propósito inferido: {item['purpose']}")

    if item['frameworks']:
        lines.append("Frameworks/stack detectados: " + ', '.join(item['frameworks'][:MAX_FRAMEWORKS_SHOWN]))

    if item['special_summary']:
        lines.append("Resumen especial: " + item['special_summary'])

    if item['imports']:
        lines.append("Imports detectados: " + ', '.join(item['imports'][:MAX_IMPORTS_SHOWN]))

    if item['classes']:
        lines.append("Clases: " + ', '.join(item['classes'][:MAX_SIGNATURES_SHOWN]))

    if item['functions']:
        lines.append("Funciones: " + ', '.join(item['functions'][:MAX_SIGNATURES_SHOWN]))

    if item['routes']:
        lines.append("Rutas/endpoints: " + ' | '.join(item['routes'][:10]))

    if item['score_reasons']:
        lines.append("Razones del score: " + ' | '.join(item['score_reasons'][:12]))

    return '\n'.join(lines)


def render_overview_file(
    root_dir: Path,
    all_tree_files: List[Path],
    content_files: List[Path],
    analyses: List[Dict[str, object]],
    key_files: List[Dict[str, object]],
) -> str:
    lines: List[str] = []
    stack = detect_global_stack(analyses)

    entrypoints = [x for x in key_files if x['role'] == 'entrypoint']
    configs = [x for x in key_files if 'configuración' in x['role'] or x['name'].lower() in {'package.json', 'pyproject.toml', 'requirements.txt', 'dockerfile', '.env', '.env.example'}]
    docs = [x for x in key_files if 'documentación' in x['role']]

    lines.append("============================================================")
    lines.append("CONTEXTO GENERAL PARA IA")
    lines.append("============================================================")
    lines.append("")
    lines.append(f"Proyecto raíz: {root_dir.resolve()}")
    lines.append(f"Archivos visibles en árbol: {len(all_tree_files)}")
    lines.append(f"Archivos candidatos analizados: {len(content_files)}")
    lines.append(f"Archivos clave seleccionados: {len(key_files)}")
    lines.append("")
    lines.append("OBJETIVO DE ESTE ARCHIVO")
    lines.append("------------------------------------------------------------")
    lines.append("Este archivo no es un volcado completo. Está optimizado para")
    lines.append("darle a una IA contexto rápido del proyecto, su estructura,")
    lines.append("sus entrypoints, su stack y los archivos más importantes.")
    lines.append("")

    lines.append("STACK DETECTADO")
    lines.append("------------------------------------------------------------")
    if stack:
        lines.append(', '.join(stack))
    else:
        lines.append("No se detectó un stack dominante con suficiente claridad.")
    lines.append("")

    lines.append("ENTRYPOINTS DETECTADOS")
    lines.append("------------------------------------------------------------")
    if entrypoints:
        for item in entrypoints:
            lines.append(f"- {item['path']} | score={item['score']} | {item['purpose']}")
    else:
        lines.append("No se detectaron entrypoints claros por heurística.")
    lines.append("")

    lines.append("CONFIGS / DEPENDENCIAS IMPORTANTES")
    lines.append("------------------------------------------------------------")
    if configs:
        for item in configs:
            special = item['special_summary']
            if special:
                lines.append(f"- {item['path']} | {special}")
            else:
                lines.append(f"- {item['path']} | score={item['score']}")
    else:
        lines.append("No se detectaron archivos de configuración prioritarios.")
    lines.append("")

    lines.append("DOCUMENTACIÓN PRINCIPAL")
    lines.append("------------------------------------------------------------")
    if docs:
        for item in docs:
            lines.append(f"- {item['path']} | {item['purpose']}")
    else:
        lines.append("No se detectó documentación principal relevante.")
    lines.append("")

    lines.append("ESTRUCTURA RESUMIDA DEL PROYECTO")
    lines.append("------------------------------------------------------------")
    lines.append(summarize_tree(all_tree_files, key_files))
    lines.append("")

    lines.append("RANKING GLOBAL DE ARCHIVOS CLAVE")
    lines.append("------------------------------------------------------------")
    for idx, item in enumerate(sorted(key_files, key=lambda x: (-int(x['score']), x['path'].lower())), start=1):
        lines.append(
            f"{idx:02d}. {item['path']} | score={item['score']} | rol={item['role']} | "
            f"{item['chars']} chars | {item['lines']} líneas"
        )
    lines.append("")

    lines.append("RESUMEN TÉCNICO DE ARCHIVOS CLAVE")
    lines.append("------------------------------------------------------------")
    for item in sorted(key_files, key=lambda x: (-int(x['score']), x['path'].lower())):
        lines.append(render_key_file_detail(item))
        lines.append("")

    return '\n'.join(lines)


# =========================================================
# ARCHIVO DE CÓDIGO CLAVE
# =========================================================
def head_preview(text: str, max_chars: int = HEAD_PREVIEW_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n...[TRUNCADO PARA CONTEXTO RÁPIDO]...\n"


def render_full_code_section(item: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append("=" * 100)
    lines.append(f"ARCHIVO CLAVE | CONTENIDO COMPLETO: {item['path']}")
    lines.append("=" * 100)
    lines.append(f"Rol: {item['role']} | Score: {item['score']} | {item['chars']} chars | {item['lines']} líneas")
    if item['purpose']:
        lines.append(f"Propósito inferido: {item['purpose']}")
    lines.append("-" * 100)
    lines.append(item['text'])
    if not item['text'].endswith('\n'):
        lines.append("")
    return '\n'.join(lines)


def render_large_excerpt_section(item: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append("=" * 100)
    lines.append(f"ARCHIVO CLAVE | EXTRACTO ESTRUCTURAL: {item['path']}")
    lines.append("=" * 100)
    lines.append(f"Rol: {item['role']} | Score: {item['score']} | {item['chars']} chars | {item['lines']} líneas")
    lines.append(f"Propósito inferido: {item['purpose']}")

    if item['frameworks']:
        lines.append("Frameworks/stack: " + ', '.join(item['frameworks'][:MAX_FRAMEWORKS_SHOWN]))
    if item['special_summary']:
        lines.append("Resumen especial: " + item['special_summary'])

    if item['import_lines']:
        lines.append("")
        lines.append("IMPORTS RELEVANTES")
        lines.append("-" * 100)
        for line in item['import_lines'][:MAX_IMPORTS_SHOWN]:
            lines.append(line)

    if item['signature_lines']:
        lines.append("")
        lines.append("FIRMAS / DECLARACIONES PRINCIPALES")
        lines.append("-" * 100)
        for line in item['signature_lines'][:MAX_SIGNATURES_SHOWN]:
            lines.append(line)

    if item['routes']:
        lines.append("")
        lines.append("RUTAS / ENDPOINTS DETECTADOS")
        lines.append("-" * 100)
        for line in item['routes'][:10]:
            lines.append(line)

    lines.append("")
    lines.append("FRAGMENTO INICIAL")
    lines.append("-" * 100)
    lines.append(head_preview(item['text'], HEAD_PREVIEW_CHARS))
    return '\n'.join(lines)


def render_keycode_file(key_files: List[Dict[str, object]]) -> str:
    ordered = sorted(key_files, key=lambda x: (-int(x['score']), x['path'].lower()))

    sections: List[str] = []
    total_chars = 0
    full_count = 0
    excerpt_count = 0
    skipped: List[str] = []

    header = []
    header.append("============================================================")
    header.append("CÓDIGO CLAVE PARA IA")
    header.append("============================================================")
    header.append("")
    header.append("Este archivo contiene:")
    header.append("- archivos pequeños importantes completos")
    header.append("- archivos grandes importantes en extracto estructural")
    header.append("")
    header_text = '\n'.join(header)
    sections.append(header_text)
    total_chars += len(header_text)

    # Primero archivos pequeños y muy importantes completos
    for item in ordered:
        if full_count >= MAX_FULL_FILES:
            break
        if item['chars'] <= FULL_FILE_MAX_CHARS:
            section = render_full_code_section(item)
            if total_chars + len(section) > MAX_CHARS_IN_KEYCODE_FILE:
                skipped.append(item['path'])
                continue
            sections.append(section)
            total_chars += len(section)
            full_count += 1

    # Luego extractos estructurales de archivos grandes importantes
    for item in ordered:
        if excerpt_count >= MAX_LARGE_EXCERPTS:
            break
        if item['chars'] <= FULL_FILE_MAX_CHARS:
            continue
        section = render_large_excerpt_section(item)
        if total_chars + len(section) > MAX_CHARS_IN_KEYCODE_FILE:
            skipped.append(item['path'])
            continue
        sections.append(section)
        total_chars += len(section)
        excerpt_count += 1

    footer = []
    footer.append("")
    footer.append("============================================================")
    footer.append("RESUMEN DE INCLUSIÓN")
    footer.append("============================================================")
    footer.append(f"Archivos completos incluidos: {full_count}")
    footer.append(f"Archivos grandes incluidos como extracto: {excerpt_count}")
    footer.append(f"Caracteres totales aprox: {total_chars}")
    if skipped:
        footer.append("Archivos omitidos por límite de tamaño:")
        for path in unique_preserve_order(skipped):
            footer.append(f"- {path}")
    else:
        footer.append("No hubo archivos omitidos por límite de tamaño.")

    footer_text = '\n'.join(footer)
    sections.append(footer_text)

    return '\n\n'.join(sections)


# =========================================================
# MANIFIESTO OPCIONAL
# =========================================================
def write_manifest(output_dir: Path, analyses: List[Dict[str, object]], key_files: List[Dict[str, object]]) -> None:
    if not GENERATE_JSON_MANIFEST:
        return

    manifest = {
        'output_mode': 'fast_ai_context',
        'key_files_count': len(key_files),
        'all_analyzed_count': len(analyses),
        'key_files': [
            {
                'path': item['path'],
                'role': item['role'],
                'score': item['score'],
                'chars': item['chars'],
                'lines': item['lines'],
                'frameworks': item['frameworks'],
                'purpose': item['purpose'],
            }
            for item in sorted(key_files, key=lambda x: (-int(x['score']), x['path'].lower()))
        ],
    }

    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


# =========================================================
# PROCESO PRINCIPAL
# =========================================================
def generate_fast_ai_context(root_dir: Path = ROOT_DIR) -> None:
    root_dir = root_dir.resolve()
    output_dir = root_dir / OUTPUT_DIRNAME
    output_dir.mkdir(parents=True, exist_ok=True)

    all_tree_files, content_files = collect_project_files(root_dir)

    analyses = [analyze_file(root_dir, rel_path) for rel_path in content_files]
    key_files = select_key_files(analyses)

    overview_text = render_overview_file(
        root_dir=root_dir,
        all_tree_files=all_tree_files,
        content_files=content_files,
        analyses=analyses,
        key_files=key_files,
    )

    keycode_text = render_keycode_file(key_files)

    (output_dir / OVERVIEW_FILENAME).write_text(overview_text, encoding='utf-8')
    (output_dir / KEYCODE_FILENAME).write_text(keycode_text, encoding='utf-8')

    write_manifest(output_dir, analyses, key_files)

    print(f"✅ Contexto IA rápido generado en: {output_dir}")
    print(f"📁 Archivos visibles en árbol: {len(all_tree_files)}")
    print(f"📄 Archivos candidatos analizados: {len(content_files)}")
    print(f"⭐ Archivos clave seleccionados: {len(key_files)}")
    print(f"🧠 Archivo general: {output_dir / OVERVIEW_FILENAME}")
    print(f"🧩 Archivo de código clave: {output_dir / KEYCODE_FILENAME}")
    if GENERATE_JSON_MANIFEST:
        print(f"🗂 Manifiesto: {output_dir / MANIFEST_FILENAME}")


if __name__ == '__main__':
    generate_fast_ai_context()