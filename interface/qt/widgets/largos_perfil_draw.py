"""Dibujo de secciones transversales de perfiles estructurales (vista 1D/3D)."""
from __future__ import annotations

import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen

PERFIL_COLORES = {
    "ANGULO": "#F59E0B",
    "CANAL": "#22C55E",
    "TUBO": "#3B82F6",
    "PTR": "#A855F7",
    "SOLERA": "#94A3B8",
    "TUBULAR": "#06B6D4",
    "REDONDO": "#EC4899",
    "VARILLA": "#F97316",
    "VIGA": "#6366F1",
    "VIGA IPR": "#6366F1",
    "DEFAULT": "#64748B",
}


def parse_perfil_material(material: str) -> dict:
    """Extrae tipo de perfil y código Herinox del texto de clasificación."""
    txt = str(material or "").strip()
    upper = txt.upper()
    codigo = ""
    m = re.match(r"^([A-Z]{2,5}\d{2,4})", upper.replace(" ", ""))
    if m:
        codigo = m.group(1)
    tipo = "DEFAULT"
    for p in (
        "ANGULO", "ÁNGULO", "CANAL", "TUBULAR", "TUBO", "PTR", "SOLERA",
        "REDONDO", "VARILLA", "VIGA IPR", "VIGA",
    ):
        if p in upper:
            if "ANGUL" in p:
                tipo = "ANGULO"
            elif p.replace("Á", "A") == "VIGA IPR":
                tipo = "VIGA IPR"
            else:
                tipo = p.replace("Á", "A")
            break
    return {"codigo": codigo, "tipo": tipo, "etiqueta": tipo, "texto": txt}


_catalogo_visor_cache: list | None = None


def _catalogo_visor():
    global _catalogo_visor_cache
    if _catalogo_visor_cache is None:
        try:
            from catalogo_largos import _cargar_placas_largos_desde_herinox

            _catalogo_visor_cache = _cargar_placas_largos_desde_herinox(solo_disponibles=False) or []
        except Exception:
            _catalogo_visor_cache = []
    return _catalogo_visor_cache


def resolver_perfil_para_visor(material: str, codigo_hint: str = "") -> dict:
    """Resuelve perfil Herinox (catálogo + clasificación) para dibujo y título."""
    base = parse_perfil_material(material)
    cod = (
        str(codigo_hint or "").strip().upper()
        or str(base.get("codigo") or "").strip().upper()
    )
    try:
        from catalogo_largos import (
            buscar_placa_herinox,
            etiqueta_tipo_perfil_mrl,
            extraer_codigo_herinox_combo,
            normalizar_perfil,
            parse_clasificacion_largo,
        )

        if not cod:
            cod = extraer_codigo_herinox_combo(material)
        catalogo = _catalogo_visor()
        parsed = parse_clasificacion_largo(material)
        perfil = normalizar_perfil(parsed.get("perfil_estructural") or "")
        if not perfil and cod:
            placa = buscar_placa_herinox(material, 0.0, catalogo=catalogo, codigo_hint=cod)
            if placa:
                perfil = normalizar_perfil(placa.get("perfil_estructural") or "")
        if perfil:
            tipo = str(perfil).upper()
            etiqueta = etiqueta_tipo_perfil_mrl(material, cod, catalogo=catalogo)
            return {
                "codigo": cod or base.get("codigo") or "",
                "tipo": tipo,
                "etiqueta": etiqueta,
                "texto": material,
            }
        if base.get("tipo") != "DEFAULT":
            etiqueta = etiqueta_tipo_perfil_mrl(material, cod, catalogo=catalogo)
            return {**base, "etiqueta": etiqueta}
        etiqueta = etiqueta_tipo_perfil_mrl(material, cod, catalogo=catalogo)
        return {
            "codigo": cod or base.get("codigo") or "",
            "tipo": "DEFAULT",
            "etiqueta": etiqueta,
            "texto": material,
        }
    except Exception:
        return base


def color_perfil(tipo: str) -> QColor:
    return QColor(PERFIL_COLORES.get(str(tipo or "").upper(), PERFIL_COLORES["DEFAULT"]))


def _path_angulo(s: float) -> QPainterPath:
    """L clásica: vertical izquierda + horizontal abajo."""
    t = s * 0.24
    p = QPainterPath()
    p.moveTo(0, 0)
    p.lineTo(t, 0)
    p.lineTo(t, s - t)
    p.lineTo(s, s - t)
    p.lineTo(s, s)
    p.lineTo(0, s)
    p.closeSubpath()
    return p


def _path_canal(s: float) -> QPainterPath:
    """Canal tipo C cuadrado (abertura a la derecha)."""
    t = s * 0.22
    p = QPainterPath()
    p.moveTo(0, 0)
    p.lineTo(s - t, 0)
    p.lineTo(s - t, t)
    p.lineTo(t, t)
    p.lineTo(t, s - t)
    p.lineTo(s - t, s - t)
    p.lineTo(s - t, s)
    p.lineTo(0, s)
    p.closeSubpath()
    return p


def _path_ptr(s: float) -> QPainterPath:
    t = s * 0.22
    outer = QRectF(0, 0, s, s)
    inner = QRectF(t, t, s - 2 * t, s - 2 * t)
    p = QPainterPath()
    p.addRect(outer)
    p.addRect(inner)
    return p


def _path_tubo_hueco(s: float) -> QPainterPath:
    """Sección tubular circular hueca (tubo / tubular)."""
    p = QPainterPath()
    p.addEllipse(QRectF(0, 0, s, s))
    p2 = QPainterPath()
    p2.addEllipse(QRectF(s * 0.28, s * 0.28, s * 0.44, s * 0.44))
    return p.subtracted(p2)


