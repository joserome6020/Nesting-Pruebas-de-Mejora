"""Visualización interactiva de tira de largo con perfil estructural (1D/3D)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from interface.qt.widgets.largos_perfil_draw import (
    color_perfil,
    dibujar_extrusion_3d,
    dibujar_seccion_perfil,
    resolver_perfil_para_visor,
)

KERF_IN = 0.25
RECORTE_EXTREMO_IN = 0.5

PIEZA_COLORES = [
    "#3B82F6", "#22C55E", "#F59E0B", "#A855F7", "#06B6D4",
    "#F97316", "#E879F9", "#84CC16", "#F43F5E", "#14B8A6",
]


class LargosTiraCanvas(QWidget):
    pieza_hover = Signal(int)
    pieza_click = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._barra: dict | None = None
        self._material = ""
        self._perfil = resolver_perfil_para_visor("")
        self._hover_idx = -1
        self._sel_idx = -1
        self._segmentos: list[dict] = []
        self._hover_tip = ""
        self.setMinimumHeight(202)
        self.setMaximumHeight(202)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setStyleSheet(
            "background:#0F172A;"
            "border-radius:12px;border:1px solid #334155;"
        )

    def mostrar_barra(self, material: str, barra: dict | None, pieza_sel: int = -1):
        self._material = str(material or "")
        cod_hint = ""
        if isinstance(barra, dict):
            cod_hint = str(barra.get("_vista_codigo") or "")
        self._perfil = resolver_perfil_para_visor(self._material, cod_hint)
        self._barra = barra if isinstance(barra, dict) else None
        self._sel_idx = int(pieza_sel)
        self._hover_idx = -1
        self._hover_tip = ""
        self._segmentos = []
        self.update()

    def _hit_test(self, pos) -> int:
        for seg in self._segmentos:
            if seg["rect"].contains(pos):
                return int(seg["idx"])
        return -1

    def mouseMoveEvent(self, event):
        idx = self._hit_test(event.position())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._hover_tip = ""
            if idx >= 0 and self._barra:
                cortes = self._barra.get("cortes") or []
                if 0 <= idx < len(cortes):
                    c = cortes[idx]
                    nom = str(c.get("nombre") or "")
                    largo = float(c.get("largo") or 0)
                    etiqueta = str(c.get("_etiqueta_pieza") or nom)
                    self._hover_tip = f"{etiqueta}  ·  {largo:.2f}\""
                    self.pieza_hover.emit(idx)
            self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._hit_test(event.position())
            if idx >= 0:
                self._sel_idx = idx
                self.pieza_click.emit(idx)
                self.update()
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        self._hover_idx = -1
        self._hover_tip = ""
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w_total = self.width()
        h_total = self.height()
        margin = 12
        footer_h = 50
        header_h = 18
        ruler_h = 18
        depth = 10
        perfil_w = 72

        if not self._barra:
            p.fillRect(self.rect(), QColor("#0F172A"))
            p.setPen(QColor("#94A3B8"))
            p.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Selecciona una barra comercial")
            p.end()
            return

        p.fillRect(self.rect(), QColor("#0F172A"))

        bar_y = margin + header_h + depth
        sec_h = 44
        bar_x0 = margin + perfil_w
        bar_w = max(80, w_total - bar_x0 - margin)

        stock = float(self._barra.get("largo_stock") or 0)
        source = str(self._barra.get("source") or "STOCK").upper()
        cortes = list(self._barra.get("cortes") or [])
        tipo = self._perfil.get("tipo") or "DEFAULT"
        tipo_lbl = str(self._perfil.get("etiqueta") or tipo)
        pcol = color_perfil(tipo)
        unidad = int(self._barra.get("_vista_comercial_unidad") or 0)
        cant_grupo = int(self._barra.get("_vista_cant_grupo") or 0)
        largo_com = float(self._barra.get("_vista_largo_comercial") or stock)
        nota_nesteo = str(self._barra.get("_vista_nota_nesteo") or "").strip()

        # Cabecera — siempre barra comercial (ej. 240")
        p.setPen(QColor("#F8FAFC"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        cod = self._perfil.get("codigo") or ""
        titulo = f"{cod}  ·  {tipo_lbl}  ·  {source}  ·  {largo_com:.0f}\" comercial"
        if unidad and cant_grupo:
            titulo += f"  ·  #{unidad}/{cant_grupo}"
        if source == "REMANENTE" and self._barra.get("rem_id"):
            titulo += f"  ·  REM {self._barra.get('rem_id')}"
        p.drawText(margin, margin + 13, titulo)

        if stock <= 0:
            p.setPen(QColor("#F87171"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Tira sin largo válido")
            p.end()
            return

        # Panel sección
        sec_rect = QRectF(margin, bar_y, perfil_w - 8, sec_h)
        p.setPen(QPen(QColor("#94A3B8"), 1.2))
        p.setBrush(QColor("#1E293B"))
        p.drawRoundedRect(sec_rect.adjusted(-2, -2, 2, 2), 6, 6)
        dibujar_seccion_perfil(p, sec_rect, tipo, fill=pcol, stroke=QColor("#F8FAFC"))
        p.setPen(QColor("#CBD5E1"))
        p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        p.drawText(int(sec_rect.left()), int(sec_rect.bottom() + 11), "SECCIÓN")

        escala = bar_w / stock
        util = max(0.0, stock - 2 * RECORTE_EXTREMO_IN)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawRoundedRect(int(bar_x0 + 2), bar_y + sec_h + 3, int(bar_w), 5, 2, 2)

        stock_rect = QRectF(bar_x0, bar_y, bar_w, sec_h)
        p.setBrush(QColor("#1E293B"))
        p.setPen(QPen(QColor("#64748B"), 1.2))
        p.drawRoundedRect(stock_rect, 4, 4)

        trim_w = RECORTE_EXTREMO_IN * escala
        p.setBrush(QColor("#991B1B"))
        p.setPen(Qt.PenStyle.NoPen)
        if trim_w > 1:
            p.drawRect(int(bar_x0), int(bar_y), int(trim_w), sec_h)
            p.drawRect(int(bar_x0 + bar_w - trim_w), int(bar_y), int(trim_w), sec_h)

        p.setPen(QPen(QColor("#94A3B8"), 1))
        p.setFont(QFont("Segoe UI", 7))
        tick_step = max(12, int(stock // 8) or 12)
        label_step = max(24, int(stock // 4) or 24)
        for inch in range(0, int(stock) + 1, tick_step):
            rx = bar_x0 + inch * escala
            p.drawLine(int(rx), int(bar_y + sec_h + 1), int(rx), int(bar_y + sec_h + 7))
            if inch % label_step == 0 or inch == 0:
                p.setPen(QColor("#E2E8F0"))
                p.drawText(int(rx - 8), int(bar_y + sec_h + 17), f'{inch}"')
                p.setPen(QPen(QColor("#94A3B8"), 1))

        self._segmentos = []
        cursor_x = bar_x0 + RECORTE_EXTREMO_IN * escala
        ux_end = bar_x0 + (stock - RECORTE_EXTREMO_IN) * escala
        hover_rect = QRectF()

        for i, corte in enumerate(cortes):
            largo = float(corte.get("largo") or 0)
            if largo <= 0:
                continue
            if i > 0:
                kw = KERF_IN * escala
                p.setBrush(QColor("#334155"))
                p.setPen(QPen(QColor("#F87171"), 1))
                p.drawRect(int(cursor_x), int(bar_y + 4), max(1, int(kw)), sec_h - 8)
                cursor_x += kw

            pw = largo * escala
            base_col = QColor(PIEZA_COLORES[i % len(PIEZA_COLORES)])
            highlight = i == self._hover_idx or i == self._sel_idx
            dibujar_extrusion_3d(
                p, cursor_x, bar_y + 2, pw, sec_h - 4, depth,
                tipo, base_col, highlight=highlight,
            )

            seg_rect = QRectF(cursor_x, bar_y - depth, pw, sec_h + depth)
            self._segmentos.append({"idx": i, "rect": seg_rect})
            if highlight:
                hover_rect = seg_rect

            if pw > 34:
                p.setPen(QColor("#FFFFFF"))
                p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                nom = str(corte.get("_etiqueta_pieza") or corte.get("nombre") or "")[:20]
                p.drawText(int(cursor_x + 3), int(bar_y + sec_h - 5), nom)
                p.setFont(QFont("Segoe UI", 7))
                p.drawText(int(cursor_x + 3), int(bar_y + 11), f'{largo:.1f}"')

            cursor_x += pw

        used_real = 0.0
        for i, corte in enumerate(cortes):
            if i > 0:
                used_real += KERF_IN
            used_real += float(corte.get("largo") or 0)

        rem = max(0.0, util - used_real)
        overflow = used_real > util + 0.02
        ocupado = min(used_real, util) if util > 0 else 0.0
        efi = (ocupado / util * 100.0) if util > 0 else 0.0
        efi_col = "#EF4444" if overflow else "#22C55E" if efi >= 70 else "#F59E0B" if efi >= 40 else "#EF4444"

        if rem > 0.5 and cursor_x < ux_end - 2:
            rw = ux_end - cursor_x
            p.setBrush(QColor("#475569"))
            p.setPen(QPen(QColor("#CBD5E1"), 1, Qt.PenStyle.DashLine))
            p.drawRect(int(cursor_x), int(bar_y + 5), int(rw), sec_h - 10)
            p.setPen(QColor("#F8FAFC"))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            if rw > 36:
                p.drawText(int(cursor_x + 5), int(bar_y + sec_h // 2 + 4), f'Sobra {rem:.1f}"')

        if self._hover_tip and not hover_rect.isEmpty():
            tip_w = min(280, max(120, len(self._hover_tip) * 6))
            tip_h = 22
            tx = min(
                w_total - margin - tip_w,
                max(margin, hover_rect.center().x() - tip_w / 2),
            )
            ty = max(margin, bar_y - depth - tip_h - 4)
            tip_rect = QRectF(tx, ty, tip_w, tip_h)
            p.setBrush(QColor("#1E40AF"))
            p.setPen(QPen(QColor("#93C5FD"), 1))
            p.drawRoundedRect(tip_rect, 4, 4)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.drawText(tip_rect, Qt.AlignmentFlag.AlignCenter, self._hover_tip)

        footer = QRectF(0, h_total - footer_h, w_total, footer_h)
        p.fillRect(footer, QColor("#020617"))
        p.setPen(QPen(QColor("#334155"), 1))
        p.drawLine(0, int(footer.top()), w_total, int(footer.top()))

        stats_y = int(footer.top()) + 13
        p.setPen(QColor("#F1F5F9"))
        p.setFont(QFont("Segoe UI", 9))
        linea1 = (
            f'Stock: {stock:.2f}"  ·  Útil: {util:.2f}"  ·  '
            f'Cortes: {len(cortes)}  ·  Kerf: {KERF_IN}"'
        )
        p.drawText(margin, stats_y, linea1)
        p.setPen(QColor(efi_col))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        efi_txt = f"Aprovechamiento: {efi:.1f}%"
        if overflow:
            efi_txt += "  ·  EXCEDE ÚTIL"
        p.drawText(margin, stats_y + 16, efi_txt)

        p.setFont(QFont("Segoe UI", 8))
        if nota_nesteo:
            p.setPen(QColor("#93C5FD"))
            fm = p.fontMetrics()
            nw = fm.horizontalAdvance(nota_nesteo)
            p.drawText(int(w_total - margin - nw), stats_y + 16, nota_nesteo)
        hint = "Pasa el cursor sobre cada tramo · clic para resaltar pieza"
        hint_w = p.fontMetrics().horizontalAdvance(hint)
        p.setPen(QColor("#64748B"))
        p.drawText(int(max(margin, w_total - margin - hint_w)), stats_y + 30, hint)

        p.end()
