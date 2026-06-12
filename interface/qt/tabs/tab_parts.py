"""Tab PARTS — PySide6 nativo."""
from __future__ import annotations

import os
import csv
import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from interface.qt.visualizer import VisorDXF, generar_thumbnail
from interface.qt.ui_mixins import TimerHost, scroll_clear, scroll_add_widget
from interface.qt.layout_helpers import (
    finalize_splitter,
    make_card,
    make_herinox_card,
    make_horizontal_splitter,
    make_scroll,
    make_scroll_content,
)

from interface.qt.theme import COLOR_BORDE, COLOR_GRIS_DARK, COLOR_GRIS_MED, COLOR_TEXTO_TITULO, apply_push_button

COLOR_TARJETA = "#FFFFFF"
COLOR_HOVER = "#E2E8F0"
ARGB_BTN_1 = "#202A36"
ARGB_BTN_2 = "#334659"
ARGB_BTN_3 = "#455E75"
ARGB_BTN_4 = "#708DA9"


class _NombrePiezaLabel(QLabel):
    """Nombre truncado con elipsis; clic para ver el nombre completo."""

    def __init__(self, texto: str, parent_row: QFrame, on_select=None, parent=None):
        super().__init__(parent)
        self._full = str(texto or "")
        self._expanded = False
        self._parent_row = parent_row
        self._on_select = on_select
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};")
        self._refresh()

    def _refresh(self):
        if self._expanded:
            self.setText(self._full)
            self.setWordWrap(True)
            self.setToolTip("Clic para contraer")
            self._parent_row.setMinimumHeight(max(48, self.sizeHint().height() + 10))
        else:
            fm = self.fontMetrics()
            self.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideRight, 200))
            self.setWordWrap(False)
            self.setToolTip(f"{self._full}\n\nClic para ver completo")
            self._parent_row.setFixedHeight(48)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._expanded = not self._expanded
            self._refresh()
            if callable(self._on_select):
                self._on_select()
            event.accept()
            return
        super().mousePressEvent(event)