def _path_redondo_varilla(s: float) -> QPainterPath:
    """Sección circular maciza (varilla / redondo)."""
    p = QPainterPath()
    p.addEllipse(QRectF(0, 0, s, s))
    return p


def _path_viga_ipr(s: float) -> QPainterPath:
    flange = s * 0.18
    web = max(2.0, s * 0.14)
    cx = (s - web) / 2.0
    p = QPainterPath()
    p.addRect(QRectF(0, 0, s, flange))
    p.addRect(QRectF(0, s - flange, s, flange))
    p.addRect(QRectF(cx, flange, web, max(1.0, s - 2 * flange)))
    return p


def path_seccion_perfil(tipo: str, size: float) -> QPainterPath:
    s = max(12.0, float(size))
    t = str(tipo or "").upper()
    if t == "ANGULO":
        return _path_angulo(s)
    if t == "CANAL":
        return _path_canal(s)
    if t == "PTR":
        return _path_ptr(s)
    if t in ("TUBO", "TUBULAR"):
        return _path_tubo_hueco(s)
    if t in ("VARILLA", "REDONDO"):
        return _path_redondo_varilla(s)
    if t in ("VIGA", "VIGA IPR"):
        return _path_viga_ipr(s)
    p = QPainterPath()
    p.addRect(QRectF(0, 0, s, s * 0.55))
    return p


def dibujar_seccion_perfil(
    p: QPainter,
    rect: QRectF,
    tipo: str,
    *,
    fill: QColor | None = None,
    stroke: QColor | None = None,
):
    """Dibuja la sección del perfil centrada en rect."""
    t = str(tipo or "").upper()
    base = fill or color_perfil(tipo)
    pen = QPen(stroke or base.darker(140), 1.2)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, base.lighter(125))
    grad.setColorAt(1, base.darker(115))

    side = min(rect.width(), rect.height()) * 0.88
    br = QRectF(0, 0, side, side)
    br.moveCenter(rect.center())

    # Círculos: dibujar nativo (evita polígonos al transformar el path)
    if t in ("VARILLA", "REDONDO"):
        p.setBrush(QBrush(grad))
        p.setPen(pen)
        p.drawEllipse(br)
        return
    if t in ("TUBO", "TUBULAR"):
        p.setBrush(QBrush(grad))
        p.setPen(pen)
        p.drawEllipse(br)
        hole = side * 0.44
        hole_off = (side - hole) / 2.0
        hole_rect = QRectF(br.left() + hole_off, br.top() + hole_off, hole, hole)
        p.setBrush(QColor("#0F172A"))
        p.setPen(QPen(base.darker(130), 0.8))
        p.drawEllipse(hole_rect)
        return

    path = path_seccion_perfil(tipo, side)
    br_path = path.boundingRect()
    tx = rect.center().x() - br_path.center().x()
    ty = rect.center().y() - br_path.center().y()
    xf = QPainterPath()
    for i in range(path.elementCount()):
        el = path.elementAt(i)
        if el.type == QPainterPath.ElementType.MoveToElement:
            xf.moveTo(el.x + tx, el.y + ty)
        elif el.type == QPainterPath.ElementType.LineToElement:
            xf.lineTo(el.x + tx, el.y + ty)
        elif el.type == QPainterPath.ElementType.CurveToElement:
            xf.cubicTo(el.x + tx, el.y + ty, el.x + tx, el.y + ty, el.x + tx, el.y + ty)
    p.setBrush(QBrush(grad))
    p.setPen(pen)
    p.drawPath(xf)


def dibujar_extrusion_3d(
    p: QPainter,
    x: float,
    y: float,
    w: float,
    h: float,
    depth: float,
    tipo: str,
    color: QColor,
    *,
    highlight: bool = False,
):
    """
    Segmento de barra con cara frontal (sección) y cara superior (extrusión).
    w = largo en pantalla, h = altura sección, depth = profundidad 3D.
    """
    d = depth
    top_color = color.lighter(105)
    front_color = color
    side_color = color.darker(108)

    # Cara superior (perspectiva)
    top = QPainterPath()
    top.moveTo(x, y)
    top.lineTo(x + w, y)
    top.lineTo(x + w + d, y - d)
    top.lineTo(x + d, y - d)
    top.closeSubpath()
    p.setBrush(top_color)
    p.setPen(QPen(side_color.darker(115), 0.8))
    p.drawPath(top)

    # Cara lateral derecha
    side = QPainterPath()
    side.moveTo(x + w, y)
    side.lineTo(x + w, y + h)
    side.lineTo(x + w + d, y + h - d)
    side.lineTo(x + w + d, y - d)
    side.closeSubpath()
    p.setBrush(side_color)
    p.setPen(QPen(side_color.darker(120), 0.8))
    p.drawPath(side)

    # Cara frontal
    front = QRectF(x, y, w, h)
    grad = QLinearGradient(front.topLeft(), front.bottomLeft())
    grad.setColorAt(0, front_color.lighter(108))
    grad.setColorAt(1, front_color.darker(104))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(front_color.darker(118), 1.0))
    p.drawRoundedRect(front, 2, 2)

    # Resalte hover/selección: borde claro sin oscurecer la pieza
    if highlight and w > 2 and h > 2:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#FDE68A"), 2.2))
        p.drawRoundedRect(front.adjusted(-1, -1, 1, 1), 3, 3)

    # Mini sección en el centro del segmento (solo si hay espacio)
    if w > 28 and h > 16:
        mini = min(h * 0.42, 18.0)
        mini_rect = QRectF(x + w / 2 - mini / 2, y + h / 2 - mini / 2, mini, mini)
        dibujar_seccion_perfil(
            p, mini_rect, tipo, fill=front_color.lighter(112), stroke=QColor("#F8FAFC"),
        )
