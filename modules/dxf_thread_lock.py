"""Lock global para acceso concurrente a ezdxf.

ezdxf (v1.4.x) NO es thread-safe. Cuando dos hilos leen/parsean/dibujan Drawings
distintos al mismo tiempo, el garbage collector de CPython 3.14 puede colectar
estructuras C internas mientras otro hilo las está tocando -> access violation
(0xc0000005 en python314.dll).

Regresión 2026-08-13:
  - Hilo `_thread_auditar_dxfs` (tab_parts.py) hace `ezdxf.readfile` masivo.
  - Hilo UI (main.py:143) simultáneamente hace `ezdxf.addons.drawing.pyqt.draw_path`
    al seleccionar una fila para mostrar el DXF en detalle.
  - Ambos rutinariamente terminan tocando `ezdxf.lldxf.tags.group_tags` y el
    pipeline de dibujo dentro del mismo ciclo de GC -> crash duro del .exe.

Este `RLock` se toma alrededor de:
  * `geometry_parser.recuperar_geometria_robusta_detalle` (hot-path del audit).
  * `dxf_qt_renderer.render_modelspace` (UI render).
  * `dxf_qt_renderer.rotate_modelspace` (mutación msp).

Es reentrante para permitir que un hilo tome el lock varias veces (por ejemplo,
un renderer que a su vez lee otro doc para overlay).

Latencia esperada: ~1-5 ms por adquisición en carga normal. La UI puede quedar
esperando 1-2 iteraciones del audit (~50 ms) al clickear una pieza mientras la
auditoría corre; imperceptible en la práctica.
"""

from __future__ import annotations

import threading

EZDXF_LOCK: threading.RLock = threading.RLock()
