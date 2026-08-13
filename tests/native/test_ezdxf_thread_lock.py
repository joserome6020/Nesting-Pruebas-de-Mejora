"""Candado: race audit + render ezdxf en Python 3.14 -> access violation.

Regresión 2026-08-13:
  - `_thread_auditar_dxfs` (tab_parts.py) hacía `ezdxf.readfile` masivo en fondo.
  - Simultaneamente la UI dibujaba otro DXF con `ezdxf.addons.drawing.pyqt`.
  - Bajo Python 3.14, el GC coleccionaba estructuras C internas de ezdxf
    mientras otro hilo las tocaba: crash duro (0xc0000005 en python314.dll).

Fix: `modules/dxf_thread_lock.EZDXF_LOCK` (RLock reentrante) serializa:
  * `geometry_parser.recuperar_geometria_robusta_detalle` (audit).
  * `dxf_part_loader.load_dxf_part` (UI load).
  * `dxf_qt_renderer.render_modelspace` y `rotate_modelspace` (UI draw).

Este test verifica:
  1. `EZDXF_LOCK` existe y es reentrante (RLock, no Lock).
  2. Los 3 archivos consumidores importan `EZDXF_LOCK`.
  3. Los wrappers públicos toman el lock antes de delegar al `_impl`.

Un test funcional (spawn de N hilos leyendo/dibujando) no es viable aquí sin
Qt inicializado; queda cubierto por el smoke manual documentado en el fix.
"""

from __future__ import annotations

import ast
import inspect
import threading
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _falla(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def test_ezdxf_lock_existe_y_es_reentrante() -> None:
    from modules.dxf_thread_lock import EZDXF_LOCK

    # RLock no tiene un type expuesto; comprobamos comportamiento: reentrante.
    if not EZDXF_LOCK.acquire(timeout=1.0):
        _falla("EZDXF_LOCK no se pudo adquirir la primera vez.")
    try:
        if not EZDXF_LOCK.acquire(timeout=1.0):
            _falla("EZDXF_LOCK no es reentrante (usar RLock, no Lock).")
        EZDXF_LOCK.release()
    finally:
        EZDXF_LOCK.release()
    print("  EZDXF_LOCK existe y es reentrante (RLock).")


def _archivo_importa_lock(rel_path: str) -> None:
    ruta = ROOT / rel_path
    if not ruta.is_file():
        _falla(f"No existe {rel_path}")
    src = ruta.read_text(encoding="utf-8")
    if "from modules.dxf_thread_lock import EZDXF_LOCK" not in src:
        _falla(
            f"{rel_path} no importa EZDXF_LOCK; el crash de ezdxf puede volver "
            "si un hilo toca ezdxf sin serialización."
        )
    print(f"  {rel_path} importa EZDXF_LOCK.")


def test_callsites_importan_lock() -> None:
    for rel in (
        "modules/nesting_engine/geometry_parser.py",
        "interface/qt/dxf_qt_renderer.py",
        "interface/qt/dxf_part_loader.py",
    ):
        _archivo_importa_lock(rel)


def _funcion_usa_with_ezdxf_lock(rel_path: str, func_name: str) -> None:
    ruta = ROOT / rel_path
    tree = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.With):
                    for item in sub.items:
                        expr = item.context_expr
                        if isinstance(expr, ast.Name) and expr.id == "EZDXF_LOCK":
                            print(f"  {rel_path}::{func_name} usa `with EZDXF_LOCK:`.")
                            return
            _falla(
                f"{rel_path}::{func_name} NO envuelve su cuerpo con `with EZDXF_LOCK:`; "
                "el crash audit+render puede volver."
            )
    _falla(f"No se encontró la función {func_name} en {rel_path}.")


def test_wrappers_toman_lock() -> None:
    _funcion_usa_with_ezdxf_lock(
        "modules/nesting_engine/geometry_parser.py",
        "recuperar_geometria_robusta_detalle",
    )
    _funcion_usa_with_ezdxf_lock(
        "interface/qt/dxf_part_loader.py",
        "load_dxf_part",
    )
    _funcion_usa_with_ezdxf_lock(
        "interface/qt/dxf_qt_renderer.py",
        "render_modelspace",
    )
    _funcion_usa_with_ezdxf_lock(
        "interface/qt/dxf_qt_renderer.py",
        "rotate_modelspace",
    )


def main() -> int:
    print("[candado] ezdxf thread lock (crash audit+render 2026-08-13)")
    test_ezdxf_lock_existe_y_es_reentrante()
    test_callsites_importan_lock()
    test_wrappers_toman_lock()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
