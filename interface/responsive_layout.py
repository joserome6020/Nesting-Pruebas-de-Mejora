"""Utilidades para que la UI se adapte automáticamente a la resolución de cada PC."""
import tkinter as tk


def geometry_pantalla(
    ventana,
    ancho_ratio=0.94,
    alto_ratio=0.92,
    min_ancho=1100,
    min_alto=700,
):
    ventana.update_idletasks()
    sw = max(min_ancho, int(ventana.winfo_screenwidth() or 1280))
    sh = max(min_alto, int(ventana.winfo_screenheight() or 800))
    w = max(min_ancho, min(sw - 32, int(sw * ancho_ratio)))
    h = max(min_alto, min(sh - 64, int(sh * alto_ratio)))
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2 - 16)
    return w, h, x, y


def aplicar_geometry_pantalla(ventana, **kwargs):
    w, h, x, y = geometry_pantalla(ventana, **kwargs)
    ventana.geometry(f"{w}x{h}+{x}+{y}")
    return w, h, x, y


def configurar_ventana_principal(ventana, min_ancho=1000, min_alto=650):
    ventana.minsize(min_ancho, min_alto)
    ventana.resizable(True, True)


def maximizar_ventana(ventana):
    """Maximiza la ventana al arranque (Windows: zoomed). Respeta cambios manuales posteriores."""
    try:
        ventana.update_idletasks()
    except Exception:
        pass
    try:
        ventana.state("zoomed")
        return True
    except Exception:
        pass
    try:
        ventana.attributes("-zoomed", True)
        return True
    except Exception:
        pass
    aplicar_geometry_pantalla(ventana, ancho_ratio=0.96, alto_ratio=0.94)
    return False


def crear_paneo_horizontal(parent, bg="#CBD5E1"):
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(0, weight=1)
    paned = tk.PanedWindow(
        parent,
        orient=tk.HORIZONTAL,
        sashwidth=7,
        sashrelief=tk.FLAT,
        opaqueresize=True,
        bg=bg,
        bd=0,
        sashcursor="sb_h_double_arrow",
    )
    paned.grid(row=0, column=0, sticky="nsew")
    return paned


def anadir_panel_paneo(paned, widget, minsize=200, width=None):
    opts = {"minsize": minsize, "stretch": "always"}
    if width is not None:
        opts["width"] = width
    paned.add(widget, **opts)


def configurar_contenedor_expandible(parent, filas=1, columnas=1):
    for r in range(filas):
        parent.rowconfigure(r, weight=1)
    for c in range(columnas):
        parent.columnconfigure(c, weight=1)


def ancho_tarjeta_adaptable(ancho_total, min_ancho=440, max_ancho=760, ratio=0.48):
    return max(min_ancho, min(max_ancho, int(ancho_total * ratio)))


def margen_adaptable(ancho_total, minimo=12, maximo=32, ratio=0.022):
    return max(minimo, min(maximo, int(ancho_total * ratio)))


def bind_redimension_figura_matplotlib(canvas_widget, fig, on_resize=None, debounce_ms=90):
    """Ajusta el tamaño de la figura matplotlib al widget Tk (con debounce)."""
    state = {"after_id": None, "last": (0, 0)}

    def _apply(w, h):
        if w < 12 or h < 12:
            return
        if state["last"] == (w, h):
            return
        state["last"] = (w, h)
        dpi = fig.get_dpi()
        fig.set_size_inches(w / dpi, h / dpi, forward=False)
        if on_resize:
            on_resize()
        elif getattr(fig, "canvas", None):
            fig.canvas.draw_idle()

    def _on_configure(event):
        if event.widget is not canvas_widget:
            return
        w, h = event.width, event.height
        if state["after_id"]:
            try:
                canvas_widget.after_cancel(state["after_id"])
            except Exception:
                pass
        state["after_id"] = canvas_widget.after(debounce_ms, lambda ww=w, hh=h: _apply(ww, hh))

    canvas_widget.bind("<Configure>", _on_configure, add="+")
