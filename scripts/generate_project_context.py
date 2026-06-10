import ast
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_MD = ROOT / "PROJECT_CONTEXT.md"
OUTPUT_JSON = ROOT / "project_context.json"
OUTPUT_CODE_DUMP = ROOT / "PROJECT_CODE_LISTING.txt"

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".cursor",
    "Contexto_Rapido",
    "NEST_EXPORTS",
}

ENTRYPOINT_CANDIDATES = {"main.py", "api_server.py", "lanzador.bat"}
KEY_FILES = [
    "main.py",
    "api_server.py",
    "config.py",
    "interface/main_window.py",
    "interface/tab_files.py",
    "interface/tab_nesting.py",
    "interface/postgres_connector.py",
    "modules/nesting_engine/manager.py",
    "modules/nesting_engine/exporter.py",
    "reporte_pdf_nesting.py",
]

INTERNAL_IMPORT_PREFIXES = ("interface", "modules", "scripts")
DB_HINTS = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE", "ALTER TABLE", "TRUNCATE ")
PATH_HINT_RE = re.compile(r"([A-Za-z]:\\|\\\\|/)")
CODE_DUMP_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".sql",
    ".bat",
    ".ps1",
    ".sh",
}
CODE_DUMP_FILENAMES = {
    "dockerfile",
    ".env",
    ".env.example",
    ".gitignore",
}
CODE_DUMP_EXCLUDED = {
    "PROJECT_CONTEXT.md",
    "project_context.json",
    "PROJECT_CODE_LISTING.txt",
    "Contexto_ARGA-NESTING-SOFTWARE.txt",
}


@dataclass
class ModuleInfo:
    path: str
    lines: int
    classes: List[str]
    functions: List[str]
    imports: List[str]
    internal_imports: List[str]
    import_aliases: Dict[str, str]
    imported_symbols: Dict[str, str]
    docstring: str
    category: str
    endpoints: List[Dict[str, str]] = field(default_factory=list)
    sql_snippets: List[str] = field(default_factory=list)
    external_paths: List[str] = field(default_factory=list)
    writes_files: bool = False
    reads_files: bool = False
    callers: Dict[str, List[str]] = field(default_factory=dict)
    parse_error: Optional[str] = None


