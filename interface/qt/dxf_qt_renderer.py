"""Render fiel de DXF en QGraphicsScene con ezdxf.addons.drawing.pyqt."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QGraphicsScene

# ezdxf NO es thread-safe. Si el UI hilo dibuja mientras un thread de fondo
# (p.ej. `_thread_auditar_dxfs`) parsea otro DXF, el GC de Python 3.14 puede
# provocar access violation en `python314.dll`. Serializamos con EZDXF_LOCK.
# Ver modules/dxf_thread_lock.py y crash 2026-08-13.
from modules.dxf_thread_lock import EZDXF_LOCK


def rotate_modelspace(msp, cx: float, cy: float, rot_deg: int) -> None:
    """Rota entidades del modelspace in-place alrededor de (cx, cy)."""
    from ezdxf.math import Matrix44

    rot = int(rot_deg) % 360
    if rot == 0:
        return
    m = (
        Matrix44.translate(cx, cy, 0)
        @ Matrix44.z_rotate(math.radians(rot))
        @ Matrix44.translate(-cx, -cy, 0)
    )
    with EZDXF_LOCK:
        for entity in list(msp):
            try:
                entity.transform(m)
            except Exception:
                pass


def render_modelspace(
    doc,
    msp,
    scene: QGraphicsScene,
    *,
    bg_color: str = "#0B1220",
) -> QRectF:
    """Dibuja modelspace en la escena; devuelve rect de contenido."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
    from ezdxf.addons.drawing.pyqt import PyQtBackend

    from PySide6.QtGui import QBrush, QColor

    scene.setBackgroundBrush(QBrush(QColor(bg_color)))
    backend = PyQtBackend(scene)
    cfg = Configuration(
        background_policy=BackgroundPolicy.CUSTOM,
        custom_bg_color=bg_color,
    )
    with EZDXF_LOCK:
        ctx = RenderContext(doc)
        Frontend(ctx, backend, config=cfg).draw_layout(msp, finalize=True)
    rect = scene.sceneRect()
    if rect.isNull() or (rect.width() < 1e-9 and rect.height() < 1e-9):
        return QRectF()
    return rect