class TabParts(QWidget, TimerHost):
    def __init__(self, master, app_principal):
        QWidget.__init__(self, master)
        TimerHost.__init__(self)
        self.app = app_principal
        self._row_widgets = {}

        self.local_col_config = [
            {"weight": 3, "min": 160},
            {"weight": 2, "min": 100},
            {"weight": 1, "min": 50},
            {"weight": 1, "min": 80},
            {"weight": 1, "min": 70},
            {"weight": 1, "min": 90},
            {"weight": 1, "min": 70},
        ]

        # Estado para lista de largos
        self.btn_lista_largos = None
        self.ventana_lista_largos = None

        self.rutas_dxf_actuales = []

        self.setup_ui()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = make_horizontal_splitter(720)
        frame_tabla = make_card()
        tabla_lay = QVBoxLayout(frame_tabla)
        tabla_lay.setContentsMargins(16, 16, 12, 16)

        frame_header = QWidget()
        hdr = QHBoxLayout(frame_header)
        self.lbl_tanques = QLabel("TANQUES DEL PROYECTO:")
        self.lbl_tanques.setStyleSheet(f"font-weight:700;color:{COLOR_GRIS_DARK};font-size:15px;")
        hdr.addWidget(self.lbl_tanques)
        self.ent_tanques = QLineEdit("X1")
        self.ent_tanques.setFixedWidth(70)
        self.ent_tanques.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(self.ent_tanques)
        self.btn_aplicar_tanques = QPushButton("Aplicar")
        apply_push_button(self.btn_aplicar_tanques, ARGB_BTN_2, font_size=11)
        self.btn_aplicar_tanques.clicked.connect(self.aplicar_cantidad_tanques)
        hdr.addWidget(self.btn_aplicar_tanques)
        self.ent_tanques.returnPressed.connect(self.aplicar_cantidad_tanques)
        hdr.addStretch()
        self.btn_lista_largos = QPushButton("Lista de largos")
        apply_push_button(self.btn_lista_largos, ARGB_BTN_3, font_size=11)
        self.btn_lista_largos.clicked.connect(self.abrir_ventana_lista_largos)
        hdr.addWidget(self.btn_lista_largos)
        tabla_lay.addWidget(frame_header)

        head = QFrame()
        head.setObjectName("TableHeader")
        head.setFrameShape(QFrame.Shape.NoFrame)
        head.setFixedHeight(42)
        head_grid = QGridLayout(head)
        head_grid.setContentsMargins(10, 0, 10, 0)
        titulos = ["PIEZA / REF", "MATERIAL", "QTY", "TOTAL QTY", "CALIBRE", "ESTADO", "VISTA"]
        for i, txt in enumerate(titulos):
            lbl = QLabel(txt)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            head_grid.addWidget(lbl, 0, i)
        for i, conf in enumerate(self.local_col_config):
            head_grid.setColumnStretch(i, conf["weight"])
            head_grid.setColumnMinimumWidth(i, conf["min"])
        tabla_lay.addWidget(head)

        self.lista_scroll = make_scroll()
        self._lista_inner, self._lista_layout = make_scroll_content()
        self._lista_layout.setSpacing(2)
        self.lista_scroll.setWidget(self._lista_inner)
        tabla_lay.addWidget(self.lista_scroll, 1)
        splitter.addWidget(frame_tabla)

        frame_visor_bg = make_card()
        vis_lay = QVBoxLayout(frame_visor_bg)
        vis_lay.setContentsMargins(16, 16, 16, 16)
        tit = QLabel("DETALLE DE PIEZA")
        tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
        tit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vis_lay.addWidget(tit)
        self.frame_black_visor = QFrame()
        self.frame_black_visor.setStyleSheet("background:#0F172A;border-radius:10px;border:none;")
        QVBoxLayout(self.frame_black_visor)
        self.visor = VisorDXF(self.frame_black_visor)
        vis_lay.addWidget(self.frame_black_visor, 1)
        splitter.addWidget(frame_visor_bg)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        finalize_splitter(splitter, min_left=420, min_right=340)
        root.addWidget(splitter)

    def refrescar_tabla(self, datos):
        multiplicador = getattr(self.app, "multiplicador_tanques", 1)
        self.lbl_tanques.setText("TANQUES DEL PROYECTO:")
        try:
            self.ent_tanques.setText(f"X{int(multiplicador)}")
        except Exception:
            pass

        self.rutas_dxf_actuales = []
        scroll_clear(self.lista_scroll)
        self._row_widgets = {}

        for idx, item in enumerate(datos):
            pieza, mat, qty_total, cal, st, ruta = item
            if ruta:
                self.rutas_dxf_actuales.append(str(ruta))
            try:
                tot_val = int(qty_total)
                qty_unidad = max(1, tot_val // multiplicador)
            except Exception:
                tot_val, qty_unidad = qty_total, qty_total

            color_fondo = "#FFFFFF" if idx % 2 == 0 else "#F8FAFC"
            row = QFrame()
            row.setObjectName("PartsRowAlt" if idx % 2 else "PartsRow")
            row.setFrameShape(QFrame.Shape.NoFrame)
            row.setFixedHeight(48)
            row.orig_name = row.objectName()
            row.orig_color = color_fondo
            row_lay = QGridLayout(row)
            row_lay.setContentsMargins(4, 2, 4, 2)
            for i, conf in enumerate(self.local_col_config):
                row_lay.setColumnStretch(i, conf["weight"])
                row_lay.setColumnMinimumWidth(i, conf["min"])

            valores = [pieza, mat, str(qty_unidad), str(tot_val), cal, st]
            for i, conf in enumerate(self.local_col_config):
                if i < 6:
                    if i == 0:
                        lbl = _NombrePiezaLabel(
                            pieza,
                            row,
                            on_select=lambda r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p),
                        )
                    else:
                        lbl = QLabel(valores[i])
                        lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};")
                        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        lbl.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p)
                    row_lay.addWidget(lbl, 0, i)
                elif i == 6:
                    try:
                        thumb = generar_thumbnail(ruta, size=(32, 32))
                        if thumb:
                            l_t = QLabel()
                            l_t.setPixmap(thumb)
                            l_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
                            l_t.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p)
                            row_lay.addWidget(l_t, 0, i)
                    except Exception:
                        pass

            row.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p)
            scroll_add_widget(self.lista_scroll, row)

    def _resolver_job_data_csv_actual(self):
        rutas = [str(r[5]) for r in (getattr(self.app, "datos_partes_actuales", []) or []) if len(r) > 5 and r[5]]
        if not rutas:
            return None

        job = str(getattr(self.app, "job_activo", "") or "").strip()
        for ruta in rutas:
            p = Path(ruta)
            candidatos = []
            for actual in [p.parent, *p.parents]:
                if job:
                    candidatos.append(actual / f"job_data_{job}.csv")
                candidatos.extend(sorted(actual.glob("job_data_*.csv")))
            for c in candidatos:
                if c.exists() and c.is_file():
                    return c
        return None

    def _persistir_multiplicador_en_job_data(self, nuevo_mult: int):
        ruta_csv = self._resolver_job_data_csv_actual()
        actualizo_algo = False
        detalle = []

        if ruta_csv is not None:
            try:
                with open(ruta_csv, newline="", encoding="utf-8", errors="ignore") as f:
                    rows = list(csv.reader(f))
                if rows:
                    while len(rows) < 2:
                        rows.append([])
                    while len(rows[1]) <= 3:
                        rows[1].append("")
                    rows[1][3] = str(int(nuevo_mult))
                    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        writer.writerows(rows)
                    actualizo_algo = True
                    detalle.append(ruta_csv.name)
            except Exception:
                pass

        # Compatibilidad con archivo legacy: job_data_job / .txt / .json
        rutas = [str(r[5]) for r in (getattr(self.app, "datos_partes_actuales", []) or []) if len(r) > 5 and r[5]]
        for ruta in rutas:
            p = Path(ruta)
            for actual in [p.parent, *p.parents]:
                for nombre in ("job_data_job.json", "job_data_job.txt", "job_data_job"):
                    legacy = actual / nombre
                    if not legacy.exists() or not legacy.is_file():
                        continue
                    try:
                        txt = legacy.read_text(encoding="utf-8", errors="ignore")
                        if nombre.endswith(".json") or txt.strip().startswith("{"):
                            data = json.loads(txt) if txt.strip() else {}
                            if not isinstance(data, dict):
                                data = {}
                            data["cantidad_tanques"] = int(nuevo_mult)
                            data["multiplicador_tanques"] = int(nuevo_mult)
                            legacy.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                        else:
                            nuevo = re.sub(
                                r"(?im)^(\s*(?:cantidad_tanques|multiplicador_tanques)\s*[:=]\s*)\d+\s*$",
                                rf"\g<1>{int(nuevo_mult)}",
                                txt,
                            )
                            if nuevo == txt:
                                nuevo = txt.rstrip() + f"\nmultiplicador_tanques={int(nuevo_mult)}\n"
                            legacy.write_text(nuevo, encoding="utf-8")
                        actualizo_algo = True
                        detalle.append(legacy.name)
                    except Exception:
                        continue
                if actualizo_algo:
                    break
            if actualizo_algo:
                break

        if actualizo_algo:
            return True, ", ".join(sorted(set(detalle)))
        return False, "No se encontró job_data_*.csv ni job_data_job del proyecto actual."

    def aplicar_cantidad_tanques(self):
        valor = str(self.ent_tanques.text() or "").strip().upper()
        if valor.startswith("X"):
            valor = valor[1:].strip()
        if not valor.isdigit() or int(valor) <= 0:
            QMessageBox.critical(self, "Valor inválido", "Ingresa una cantidad válida, por ejemplo: X10")
            return

        nuevo_mult = int(valor)
        mult_actual = max(1, int(getattr(self.app, "multiplicador_tanques", 1) or 1))

        ok, msg = self._persistir_multiplicador_en_job_data(nuevo_mult)
        if not ok:
            QMessageBox.critical(self, "No se pudo actualizar", msg)
            return

        nuevos_datos = []
        for fila in getattr(self.app, "datos_partes_actuales", []) or []:
            try:
                pieza, mat, qty_total, cal, st, ruta = fila
                qty_total_int = int(str(qty_total).strip())
                qty_base = max(1, qty_total_int // mult_actual)
                nuevos_total = qty_base * nuevo_mult
                nuevos_datos.append((pieza, mat, str(nuevos_total), cal, st, ruta))
            except Exception:
                nuevos_datos.append(fila)

        self.app.multiplicador_tanques = nuevo_mult
        self.app.cargar_datos_parts(nuevos_datos)
        QMessageBox.information(self, "Actualizado", f"Cantidad de tanques actualizada a X{nuevo_mult}.")

    def seleccionar_fila(self, ruta_dxf, frame_fila, nombre_pieza):
        inner = self.lista_scroll.widget()
        if inner:
            for i in range(self._lista_layout.count()):
                w = self._lista_layout.itemAt(i).widget()
                if w and hasattr(w, "orig_name"):
                    w.setObjectName(w.orig_name)
                    w.setStyleSheet("")
        frame_fila.setObjectName("PartsRow")
        frame_fila.setStyleSheet("background:#DBEAFE;border-radius:6px;")

        if os.path.exists(ruta_dxf):
            self.visor.renderizar_dxf(ruta_dxf)
            # Mantener una sola fuente de verdad para medidas: el propio render del visor (con detección de unidades).
            self.visor.actualizar_info_extra(referencia=nombre_pieza)

    # =========================================================
    # HELPERS GENERALES AUTODXF
    # =========================================================
    def _resolver_autodxf_desde_ruta(self, ruta_archivo: str):
        try:
            p = Path(str(ruta_archivo))
        except Exception:
            return None

        candidatos = [p]
        candidatos.extend(p.parents)

        for actual in candidatos:
            nombre = actual.name.strip().lower()

            if nombre == "autodxf":
                return actual

            if nombre == "processed files":
                padre = actual.parent
                if padre.name.strip().lower() == "autodxf":
                    return padre

        return None

    def _resolver_job_desde_autodxf(self, ruta_autodxf: Path) -> str:
        """
        Intenta sacar el nombre del job desde la ruta:
        .../<JOB>/MODEL CORE FILES/AutoDXF
        """
        try:
            actual = ruta_autodxf
            while actual.parent != actual:
                if actual.name.strip().lower() == "model core files":
                    return actual.parent.name
                actual = actual.parent
        except Exception:
            pass

        try:
            return ruta_autodxf.parent.name
        except Exception:
            return "JOB_DESCONOCIDO"

    def _normalizar_key_csv(self, value: str) -> str:
        text = str(value or "").strip().lower().lstrip("\ufeff")
        text = (
            text.replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
        )
        return text

    def _safe_int(self, value, default=0):
        try:
            if value is None or value == "":
                return default
            return int(float(value))
        except Exception:
            return default

    def _normalizar_nombre_dxf(self, value: str) -> str:
        txt = str(value or "").replace("\\", "/").strip().lower()
        txt = os.path.basename(txt)
        txt = " ".join(txt.split())
        return txt

    # =========================================================
    # LISTA DE LARGOS DESDE CSV EN AUTODXF
    # =========================================================
    def _resolver_csv_lista_largos(self, ruta_autodxf: Path):
        candidatos_exactos = [
            "Lista_Perfiles_Clasificados.csv",
            "materiales_input.csv",
            "Lista_Largos.csv",
        ]

        for nombre in candidatos_exactos:
            ruta = ruta_autodxf / nombre
            if ruta.exists() and ruta.is_file():
                return ruta

        try:
            for archivo in sorted(ruta_autodxf.glob("*.csv")):
                nombre = archivo.name.lower()
                if "lista" in nombre and ("perfil" in nombre or "larg" in nombre):
                    return archivo
        except Exception:
            pass

        return None

    def _mapear_columnas_lista_largos(self, fieldnames):
        mapa = {self._normalizar_key_csv(c): c for c in (fieldnames or [])}
        return {
            "nombre": mapa.get("nombre"),
            "clasificacion": mapa.get("clasificacion") or mapa.get("clasificación"),
            "largo_in": mapa.get("largo (in)") or mapa.get("largo"),
            "cantidad": mapa.get("cantidad") or mapa.get("qty"),
        }

    def _leer_csv_lista_largos(self, csv_path: Path):
        encodings = ("utf-8-sig", "cp1252", "latin-1")
        ultimo_error = None

        for enc in encodings:
            try:
                with csv_path.open("r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    columnas = self._mapear_columnas_lista_largos(reader.fieldnames or [])

                    if not columnas["nombre"] or not columnas["cantidad"]:
                        raise ValueError(
                            f"CSV sin columnas mínimas esperadas. Detectadas: {reader.fieldnames}"
                        )

                    rows = []
                    for raw in reader:
                        nombre = str(raw.get(columnas["nombre"], "")).strip()
                        clasificacion = str(raw.get(columnas["clasificacion"], "")).strip() if columnas["clasificacion"] else ""
                        largo_txt = str(raw.get(columnas["largo_in"], "0")).strip() if columnas["largo_in"] else "0"
                        cantidad_txt = str(raw.get(columnas["cantidad"], "0")).strip()

                        if not nombre:
                            continue

                        try:
                            largo_in = round(float(largo_txt or 0), 3)
                        except Exception:
                            largo_in = 0.0

                        try:
                            cantidad = int(float(cantidad_txt or 0))
                        except Exception:
                            cantidad = 0

                        rows.append({
                            "nombre": nombre,
                            "clasificacion": clasificacion,
                            "largo_in": largo_in,
                            "cantidad": cantidad,
                        })

                    return rows

            except Exception as e:
                ultimo_error = e

        raise RuntimeError(f"No se pudo leer el CSV '{csv_path}'. Error: {ultimo_error}")

    def _cargar_listas_largos_desde_rutas(self):
        """
        Regresa un grupo por cada AutoDXF detectado en el contexto.
        Si el job no tiene CSV, también se agrega para poder mostrarlo explícitamente.
        """
        if not self.rutas_dxf_actuales:
            return []

        grupos = {}
        vistos_autodxf = set()

        for ruta in self.rutas_dxf_actuales:
            ruta_autodxf = self._resolver_autodxf_desde_ruta(ruta)
            if not ruta_autodxf:
                continue

            clave_autodxf = str(ruta_autodxf).lower()
            if clave_autodxf in vistos_autodxf:
                continue
            vistos_autodxf.add(clave_autodxf)

            job = self._resolver_job_desde_autodxf(ruta_autodxf)
            csv_path = self._resolver_csv_lista_largos(ruta_autodxf)

            grupo = {
                "job": job,
                "ruta_autodxf": str(ruta_autodxf),
                "csv_path": str(csv_path) if csv_path else "",
                "rows": [],
                "status": "sin_csv",
                "mensaje": "No se encontró lista de largos para este job.",
            }

            if csv_path:
                try:
                    rows = self._leer_csv_lista_largos(csv_path)
                    grupo["rows"] = rows
                    grupo["status"] = "ok"
                    grupo["mensaje"] = f"CSV encontrado: {csv_path.name}"
                except Exception as e:
                    grupo["status"] = "error_csv"
                    grupo["mensaje"] = f"No se pudo leer el CSV: {e}"
                    print(f"[TAB_PARTS][LISTA_LARGOS][WARN] No se pudo leer '{csv_path}': {e}")

            grupos[clave_autodxf] = grupo

        return sorted(list(grupos.values()), key=lambda g: str(g.get("job", "")).lower())

    def _crear_bloque_job(self, contenedor, grupo, columnas, encabezados, anchos):
        status = grupo.get("status", "sin_csv")
        if status == "ok":
            color_titulo, texto_status, color_status, color_fondo = "#2563EB", "CON LISTA DE LARGOS", "#16A34A", "#F8FAFC"
        elif status == "sin_csv":
            color_titulo, texto_status, color_status, color_fondo = "#DC2626", "SIN LISTA DE LARGOS", "#DC2626", "#FEF2F2"
        else:
            color_titulo, texto_status, color_status, color_fondo = "#D97706", "ERROR AL LEER CSV", "#D97706", "#FFFBEB"

        frame_job = make_herinox_card(shadow=False)
        frame_job.setStyleSheet(
            f"QFrame#HerinoxCard{{background:{color_fondo};border:1px solid {COLOR_BORDE};border-radius:12px;}}"
        )
        fj_lay = QVBoxLayout(frame_job)
        hdr = QHBoxLayout()
        lbl_job = QLabel(f"JOB: {grupo['job']}")
        lbl_job.setStyleSheet(f"font-weight:700;color:{color_titulo};")
        hdr.addWidget(lbl_job)
        hdr.addStretch()
        lbl_st = QLabel(texto_status)
        lbl_st.setStyleSheet(f"font-weight:700;color:{color_status};")
        hdr.addWidget(lbl_st)
        fj_lay.addLayout(hdr)
        if status != "ok":
            msg_lbl = QLabel(grupo.get("mensaje", ""))
            msg_lbl.setStyleSheet(f"color:{color_status};")
            fj_lay.addWidget(msg_lbl)
            contenedor.layout().addWidget(frame_job)
            return
        table = QTableWidget(min(max(len(grupo["rows"]), 3), 10), len(columnas))
        table.setHorizontalHeaderLabels([encabezados[c] for c in columnas])
        for ri, row in enumerate(grupo["rows"]):
            table.setItem(ri, 0, QTableWidgetItem(str(row.get("nombre", ""))))
            table.setItem(ri, 1, QTableWidgetItem(str(row.get("clasificacion", ""))))
            table.setItem(ri, 2, QTableWidgetItem(f"{float(row.get('largo_in', 0) or 0):.3f}"))
            table.setItem(ri, 3, QTableWidgetItem(str(row.get("cantidad", 0))))
        fj_lay.addWidget(table)
        contenedor.layout().addWidget(frame_job)

    def abrir_ventana_lista_largos(self):
        grupos = self._cargar_listas_largos_desde_rutas()
        if not grupos:
            QMessageBox.information(self, "Lista de largos", "No se encontraron rutas AutoDXF válidas en el contexto actual.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Lista de largos")
        dlg.resize(1260, 680)
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        card = make_herinox_card()
        card_lay = QVBoxLayout(card)
        tit_lbl = QLabel("LISTA DE LARGOS")
        tit_lbl.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};font-size:16px;")
        card_lay.addWidget(tit_lbl)
        total_grupos = len(grupos)
        total_ok = sum(1 for g in grupos if g.get("status") == "ok")
        total_sin_csv = sum(1 for g in grupos if g.get("status") == "sin_csv")
        total_error = sum(1 for g in grupos if g.get("status") == "error_csv")
        total_rows = sum(len(g["rows"]) for g in grupos if g.get("status") == "ok")
        card_lay.addWidget(QLabel(
            f"Jobs detectados: {total_grupos}   |   Con lista: {total_ok}   |   "
            f"Sin lista: {total_sin_csv}   |   Error lectura: {total_error}   |   Registros totales: {total_rows}"
        ))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        scroll.setWidget(inner)
        columnas = ("nombre", "clasificacion", "largo_in", "cantidad")
        encabezados = {"nombre": "NOMBRE", "clasificacion": "CLASIFICACIÓN", "largo_in": "LARGO (in)", "cantidad": "CANTIDAD"}
        anchos = {"nombre": 360, "clasificacion": 180, "largo_in": 120, "cantidad": 120}
        for grupo in grupos:
            self._crear_bloque_job(inner, grupo, columnas, encabezados, anchos)
        card_lay.addWidget(scroll, 1)
        lay.addWidget(card)
        self.ventana_lista_largos = dlg
        dlg.exec()