def _safe_read(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return ""


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def _category(rel: Path) -> str:
    p = rel.as_posix()
    if p.startswith("interface/"):
        return "ui"
    if p.startswith("modules/nesting_engine/"):
        return "nesting_engine"
    if p.startswith("modules/"):
        return "modules"
    if p.startswith("scripts/"):
        return "scripts"
    if p.endswith(".py"):
        return "root"
    return "other"


def _module_name_from_rel(rel: Path) -> str:
    raw = rel.as_posix().replace("/", ".")
    return raw[:-3] if raw.endswith(".py") else raw


def _resolve_internal_import(mod: str, rel: Path) -> Optional[str]:
    clean = (mod or "").strip(".")
    if not clean:
        return None
    if clean.startswith(INTERNAL_IMPORT_PREFIXES):
        candidate = f"{clean.replace('.', '/')}.py"
        if (ROOT / candidate).exists():
            return candidate
    if clean.endswith(".py") and (ROOT / clean).exists():
        return clean
    # root-level module import (e.g., "config", "freecad_runner")
    root_candidate = f"{clean.replace('.', '/')}.py"
    if (ROOT / root_candidate).exists():
        return root_candidate
    return None


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _extract_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: List[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
        return "".join(parts) if parts else None
    return None


class FunctionCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.current = "<module>"
        self.calls_by_fn: Dict[str, Set[str]] = {}
        self.sql_snippets: Set[str] = set()
        self.external_paths: Set[str] = set()
        self.writes_files = False
        self.reads_files = False
        self.endpoints: List[Dict[str, str]] = []

    def _ensure_current(self) -> None:
        if self.current not in self.calls_by_fn:
            self.calls_by_fn[self.current] = set()

    def _visit_decorators_for_endpoint(self, name: str, decorators: List[ast.AST]) -> None:
        for dec in decorators:
            if not isinstance(dec, ast.Call):
                continue
            dec_name = _call_name(dec.func) or ""
            if any(dec_name.endswith(s) for s in ("get", "post", "put", "delete", "patch", "route")):
                route = ""
                method = dec_name.split(".")[-1].upper()
                if dec.args:
                    route = _extract_str(dec.args[0]) or ""
                self.endpoints.append({"function": name, "method": method, "route": route})

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prev = self.current
        self.current = node.name
        self._ensure_current()
        self._visit_decorators_for_endpoint(node.name, node.decorator_list)
        self.generic_visit(node)
        self.current = prev

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        prev = self.current
        self.current = node.name
        self._ensure_current()
        self._visit_decorators_for_endpoint(node.name, node.decorator_list)
        self.generic_visit(node)
        self.current = prev

    def visit_Call(self, node: ast.Call) -> None:
        self._ensure_current()
        name = _call_name(node.func)
        if name:
            self.calls_by_fn[self.current].add(name)
            lowered = name.lower()
            if lowered.endswith(("open", "pathlib.path.open", "read_text", "read_bytes", "write_text", "write_bytes")):
                if "read" in lowered:
                    self.reads_files = True
                if "write" in lowered or lowered.endswith("open"):
                    self.writes_files = True
            if lowered in {"open", "path.open"} and len(node.args) >= 2:
                mode = _extract_str(node.args[1]) or ""
                if "r" in mode:
                    self.reads_files = True
                if any(m in mode for m in ("w", "a", "x", "+")):
                    self.writes_files = True

        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            text = _extract_str(arg)
            if not text:
                continue
            normalized = text.strip()
            up = normalized.upper()
            if any(hint in up for hint in DB_HINTS):
                self.sql_snippets.add(normalized[:240])
            if PATH_HINT_RE.search(normalized):
                self.external_paths.add(normalized[:240])

        self.generic_visit(node)


def _analyze_python_file(path: Path) -> ModuleInfo:
    text = _safe_read(path)
    rel = path.relative_to(ROOT)
    module = ModuleInfo(
        path=rel.as_posix(),
        lines=len(text.splitlines()),
        classes=[],
        functions=[],
        imports=[],
        internal_imports=[],
        import_aliases={},
        imported_symbols={},
        docstring="",
        category=_category(rel),
    )
    if not text.strip():
        return module

    try:
        tree = ast.parse(text)
    except Exception as exc:
        module.parse_error = str(exc)
        return module

    module.docstring = (ast.get_docstring(tree) or "").strip()[:400]

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                module.imports.append(alias.name)
                if alias.asname:
                    module.import_aliases[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    module.import_aliases[head] = alias.name
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or "."
            module.imports.append(mod_name)
            for alias in node.names:
                symbol = alias.asname or alias.name
                module.imported_symbols[symbol] = mod_name
        elif isinstance(node, ast.ClassDef):
            module.classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module.functions.append(node.name)

    module.imports = sorted(set(i for i in module.imports if i))[:80]
    module.internal_imports = sorted(
        set(filter(None, (_resolve_internal_import(imp, rel) for imp in module.imports)))
    )

    visitor = FunctionCallVisitor()
    visitor.visit(tree)
    module.callers = {k: sorted(v)[:120] for k, v in visitor.calls_by_fn.items()}
    module.sql_snippets = sorted(visitor.sql_snippets)[:20]
    module.external_paths = sorted(visitor.external_paths)[:20]
    module.writes_files = visitor.writes_files
    module.reads_files = visitor.reads_files
    module.endpoints = visitor.endpoints[:120]
    return module


def _discover_modules() -> List[ModuleInfo]:
    modules: List[ModuleInfo] = []
    for py in ROOT.rglob("*.py"):
        rel_parts = set(py.relative_to(ROOT).parts)
        if rel_parts & IGNORED_DIRS:
            continue
        modules.append(_analyze_python_file(py))
    modules.sort(key=lambda m: m.path)
    return modules


def _build_call_edges(modules: List[ModuleInfo]) -> List[Dict[str, str]]:
    by_path = {m.path: m for m in modules}
    edges: Set[str] = set()
    materialized: List[Dict[str, str]] = []
    for m in modules:
        for fn, callees in m.callers.items():
            for callee in callees:
                target_mod: Optional[str] = None
                target_fn: Optional[str] = None

                if "." in callee:
                    left, right = callee.split(".", 1)
                    imported_mod = m.import_aliases.get(left)
                    if imported_mod:
                        resolved = _resolve_internal_import(imported_mod, Path(m.path))
                        if resolved and resolved in by_path:
                            target_mod = resolved
                            target_fn = right.split(".")[-1]
                else:
                    if callee in m.functions:
                        target_mod = m.path
                        target_fn = callee
                    elif callee in m.imported_symbols:
                        imported_mod = m.imported_symbols[callee]
                        resolved = _resolve_internal_import(imported_mod, Path(m.path))
                        if resolved and resolved in by_path:
                            target_mod = resolved
                            target_fn = callee

                if target_mod and target_fn:
                    key = f"{m.path}:{fn}->{target_mod}:{target_fn}"
                    if key not in edges:
                        edges.add(key)
                        materialized.append(
                            {"from_module": m.path, "from_function": fn, "to_module": target_mod, "to_function": target_fn}
                        )
    return materialized[:2000]


def _build_internal_import_edges(modules: List[ModuleInfo]) -> List[Dict[str, str]]:
    edges: List[Dict[str, str]] = []
    for m in modules:
        for target in m.internal_imports:
            edges.append({"from_module": m.path, "to_module": target})
    return edges[:4000]


def _build_risk_flags(modules: List[ModuleInfo]) -> List[str]:
    flags: List[str] = []
    parse_errors = [m.path for m in modules if m.parse_error]
    if parse_errors:
        flags.append(f"Modules with parse errors ({len(parse_errors)}): {', '.join(parse_errors[:10])}")

    with_sql = [m.path for m in modules if m.sql_snippets]
    if with_sql:
        flags.append(f"Modules with embedded SQL ({len(with_sql)}).")

    with_ext_paths = [m.path for m in modules if m.external_paths]
    if with_ext_paths:
        flags.append(f"Modules with hardcoded external paths ({len(with_ext_paths)}).")

    no_entrypoint = [ep for ep in ENTRYPOINT_CANDIDATES if not (ROOT / ep).exists()]
    if no_entrypoint:
        flags.append(f"Declared entrypoint candidates missing: {', '.join(no_entrypoint)}")

    return flags


def _build_json_payload(modules: List[ModuleInfo]) -> Dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    categories: Dict[str, int] = {}
    for m in modules:
        categories[m.category] = categories.get(m.category, 0) + 1

    by_path = {m.path: m for m in modules}
    key_modules = [p for p in KEY_FILES if p in by_path]
    endpoints = [
        {"module": m.path, **ep}
        for m in modules
        for ep in m.endpoints
        if ep.get("route")
    ]

    return {
        "project": ROOT.name,
        "updated_at_utc": now,
        "git_sha": _git_sha(),
        "scope": {
            "language": "python",
            "files_scanned_pattern": "**/*.py",
            "exclusions": sorted(IGNORED_DIRS),
        },
        "totals": {
            "python_modules": len(modules),
            "categories": categories,
            "endpoints": len(endpoints),
            "call_edges": len(_build_call_edges(modules)),
        },
        "entrypoints": [p for p in ENTRYPOINT_CANDIDATES if (ROOT / p).exists()],
        "key_modules": key_modules,
        "architecture": {
            "internal_import_edges": _build_internal_import_edges(modules),
            "function_call_edges": _build_call_edges(modules),
            "api_endpoints": endpoints[:500],
        },
        "observability": {
            "modules_with_sql": [m.path for m in modules if m.sql_snippets],
            "modules_with_external_paths": [m.path for m in modules if m.external_paths],
            "modules_writing_files": [m.path for m in modules if m.writes_files],
            "modules_reading_files": [m.path for m in modules if m.reads_files],
            "risk_flags": _build_risk_flags(modules),
        },
        "modules": [
            {
                "path": m.path,
                "module_name": _module_name_from_rel(Path(m.path)),
                "category": m.category,
                "lines": m.lines,
                "docstring": m.docstring,
                "classes": m.classes,
                "functions": m.functions,
                "imports": m.imports,
                "internal_imports": m.internal_imports,
                "import_aliases": m.import_aliases,
                "imported_symbols": m.imported_symbols,
                "endpoints": m.endpoints,
                "sql_snippets": m.sql_snippets,
                "external_paths": m.external_paths,
                "reads_files": m.reads_files,
                "writes_files": m.writes_files,
                "calls_by_function": m.callers,
                "parse_error": m.parse_error,
            }
            for m in modules
        ],
    }


def _build_markdown(payload: Dict[str, object]) -> str:
    totals = payload["totals"]
    categories = totals["categories"]
    key_modules = payload["key_modules"]
    modules = payload["modules"]
    module_map = {m["path"]: m for m in modules}
    obs = payload["observability"]
    arch = payload["architecture"]

    lines: List[str] = []
    lines.append("# PROJECT_CONTEXT")
    lines.append("")
    lines.append("## Project Snapshot")
    lines.append(f"- Name: `{payload['project']}`")
    lines.append(f"- Updated (UTC): `{payload['updated_at_utc']}`")
    lines.append(f"- Git SHA: `{payload['git_sha']}`")
    lines.append(f"- Python modules scanned: `{totals['python_modules']}`")
    lines.append(f"- API endpoints detected: `{totals['endpoints']}`")
    lines.append(f"- Function call edges detected: `{totals['call_edges']}`")
    lines.append("")
    lines.append("## Entrypoints")
    for ep in payload["entrypoints"]:
        lines.append(f"- `{ep}`")
    lines.append("")
    lines.append("## Module Categories")
    for cat, count in sorted(categories.items(), key=lambda x: x[0]):
        lines.append(f"- `{cat}`: {count}")
    lines.append("")
    lines.append("## Key Modules")
    for path in key_modules:
        m = module_map[path]
        lines.append(
            f"- `{path}`: {m['lines']} lines, {len(m['functions'])} funcs, {len(m['classes'])} classes, "
            f"{len(m['endpoints'])} endpoints"
        )
    lines.append("")
    lines.append("## Runtime Flow")
    lines.append("- `main.py` initializes desktop UI (`SistemaNestingPro`).")
    lines.append("- `interface/tab_files.py` ingests DXF and prepares source data.")
    lines.append("- `interface/tab_nesting.py` orchestrates nesting execution/export.")
    lines.append("- `modules/nesting_engine/manager.py` resolves optimization batches.")
    lines.append("- `modules/nesting_engine/exporter.py` handles CAD export/STEP bridge.")
    lines.append("- `reporte_pdf_nesting.py` generates production and summary PDFs.")
    lines.append("- `api_server.py` exposes operational endpoints for web/clients.")
    lines.append("")
    lines.append("## API Endpoints (Detected)")
    for ep in arch["api_endpoints"][:40]:
        route = ep.get("route", "") or "-"
        lines.append(f"- `{ep['method']}` `{route}` -> `{ep['module']}::{ep['function']}`")
    lines.append("")
    lines.append("## Critical Signals")
    lines.append(f"- Modules with SQL: `{len(obs['modules_with_sql'])}`")
    lines.append(f"- Modules with external paths: `{len(obs['modules_with_external_paths'])}`")
    lines.append(f"- Modules writing files: `{len(obs['modules_writing_files'])}`")
    lines.append(f"- Modules reading files: `{len(obs['modules_reading_files'])}`")
    lines.append("")
    lines.append("## Ambiguity/Risk Flags")
    for risk in obs["risk_flags"] or ["No structural risk flags detected by static analyzer."]:
        lines.append(f"- {risk}")
    lines.append("")
    lines.append("## Notes")
    lines.append("- This file is autogenerated. Do not edit manually.")
    lines.append("- Regenerate with: `python scripts/generate_project_context.py`")
    lines.append("- `project_context.json` is the source of truth for full machine-readable context.")
    lines.append("- `PROJECT_CODE_LISTING.txt` contains exact file-by-file source listing.")
    return "\n".join(lines) + "\n"


def _iter_code_dump_files() -> List[Path]:
    files: List[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if set(rel.parts) & IGNORED_DIRS:
            continue
        if p.name in CODE_DUMP_EXCLUDED:
            continue
        if p.name.lower() in CODE_DUMP_FILENAMES or p.suffix.lower() in CODE_DUMP_EXTENSIONS:
            files.append(rel)
    files.sort(key=lambda x: x.as_posix())
    return files


def _build_code_listing_dump() -> str:
    files = _iter_code_dump_files()
    now = datetime.now(timezone.utc).isoformat()
    out: List[str] = []
    out.append("# PROJECT_CODE_LISTING")
    out.append("")
    out.append(f"# generated_utc: {now}")
    out.append(f"# git_sha: {_git_sha()}")
    out.append(f"# files_included: {len(files)}")
    out.append("# NOTE: exact content dump by file for maximum AI precision.")
    out.append("")

    for rel in files:
        content = _safe_read(ROOT / rel)
        out.append("=" * 120)
        out.append(f"FILE: {rel.as_posix()}")
        out.append("=" * 120)
        out.append(content)
        if not content.endswith("\n"):
            out.append("")
        out.append("")
    return "\n".join(out)


def main() -> None:
    modules = _discover_modules()
    payload = _build_json_payload(modules)
    markdown = _build_markdown(payload)
    code_dump = _build_code_listing_dump()

    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(markdown, encoding="utf-8")
    OUTPUT_CODE_DUMP.write_text(code_dump, encoding="utf-8")

    print(f"[ok] {OUTPUT_MD}")
    print(f"[ok] {OUTPUT_JSON}")
    print(f"[ok] {OUTPUT_CODE_DUMP}")


if __name__ == "__main__":
    main()
