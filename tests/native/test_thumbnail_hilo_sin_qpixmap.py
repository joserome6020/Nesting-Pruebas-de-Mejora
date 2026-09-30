"""Candado 2026-09-30: crash (access violation) del ANS al terminar un nest.

`_thread_generar_thumbnails` (PARTS) creaba QPixmap en hilo worker; el GC lo
liberaba fuera del hilo GUI mientras Qt pintaba. El hilo solo debe producir
QImage con buffer propio; la conversión a QPixmap va en el hilo GUI.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

import ezdxf  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

from interface.qt import visualizer  # noqa: E402
from interface.qt.tabs import tab_parts  # noqa: E402


def _dxf_temporal() -> str:
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (100, 0), (100, 60), (0, 60)], close=True, dxfattribs={"layer": "IV_OUTER_PROFILE"}
    )
    msp.add_circle((50, 30), 10, dxfattribs={"layer": "CUT_INNER"})
    fd, ruta = tempfile.mkstemp(suffix=".dxf")
    os.close(fd)
    doc.saveas(ruta)
    return ruta


def test_imagen_en_hilo_es_qimage():
    ruta = _dxf_temporal()
    out = {}

    def _w():
        out["img"] = visualizer.generar_thumbnail_imagen(ruta, size=(40, 40), material="A 36")

    t = threading.Thread(target=_w)
    t.start()
    t.join(60)
    os.remove(ruta)
    img = out.get("img")
    assert isinstance(img, QImage), type(img)
    assert not img.isNull()
    assert max(img.width(), img.height()) <= 40


def test_hilo_thumbnails_no_crea_qpixmap():
    src = inspect.getsource(tab_parts.TabParts._thread_generar_thumbnails)
    assert "generar_thumbnail_imagen(" in src
    assert "generar_thumbnail(" not in src.replace("generar_thumbnail_imagen(", "")
    assert "QPixmap" not in src
    src_img = inspect.getsource(visualizer.generar_thumbnail_imagen)
    assert "QPixmap." not in src_img and "QPixmap(" not in src_img


if __name__ == "__main__":
    test_imagen_en_hilo_es_qimage()
    test_hilo_thumbnails_no_crea_qpixmap()
    print("OK test_thumbnail_hilo_sin_qpixmap")
