"""Orquesta el clasificador UF1/UF2 sobre un DXF de Robot Láser.

El paquete vendido (UF1_clasificador / UF2_clasificador) usa nombres de módulo
que chocan con ANS (`config`, `classification`, `dxf`). Cada cama se corre
aislada: se guarda/restaura sys.modules y sys.path.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

_ISOLATE_LOCK = threading.Lock()

_COLLIDE_TOP = frozenset(
    {
        "classification",
        "classification_config",
        "dxf",
        "scene_resolver",
        "json_source",
        "path_geometry",
        "placement_transform",
        "simple_r12_dxf",
        "lector_dxf",
        "config",
    }
)


def ls_ready_habilitado() -> bool:
    flag = os.environ.get("ARGA_LS_READY", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _package_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", "") or "")
        cand = meipass / "modules" / "ls_ready_paso1"
        if cand.is_dir():
            return cand
    return Path(__file__).resolve().parent


def _classifier_root(cama: str) -> Path:
    tag = str(cama or "").strip().upper()
    name = "UF1_clasificador" if tag == "A" else "UF2_clasificador"
    root = _package_dir() / name
    script = root / "run_ls_ready_flow.py"
    if not script.is_file():
        raise FileNotFoundError(f"Clasificador LS-READY {name} no encontrado: {script}")
    return root


def rutas_json_ls_ready_para_dxf(dxf_path: str | Path) -> tuple[Path, Path]:
    """JSON Cama A / Cama B junto a la familia Robot Láser (análogo a STEP/Cama A|B)."""
    dxf = Path(dxf_path).resolve()
    stem = dxf.stem
    parent = dxf.parent
    family = parent.parent if parent.name.upper() == "DXF" else parent
    return (
        family / "JSON" / "Cama A" / f"{stem}_LS_READY_UF1.json",
        family / "JSON" / "Cama B" / f"{stem}_LS_READY_UF2.json",
    )


def _should_swap_module(name: str) -> bool:
    return name.split(".", 1)[0] in _COLLIDE_TOP


def _run_one_classifier(cama: str, dxf_path: Path, out_json: Path, raw_dir: Path) -> dict[str, Any]:
    root = _classifier_root(cama)
    script = root / "run_ls_ready_flow.py"
    config_dir = root / "config"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    saved_modules: dict[str, Any] = {}
    for name in list(sys.modules):
        if _should_swap_module(name):
            saved_modules[name] = sys.modules.pop(name)

    old_path = list(sys.path)
    old_cwd = os.getcwd()
    old_argv = list(sys.argv)
    old_raw = os.environ.get("LS_FLOW_RAW_DIR")
    os.environ["LS_FLOW_RAW_DIR"] = str(raw_dir)

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    rc = 1
    err: str | None = None
    try:
        sys.path.insert(0, str(root))
        sys.path.insert(1, str(config_dir))
        os.chdir(str(root))
        sys.argv = [str(script), str(dxf_path), str(out_json)]
        spec = importlib.util.spec_from_file_location("_ans_ls_ready_flow", script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"No se pudo cargar {script}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_ans_ls_ready_flow"] = mod
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            spec.loader.exec_module(mod)
            js = sys.modules.get("json_source")
            if js is not None:
                js.LAST_JSON_POINTER = str(raw_dir / "ultimo_json.txt")
            rc = int(mod.main(sys.argv) or 0)
    except Exception as exc:
        err = f"{exc}\n{traceback.format_exc()}"
        rc = 1
    finally:
        sys.modules.pop("_ans_ls_ready_flow", None)
        root_key = str(root).replace("\\", "/").lower()
        for name in list(sys.modules):
            mod = sys.modules.get(name)
            file_name = str(getattr(mod, "__file__", "") or "").replace("\\", "/").lower()
            if root_key and root_key in file_name:
                sys.modules.pop(name, None)
            elif _should_swap_module(name) and name not in saved_modules:
                sys.modules.pop(name, None)
        sys.path[:] = old_path
        os.chdir(old_cwd)
        sys.argv = old_argv
        if old_raw is None:
            os.environ.pop("LS_FLOW_RAW_DIR", None)
        else:
            os.environ["LS_FLOW_RAW_DIR"] = old_raw
        sys.modules.update(saved_modules)

    log_txt = (buf_out.getvalue() + buf_err.getvalue()).strip()
    ok = rc == 0 and out_json.is_file() and out_json.stat().st_size > 32
    return {
        "ok": ok,
        "cama": cama,
        "path": str(out_json) if ok else None,
        "returncode": rc,
        "error": err,
        "log": log_txt[-4000:] if log_txt else "",
    }


def generar_ls_ready_desde_dxf(
    dxf_path: str | Path,
    *,
    out_uf1: str | Path | None = None,
    out_uf2: str | Path | None = None,
) -> dict[str, Any]:
    """Procesa un DXF de nest y escribe los dos JSON LS-READY (Cama A y Cama B)."""
    dxf = Path(dxf_path).resolve()
    if not dxf.is_file() or dxf.suffix.lower() != ".dxf":
        raise ValueError(f"DXF inválido: {dxf_path}")

    dest_a, dest_b = rutas_json_ls_ready_para_dxf(dxf)
    if out_uf1 is not None:
        dest_a = Path(out_uf1)
    if out_uf2 is not None:
        dest_b = Path(out_uf2)

    from modules.dxf_thread_lock import EZDXF_LOCK

    results: dict[str, Any] = {"dxf": str(dxf), "UF1": None, "UF2": None, "ok": False}
    with tempfile.TemporaryDirectory(prefix="ans_ls_ready_") as raw:
        raw_dir = Path(raw)
        with _ISOLATE_LOCK, EZDXF_LOCK:
            results["UF1"] = _run_one_classifier("A", dxf, dest_a, raw_dir)
            results["UF2"] = _run_one_classifier("B", dxf, dest_b, raw_dir)
    results["ok"] = bool(results["UF1"].get("ok") and results["UF2"].get("ok"))
    return results
