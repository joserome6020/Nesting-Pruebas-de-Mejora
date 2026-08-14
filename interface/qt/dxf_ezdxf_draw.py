"""Render fiel de DXF con ezdxf.addons.drawing sobre un axes matplotlib."""
from __future__ import annotations

import math


def prepare_cad_axes(ax, bg_color: str = "#0B1220") -> None:
    """Configura el axes para ocupar todo el lienzo (sin marco interno)."""
    ax.set_facecolor(bg_color)
    ax.set_position([0.0, 0.0, 1.0, 1.0])
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_clip_on(False)
    ax.patch.set_visible(True)


def rotate_modelspace(msp, cx: float, cy: float, rot_deg: int) -> None:
    """Rota entidades del modelspace in-place alrededor de (cx, cy)."""
    from ezdxf.math import Matrix44

    rot = int(rot_deg) % 360
    if rot == 0:
        return
    # ezdxf usa vectores fila (v' = v @ M): en `A @ B` se aplica A PRIMERO.
    # Centro al origen → rotar → devolver. Ver nota en dxf_qt_renderer.
    m = Matrix44.chain(
        Matrix44.translate(-cx, -cy, 0),
        Matrix44.z_rotate(math.radians(rot)),
        Matrix44.translate(cx, cy, 0),
    )
    for entity in list(msp):
        try:
            entity.transform(m)
        except Exception:
            pass


def draw_dxf_modelspace(ax, doc, msp, *, bg_color: str = "#0B1220") -> None:
    """Dibuja modelspace tal como ezdxf lo interpreta (capas, texto, arcos, etc.)."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    prepare_cad_axes(ax, bg_color)
    cfg = Configuration(
        background_policy=BackgroundPolicy.CUSTOM,
        custom_bg_color=bg_color,
    )
    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    Frontend(ctx, backend, config=cfg).draw_layout(msp, finalize=True)
    set_artists_no_clip(ax)


def set_artists_no_clip(ax) -> None:
    """Evita que la geometría se corte antes del borde del visor al hacer pan."""
    for artist in ax.get_children():
        setter = getattr(artist, "set_clip_on", None)
        if callable(setter):
            try:
                setter(False)
            except Exception:
                pass


def fit_axes_to_content(ax, margin: float = 0.08):
    """Encuadra y centra según lo dibujado (no el bbox teórico del DXF)."""
    ax.relim()
    ax.autoscale_view()
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    if abs(x1 - x0) < 1e-12 or abs(y1 - y0) < 1e-12:
        return None, None
    mx = (x1 - x0) * margin
    my = (y1 - y0) * margin
    xlim = (x0 - mx, x1 + mx)
    ylim = (y0 - my, y1 + my)
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_autoscale_on(False)
    return xlim, ylim
