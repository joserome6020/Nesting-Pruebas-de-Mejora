"""Buzón de soporte: abre Outlook con el reporte y adjuntos listos."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import config
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
)

from interface.qt.theme import apply_push_button, surface_dialog_stylesheet
from interface.qt.thread_bridge import call_on_main


DESTINATARIO_SOPORTE = "Jose Rosales <jose_rosales@grupoarga.com>"
DESTINATARIO_EMAIL = "jose_rosales@grupoarga.com"
_MAX_ATTACHMENT_BYTES = 9 * 1024 * 1024


class _PasteableDescription(QPlainTextEdit):
    """Ctrl+V pega texto normal; si hay imagen en el portapapeles, la adjunta."""

    def __init__(self, on_image, parent=None):
        super().__init__(parent)
        self._on_image = on_image

    def insertFromMimeData(self, source) -> None:
        if source is not None and source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self._on_image(image)
            elif isinstance(image, QPixmap) and not image.isNull():
                self._on_image(image.toImage())
            if source.hasText() and str(source.text() or "").strip():
                super().insertFromMimeData(source)
            return
        super().insertFromMimeData(source)


def _outbox_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(config.ruta_persistente(os.path.join("_tmp", "buzon", stamp)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_body(*, category: str, description: str, app_context: dict) -> str:
    lines = [
        "Reporte ANS Nesting Suite",
        f"Para: {DESTINATARIO_SOPORTE}",
        f"Categoría: {category}",
        f"Fecha UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Descripción:",
        description,
        "",
        "Contexto técnico:",
        "- App: ARGA NESTING SUITE",
        f"- Python: {sys.version.split()[0]}",
        f"- Plataforma: {platform.platform()}",
        f"- Frozen: {bool(getattr(sys, 'frozen', False))}",
    ]
    for key, value in app_context.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def open_outlook_draft(*, subject: str, body: str, attachments: list[Path]) -> None:
    """Abre un borrador de Outlook desktop con adjuntos."""
    outbox = _outbox_dir()
    body_path = outbox / "body.txt"
    body_path.write_text(body, encoding="utf-8")
    attach_paths: list[str] = []
    for src in attachments:
        dst = outbox / src.name
        if src.resolve() != dst.resolve():
            dst.write_bytes(src.read_bytes())
        attach_paths.append(str(dst))
    meta = {
        "to": DESTINATARIO_EMAIL,
        "subject": subject,
        "body_path": str(body_path),
        "attachments": attach_paths,
    }
    meta_path = outbox / "draft.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    script_path = outbox / "open_draft.ps1"
    script_path.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$meta = Get-Content -LiteralPath '{meta_path}' -Raw -Encoding UTF8 | ConvertFrom-Json",
                "$body = Get-Content -LiteralPath $meta.body_path -Raw -Encoding UTF8",
                "$outlook = New-Object -ComObject Outlook.Application",
                "$mail = $outlook.CreateItem(0)",
                "$mail.To = $meta.to",
                "$mail.Subject = $meta.subject",
                "$mail.Body = $body",
                "foreach ($path in @($meta.attachments)) {",
                "  if ($path) { [void]$mail.Attachments.Add($path) }",
                "}",
                "$mail.Display()",
            ]
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or f"código {completed.returncode}"
        raise RuntimeError(detail)


class SupportInboxDialog(QDialog):
    """Formulario local; Outlook abre el borrador listo para enviar."""

    def __init__(self, parent, *, app_context: dict | None = None):
        super().__init__(parent)
        self._app_context = dict(app_context or {})
        self._attachments: list[Path] = []
        self._temp_dir = tempfile.TemporaryDirectory(prefix="arga-buzon-")

        self.setWindowTitle("BUZÓN · Reportar problema")
        self.setMinimumWidth(650)
        self.setModal(True)
        self.setStyleSheet(surface_dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(12)

        title = QLabel("Reportar problema a soporte")
        title.setStyleSheet("font-size:20px;font-weight:800;")
        layout.addWidget(title)
        detail = QLabel(
            f"Se abrirá Outlook con destino <b>{DESTINATARIO_SOPORTE}</b>, "
            "el texto del reporte y las capturas adjuntas. Solo revisa y pulsa Enviar."
        )
        detail.setWordWrap(True)
        detail.setObjectName("LabelMuted")
        layout.addWidget(detail)

        layout.addWidget(QLabel("Tipo de reporte"))
        self.category = QComboBox()
        self.category.addItems(("Error o cierre inesperado", "Resultado de nesting", "Exportación", "Mejora", "Otro"))
        layout.addWidget(self.category)

        layout.addWidget(QLabel("Describe qué ocurrió y cómo repetirlo"))
        self.description = _PasteableDescription(self._attach_clipboard_image)
        self.description.setPlaceholderText(
            "Escribe el problema. También puedes pegar aquí una captura con Ctrl+V "
            "(después de Shift+Win+S)."
        )
        self.description.setMinimumHeight(150)
        layout.addWidget(self.description)

        attach_header = QHBoxLayout()
        attach_header.addWidget(QLabel("Capturas y archivos adjuntos"))
        attach_header.addStretch()
        self.capture_button = QPushButton("CAPTURAR VENTANA")
        self.capture_button.clicked.connect(self._capture_window)
        apply_push_button(self.capture_button, "#FFFFFF", padding="6px 10px", font_size=10)
        attach_header.addWidget(self.capture_button)
        self.paste_button = QPushButton("PEGAR CAPTURA")
        self.paste_button.setToolTip("Pega la imagen del portapapeles (Shift+Win+S → Ctrl+V).")
        self.paste_button.clicked.connect(self._paste_clipboard_image)
        apply_push_button(self.paste_button, "#FFFFFF", padding="6px 10px", font_size=10)
        attach_header.addWidget(self.paste_button)
        self.add_file_button = QPushButton("AGREGAR ARCHIVO")
        self.add_file_button.clicked.connect(self._add_files)
        apply_push_button(self.add_file_button, "#FFFFFF", padding="6px 10px", font_size=10)
        attach_header.addWidget(self.add_file_button)
        layout.addLayout(attach_header)

        self.files = QListWidget()
        self.files.setMinimumHeight(90)
        self.files.currentRowChanged.connect(self._preview_selected)
        layout.addWidget(self.files)

        preview_row = QHBoxLayout()
        self.preview_label = QLabel("Vista previa del adjunto")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(180)
        self.preview_label.setStyleSheet(
            "background:#FFFFFF;border:1px solid #CFD7E6;border-radius:8px;color:#6F7D93;"
        )
        self.preview_label.setScaledContents(False)
        preview_row.addWidget(self.preview_label, 1)
        layout.addLayout(preview_row)

        remove = QPushButton("QUITAR SELECCIONADO")
        remove.clicked.connect(self._remove_selected)
        apply_push_button(remove, "#FFFFFF", padding="6px 10px", font_size=10)
        layout.addWidget(remove, alignment=Qt.AlignmentFlag.AlignRight)

        footer = QHBoxLayout()
        self.status = QLabel("Listo: se abrirá Outlook con el correo y adjuntos.")
        self.status.setWordWrap(True)
        self.status.setObjectName("LabelMuted")
        footer.addWidget(self.status, 1)
        cancel = QPushButton("CANCELAR")
        cancel.clicked.connect(self.reject)
        apply_push_button(cancel, "#FFFFFF", padding="6px 10px", font_size=10)
        footer.addWidget(cancel)
        self.send_button = QPushButton("ABRIR EN OUTLOOK")
        self.send_button.clicked.connect(self._send)
        apply_push_button(self.send_button, "#2F6DEA")
        footer.addWidget(self.send_button)
        layout.addLayout(footer)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._preview_selected(self.files.currentRow())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._preview_selected(self.files.currentRow())

    def _add_attachment(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            if path.stat().st_size > _MAX_ATTACHMENT_BYTES:
                QMessageBox.warning(self, "BUZÓN", f"{path.name} supera el límite de 9 MB por archivo.")
                return
        except OSError:
            return
        if path not in self._attachments:
            self._attachments.append(path)
            self.files.addItem(path.name)
            self.files.setCurrentRow(len(self._attachments) - 1)
            self._preview_selected(len(self._attachments) - 1)

    def _preview_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._attachments):
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Vista previa del adjunto")
            return
        path = self._attachments[row]
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(f"Sin vista previa\n{path.name}")
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(f"No se pudo cargar\n{path.name}")
            return
        target = self.preview_label.size()
        w = max(120, target.width() - 16)
        h = max(100, target.height() - 16)
        scaled = pixmap.scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled)

    def _add_files(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar capturas o archivos",
            "",
            "Archivos de soporte (*.png *.jpg *.jpeg *.bmp *.txt *.log *.pdf);;Todos (*.*)",
        )
        for raw_path in selected:
            self._add_attachment(Path(raw_path))

    def _capture_window(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        pixmap: QPixmap = parent.grab()
        if pixmap.isNull():
            QMessageBox.warning(self, "BUZÓN", "No fue posible capturar la ventana.")
            return
        target = Path(self._temp_dir.name) / f"captura_ans_{len(self._attachments) + 1}.png"
        if pixmap.save(str(target), "PNG"):
            self._add_attachment(target)

    def _clipboard_image(self) -> QImage | None:
        clip = QApplication.clipboard()
        if clip is None:
            return None
        mime = clip.mimeData()
        if mime is not None and mime.hasImage():
            image = mime.imageData()
            if isinstance(image, QImage) and not image.isNull():
                return image
            if isinstance(image, QPixmap) and not image.isNull():
                return image.toImage()
        pixmap = clip.pixmap()
        if pixmap is not None and not pixmap.isNull():
            return pixmap.toImage()
        return None

    def _attach_clipboard_image(self, image: QImage) -> None:
        if image is None or image.isNull():
            return
        target = Path(self._temp_dir.name) / f"captura_portapapeles_{len(self._attachments) + 1}.png"
        if image.save(str(target), "PNG"):
            self._add_attachment(target)
            self.status.setText(f"Captura pegada: {target.name}")

    def _paste_clipboard_image(self) -> None:
        image = self._clipboard_image()
        if image is None:
            QMessageBox.information(
                self,
                "BUZÓN",
                "No hay una imagen en el portapapeles.\n"
                "Haz Shift+Win+S, copia la zona y luego Ctrl+V en la descripción "
                "o pulsa PEGAR CAPTURA.",
            )
            return
        self._attach_clipboard_image(image)

    def _remove_selected(self) -> None:
        row = self.files.currentRow()
        if row >= 0:
            self.files.takeItem(row)
            self._attachments.pop(row)
            if self._attachments:
                self.files.setCurrentRow(min(row, len(self._attachments) - 1))
            else:
                self._preview_selected(-1)

    def _send(self) -> None:
        description = self.description.toPlainText().strip()
        if not description:
            QMessageBox.warning(self, "BUZÓN", "Describe el problema antes de enviarlo.")
            return
        category = self.category.currentText()
        subject = f"ANS · {category}"
        body = _build_body(category=category, description=description, app_context=self._app_context)
        self.send_button.setEnabled(False)
        self.status.setText("Abriendo Outlook…")
        attachments = list(self._attachments)

        def deliver() -> None:
            try:
                open_outlook_draft(subject=subject, body=body, attachments=attachments)
            except Exception as exc:
                call_on_main(lambda: self._delivery_failed(str(exc)))
                return
            call_on_main(self._delivery_succeeded)

        threading.Thread(target=deliver, name="arga-buzon", daemon=True).start()

    def _delivery_succeeded(self) -> None:
        QMessageBox.information(
            self,
            "BUZÓN",
            "Outlook abrió el correo con tus adjuntos.\nRevisa y pulsa Enviar ahí.",
        )
        self.accept()

    def _delivery_failed(self, error: str) -> None:
        self.send_button.setEnabled(True)
        self.status.setText("No se pudo abrir Outlook.")
        QMessageBox.critical(
            self,
            "BUZÓN",
            "No se pudo abrir Outlook.\n"
            "Confirma que Outlook de escritorio esté instalado e iniciado sesión.\n\n"
            f"{error}",
        )
