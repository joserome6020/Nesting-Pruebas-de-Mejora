import os
import re
import glob
import time
import ctypes
from datetime import datetime
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
)

import psycopg2
from psycopg2.extras import RealDictCursor
from freecad_runner import ejecutar_macro_freecad

try:
    from modules.cobre_step_fuentes import buscar_manifest_en_nesting
except ImportError:
    buscar_manifest_en_nesting = None  # type: ignore

DB_CONFIG = {
    "host": "localhost",
    "database": "nestingpro_db",
    "user": "postgres",
    "password": "nesting123",
    "port": "5433"
}

DEBUG_DIR = r"C:\NEST_EXPORTS"
DEBUG_LOG = os.path.join(DEBUG_DIR, "despachador_debug.txt")
SERVER_UNC_PREFIX = r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
FREECAD_SHORT_UNC_PREFIX = r"\\192.168.2.80\Grupo Arga Metals"

AUTO_TIMEOUT_MS = 5 * 60 * 1000  # 5 minutos


def obtener_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
    return app


class DialogoModoOperacion(QDialog):
    def __init__(self, timeout_ms=AUTO_TIMEOUT_MS):
        super().__init__()
        self.modo = None
        self.segundos_restantes = timeout_ms // 1000

        self.setWindowTitle("Seleccionar modo de operación")
        self.setFixedSize(520, 250)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowModality(Qt.ApplicationModal)

        self.setStyleSheet("""
            QDialog {
                background-color: #f4f6f8;
            }
            QLabel#titulo {
                font-size: 18px;
                font-weight: 600;
                color: #1f2937;
            }
            QLabel#descripcion {
                font-size: 13px;
                color: #4b5563;
            }
            QLabel#temporizador {
                font-size: 12px;
                color: #b45309;
                font-weight: 600;
            }
            QPushButton {
                min-height: 58px;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 8px 14px;
            }
            QPushButton#autoBtn {
                background-color: #2563eb;
                color: white;
                border: none;
            }
            QPushButton#autoBtn:hover {
                background-color: #1d4ed8;
            }
            QPushButton#manualBtn {
                background-color: white;
                color: #111827;
                border: 1px solid #d1d5db;
            }
            QPushButton#manualBtn:hover {
                background-color: #f9fafb;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        lbl_titulo = QLabel("Selecciona el modo de operación")
        lbl_titulo.setObjectName("titulo")
        lbl_titulo.setAlignment(Qt.AlignCenter)

        lbl_desc = QLabel(
            "Si no seleccionas una opción en 5 minutos, se activará el modo automático."
        )
        lbl_desc.setObjectName("descripcion")
        lbl_desc.setWordWrap(True)
        lbl_desc.setAlignment(Qt.AlignCenter)

        self.lbl_timer = QLabel("")
        self.lbl_timer.setObjectName("temporizador")
        self.lbl_timer.setAlignment(Qt.AlignCenter)
        self._actualizar_texto_timer()

        btn_auto = QPushButton("Modo automático\n(Leer pendientes desde la BD)")
        btn_auto.setObjectName("autoBtn")
        btn_auto.clicked.connect(self.seleccionar_automatico)

        btn_manual = QPushButton("Modo manual\n(Seleccionar carpeta NESTING)")
        btn_manual.setObjectName("manualBtn")
        btn_manual.clicked.connect(self.seleccionar_manual)

        botones = QHBoxLayout()
        botones.setSpacing(12)
        botones.addWidget(btn_auto)
        botones.addWidget(btn_manual)

        layout.addWidget(lbl_titulo)
        layout.addWidget(lbl_desc)
        layout.addWidget(self.lbl_timer)
        layout.addSpacing(6)
        layout.addLayout(botones)

        self.timer_conteo = QTimer(self)
        self.timer_conteo.timeout.connect(self._tick)
        self.timer_conteo.start(1000)

        self.timer_auto = QTimer(self)
        self.timer_auto.setSingleShot(True)
        self.timer_auto.timeout.connect(self.auto_por_timeout)
        self.timer_auto.start(timeout_ms)

    def _actualizar_texto_timer(self):
        minutos, segundos = divmod(max(self.segundos_restantes, 0), 60)
        self.lbl_timer.setText(
            f"Activación automática en: {minutos:02d}:{segundos:02d}"
        )

    def _tick(self):
        self.segundos_restantes -= 1
        self._actualizar_texto_timer()

    def _detener_timers(self):
        if self.timer_conteo.isActive():
            self.timer_conteo.stop()
        if self.timer_auto.isActive():
            self.timer_auto.stop()

    def seleccionar_automatico(self):
        self.modo = "AUTO"
        self._detener_timers()
        self.accept()

    def seleccionar_manual(self):
        self.modo = "MANUAL"
        self._detener_timers()
        self.accept()

    def auto_por_timeout(self):
        self.modo = "AUTO"
        self._detener_timers()
        self.accept()

    def reject(self):
        if self.modo is None:
            self.modo = "CANCELADO"
        self._detener_timers()
        super().reject()

def ruta_para_freecad(p: str) -> str:
    return norm_path(p)

def norm_path(p: str) -> str:
    if not p:
        return p
    return os.path.normpath(p)


def dbg(msg: str):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    linea = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(linea)
    except UnicodeEncodeError:
        try:
            print(linea.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def contar_archivos(carpeta: str, patron: str) -> int:
    carpeta = norm_path(carpeta)
    if not carpeta or not os.path.isdir(carpeta):
        return 0
    return len(glob.glob(os.path.join(carpeta, patron)))


def listar_dxf_y_step_esperado(dxf_dir: str, step_dir: str):
    pares = []
    dxf_dir = norm_path(dxf_dir)
    step_dir = norm_path(step_dir)

    if not dxf_dir or not os.path.isdir(dxf_dir):
        return pares

    dxf_files = sorted(glob.glob(os.path.join(dxf_dir, "*.dxf")))
    for dxf_path in dxf_files:
        base = os.path.splitext(os.path.basename(dxf_path))[0]
        step_path = os.path.join(step_dir, f"{base}.step")
        pares.append((norm_path(dxf_path), norm_path(step_path)))
    return pares


def snapshot_steps(step_dir: str):
    data = {}
    step_dir = norm_path(step_dir)

    if not step_dir or not os.path.isdir(step_dir):
        return data

    for path in glob.glob(os.path.join(step_dir, "*.step")):
        try:
            data[norm_path(path)] = os.path.getmtime(path)
        except Exception:
            pass
    return data


def diff_steps(before: dict, after: dict):
    nuevos = []
    actualizados = []

    for path, mtime in after.items():
        if path not in before:
            nuevos.append(path)
        elif before[path] != mtime:
            actualizados.append(path)

    return sorted(nuevos), sorted(actualizados)


def _step_universal_sin_camas() -> bool:
    """Paridad con config/exporter: 1 STEP por DXF, coords 1:1, sin Cama A/B."""
    try:
        import config as _cfg

        return bool(getattr(_cfg, "STEP_UNIVERSAL_SIN_CAMAS", True))
    except Exception:
        return True


def resolver_destinos_step(step_root: str):
    """
    Destinos STEP para acero.

    Modo actual (STEP_UNIVERSAL_SIN_CAMAS=True, default):
      - STEP/  (plano) → origen NONE, offsets 0 (coords DXF 1:1)
    Legacy (flag off):
      - STEP/Cama A → ancla TR + offset robot
      - STEP/Cama B → ancla BR + offset robot
    """
    step_root = norm_path(step_root)
    if not step_root:
        return []

    os.makedirs(step_root, exist_ok=True)

    if _step_universal_sin_camas():
        return [
            {
                "tag": "UNIVERSAL",
                "dir": step_root,
                "origen": "NONE",
                "off_x": 0.0,
                "off_y": 0.0,
                "off_z": 0.0,
                "prefer_verde": True,
            }
        ]

    cama_a = norm_path(os.path.join(step_root, "Cama A"))
    cama_b = norm_path(os.path.join(step_root, "Cama B"))

    os.makedirs(cama_a, exist_ok=True)
    os.makedirs(cama_b, exist_ok=True)

    return [
        {
            "tag": "A",
            "dir": cama_a,
            "origen": "TR",
            "off_x": 4235,
            "off_y": -1015,
            "off_z": -700,
            "prefer_verde": True,
        },
        {
            "tag": "B",
            "dir": cama_b,
            "origen": "BR",
            "off_x": 4235,
            "off_y": 840,
            "off_z": -700,
            "prefer_verde": True,
        },
    ]


def clasificar_familia(nombre_carpeta: str):
    nombre = (nombre_carpeta or "").upper()

    # Cobre largos: solo NESTEOS DE COBRE genera STEP (sin Cama A/B).
    if "NESTEOS DE COBRE" in nombre:
        return "COBRE"

    universal = _step_universal_sin_camas()

    # Overlay universal: CAMA LASER también genera 1 STEP (coords 1:1).
    # Legacy: CAMA LASER solo DXF de corte.
    if "CAMA LASER" in nombre:
        return "CAMA_LASER" if universal else None

    if "ROBOT" not in nombre:
        return None

    if "PLASMA" in nombre:
        return "PLASMA"

    if "LASER" in nombre:
        return "LASER"

    return None


def resolver_destinos_step_cobre(step_root: str):
    """NESTEOS DE COBRE: un solo STEP por hoja (sin Cama A/B)."""
    step_root = norm_path(step_root)
    if not step_root:
        return []

    os.makedirs(step_root, exist_ok=True)
    return [
        {
            "tag": "COBRE",
            "dir": step_root,
            "origen": "TR",
            "off_x": 0.0,
            "off_y": 0.0,
            "off_z": 0.0,
            "prefer_verde": False,
        }
    ]


def descubrir_familias(ruta_nesting: str):
    """
    Detecta carpetas con conversión STEP dentro de NESTING.
    Universal: NESTEOS DE COBRE, CAMA LASER, ROBOT LASER, ROBOT PLASMA.
    Legacy: NESTEOS DE COBRE, ROBOT LASER, ROBOT PLASMA (sin CAMA LASER).
    """
    ruta_nesting = norm_path(ruta_nesting)
    familias = []

    if not os.path.isdir(ruta_nesting):
        return familias

    for nombre in sorted(os.listdir(ruta_nesting)):
        ruta_familia = norm_path(os.path.join(ruta_nesting, nombre))
        if not os.path.isdir(ruta_familia):
            continue

        tipo = clasificar_familia(nombre)
        if tipo is None:
            continue

        dxf_dir = norm_path(os.path.join(ruta_familia, "DXF"))
        step_root = norm_path(os.path.join(ruta_familia, "STEP"))
        if tipo == "COBRE":
            destinos = resolver_destinos_step_cobre(step_root)
        else:
            destinos = resolver_destinos_step(step_root)

        familias.append({
            "nombre": nombre,
            "tipo": tipo,
            "ruta_base": ruta_familia,
            "dxf_dir": dxf_dir,
            "step_root": step_root,
            "destinos_step": destinos,
        })

    return familias


def inferir_espesor_desde_dxf(familias):
    """
    Intenta inferir el espesor desde nombres como:
    NESTING_0.25_CARBONO_HOJA_01.dxf
    SWO-027_0.1875_SWO-027-H1.dxf
    """
    patrones = (
        re.compile(r"NESTING_([0-9]+(?:\.[0-9]+)?)_", re.IGNORECASE),
        re.compile(r"SWO[-\s]*\d+[_\s-]+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    )

    for fam in familias:
        dxf_dir = norm_path(fam["dxf_dir"])
        for archivo in glob.glob(os.path.join(dxf_dir, "*.dxf")):
            nombre = os.path.basename(archivo)
            for patron in patrones:
                m = patron.search(nombre)
                if not m:
                    continue
                try:
                    valor = float(m.group(1))
                    return valor, m.group(1)
                except Exception:
                    pass

    return None, None


def pedir_ruta_nesting():
    """
    Selección gráfica Qt de carpeta NESTING.
    Si se cancela, se considera cancelación explícita del modo manual.
    """
    try:
        obtener_qapp()

        dialog = QFileDialog()
        dialog.setWindowTitle("Selecciona la carpeta NESTING")
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        if dialog.exec():
            rutas = dialog.selectedFiles()
            if rutas:
                return norm_path(rutas[0])

        return ""
    except Exception as e:
        dbg(f"⚠️ No se pudo abrir el selector Qt de carpeta: {e}")
        return ""


def elegir_modo_operacion():
    """
    Ventana Qt con 3 posibles salidas:
    - AUTO: botón automático o timeout de 5 min
    - MANUAL: botón manual
    - CANCELADO: cerrar con X o Esc
    """
    obtener_qapp()
    dlg = DialogoModoOperacion(timeout_ms=AUTO_TIMEOUT_MS)
    dlg.exec()
    return dlg.modo or "CANCELADO"


def _usar_occt_para_crear_steps(motor: str | None = None) -> bool:
    """
    Crear STEPs / despachador: motor OCCT por defecto.

    Los DXF de nest ANS son LINE/ARC/CIRCLE 1:1 (exactitud plasma/láser). FreeCAD
    a menudo no une esos bordes en wires cerrados → SKIP OUTER:0. OCCT hace el
    join en memoria al convertir a STEP; no reescribe ni altera el DXF en disco.

    ``motor`` (UI Crear STEPs / export) gana sobre el env.
    Override legacy sin UI: ARGA_CREAR_STEPS_MOTOR=freecad
    """
    if motor is None or not str(motor).strip():
        motor = (os.environ.get("ARGA_CREAR_STEPS_MOTOR") or "occt").strip().lower()
    else:
        motor = str(motor).strip().lower()
    return motor not in ("freecad", "fc", "verde", "free-cad")


def _procesar_familia_occt(familia: dict, thk_mm: float, plasma_off_mm: float):
    """
    DXF → STEP con OCCT (join de LINE/ARC en memoria). Misma carpeta STEP.
    No modifica los DXF. El offset plasma del nest ya viene en el DXF exportado;
    no se reaplica aquí (evita doble kerf).
    """
    nombre = familia["nombre"]
    tipo = familia["tipo"]
    dxf_dir = norm_path(familia["dxf_dir"])
    destinos_step = familia["destinos_step"]
    resultados = []

    dxf_count = contar_archivos(dxf_dir, "*.dxf")
    dbg(f"[{nombre}] tipo={tipo} | DXF detectados={dxf_count} | motor=OCCT")
    if dxf_count == 0:
        dbg(f"[{nombre}] ℹ️ No hay DXF para procesar.")
        return resultados
    if not destinos_step:
        dbg(f"[{nombre}] ❌ No hay destinos STEP definidos.")
        return resultados

    if float(plasma_off_mm or 0.0) > 1e-9 and str(tipo).upper() == "PLASMA":
        dbg(
            f"[{nombre}] ℹ️ Offset plasma nest={plasma_off_mm} mm ya embebido en DXF; "
            "OCCT no lo vuelve a aplicar."
        )

    try:
        from modules.nesting_engine.occt_step_export import _ensure_cad_engine
    except Exception as exc:
        dbg(f"[{nombre}] ❌ No se pudo cargar motor OCCT: {exc}")
        return [(f"{nombre}_OCCT", False)]

    export_fn, _robot_fn, thk_from_name = _ensure_cad_engine()
    material = "CU" if str(tipo).upper() == "COBRE" else "STEEL"

    for dest in destinos_step:
        tag = dest["tag"]
        step_dir = norm_path(dest["dir"])
        origen = dest.get("origen") or "NONE"
        off_x = float(dest.get("off_x") or 0.0)
        off_y = float(dest.get("off_y") or 0.0)
        off_z = float(dest.get("off_z") or 0.0)
        os.makedirs(step_dir, exist_ok=True)

        before_snapshot = snapshot_steps(step_dir)
        pares = listar_dxf_y_step_esperado(dxf_dir, step_dir)
        dbg(
            f"[{nombre}] OCCT destino {tag} | DXF={dxf_dir} | STEP={step_dir} | "
            f"origen={origen} | offset=({off_x}, {off_y}, {off_z}) | pares={len(pares)}"
        )

        ok_all = True
        for dxf_path, step_path in pares:
            nombre_dxf = os.path.basename(dxf_path)
            thk = float(thk_from_name(nombre_dxf, default_mm=float(thk_mm)))
            dbg(
                f"[{nombre}] [{tag}] OCCT {nombre_dxf} -> {os.path.basename(step_path)} "
                f"(thk={thk:.4f} mm)"
            )
            try:
                export_fn(
                    dxf_path,
                    step_path,
                    thk_mm=thk,
                    material=material,
                    off_x=off_x,
                    off_y=off_y,
                    off_z=off_z,
                    origen=None if str(origen).upper() in ("NONE", "", "NULL") else origen,
                    # ENGRAVE por lotes (chunk 100): estable con 1k–3k MARK.
                    # PIECE_ONESHOT / ONESHOT se cuelga o tarda horas en nests densos.
                    mark_mode="ENGRAVE",
                    include_plate=False,
                )
                if not os.path.isfile(step_path) or os.path.getsize(step_path) < 64:
                    raise RuntimeError(f"STEP vacio o ausente: {step_path}")
                try:
                    dbg(f"[{nombre}] [{tag}] OK -> {step_path}")
                except Exception:
                    pass
            except Exception as exc:
                # Si el STEP ya quedó escrito, no tumbar por fallo de log/consola.
                if os.path.isfile(step_path) and os.path.getsize(step_path) >= 64:
                    try:
                        dbg(f"[{nombre}] [{tag}] OK (STEP escrito; aviso: {exc})")
                    except Exception:
                        pass
                else:
                    ok_all = False
                    try:
                        dbg(f"[{nombre}] [{tag}] ERR {nombre_dxf}: {exc}")
                    except Exception:
                        pass

        after_snapshot = snapshot_steps(step_dir)
        nuevos, actualizados = diff_steps(before_snapshot, after_snapshot)
        dbg(
            f"[{nombre}] Resultado OCCT {tag} => ok={ok_all} | "
            f"nuevos={len(nuevos)} | actualizados={len(actualizados)}"
        )
        for path in nuevos:
            dbg(f"[{nombre}] [{tag}] STEP NUEVO -> {path}")
        for path in actualizados:
            dbg(f"[{nombre}] [{tag}] STEP ACTUALIZADO -> {path}")
        if not nuevos and not actualizados:
            dbg(f"[{nombre}] [{tag}] ⚠️ No se detectaron STEP nuevos/actualizados en: {step_dir}")
        resultados.append((f"{nombre}_{tag}", ok_all))

    return resultados


def procesar_familia(
    familia: dict,
    thk_mm: float,
    plasma_off_mm: float,
    *,
    motor_3d: str | None = None,
):
    """
    DXF → STEP para una familia (Cama Laser / Robot / Cobre / Plasma).

    Por defecto usa OCCT (une LINE/ARC en memoria; no altera DXF en disco).
    FreeCAD: ``motor_3d='freecad'`` o env ``ARGA_CREAR_STEPS_MOTOR=freecad``.
    """
    if _usar_occt_para_crear_steps(motor_3d):
        return _procesar_familia_occt(familia, thk_mm, plasma_off_mm)

    nombre = familia["nombre"]
    tipo = familia["tipo"]
    dxf_dir = norm_path(familia["dxf_dir"])
    destinos_step = familia["destinos_step"]

    resultados = []

    dxf_count = contar_archivos(dxf_dir, "*.dxf")
    dbg(f"[{nombre}] tipo={tipo} | DXF detectados={dxf_count} | motor=FreeCAD")

    if dxf_count == 0:
        dbg(f"[{nombre}] ℹ️ No hay DXF para procesar.")
        return resultados

    if not destinos_step:
        dbg(f"[{nombre}] ❌ No hay destinos STEP definidos.")
        return resultados

    offset_mm = plasma_off_mm if tipo == "PLASMA" else 0.0
    os.environ["FREECAD_PLASMA_OFFSET"] = str(offset_mm)

    for dest in destinos_step:
        tag = dest["tag"]
        step_dir = norm_path(dest["dir"])
        origen = dest["origen"]
        off_x = dest["off_x"]
        off_y = dest["off_y"]
        off_z = dest["off_z"]
        prefer_verde = bool(dest.get("prefer_verde", False))

        # Ruta real del servidor (la que tú quieres conservar)
        dxf_dir_server = dxf_dir
        step_dir_server = step_dir

        # Ruta acortada solo para FreeCAD
        dxf_dir_fc = ruta_para_freecad(dxf_dir_server)
        step_dir_fc = ruta_para_freecad(step_dir_server)

        pre_steps = contar_archivos(step_dir_server, "*.step")
        before_snapshot = snapshot_steps(step_dir_server)

        dbg(
            f"[{nombre}] Ejecutando destino {tag} | "
            f"DXF servidor={dxf_dir_server} | "
            f"STEP servidor={step_dir_server} | "
            f"DXF FreeCAD={dxf_dir_fc} | "
            f"STEP FreeCAD={step_dir_fc} | "
            f"origen={origen} | offset=({off_x}, {off_y}, {off_z}) | "
            f"prefer_verde={prefer_verde} | "
            f"steps_antes={pre_steps} | plasma_offset={offset_mm}"
        )

        pares = listar_dxf_y_step_esperado(dxf_dir_server, step_dir_server)
        dbg(f"[{nombre}] [{tag}] Archivos DXF a procesar: {len(pares)}")

        for dxf_path, step_path in pares:
            dbg(f"[{nombre}] [{tag}] DXF -> {dxf_path}")
            dbg(f"[{nombre}] [{tag}] STEP esperado -> {step_path}")

        material_fc = "CU" if str(tipo).upper() == "COBRE" else "STEEL"
        ok = ejecutar_macro_freecad(
            dxf_dir_fc,
            step_dir_fc,
            thk_mm,
            origen,
            off_x,
            off_y,
            off_z,
            prefer_verde=prefer_verde,
            material=material_fc,
        )

        post_steps = contar_archivos(step_dir, "*.step")
        after_snapshot = snapshot_steps(step_dir)
        nuevos, actualizados = diff_steps(before_snapshot, after_snapshot)

        dbg(
            f"[{nombre}] Resultado {tag} => ok={ok} | "
            f"steps_despues={post_steps}"
        )

        if nuevos:
            dbg(f"[{nombre}] [{tag}] STEP nuevos detectados: {len(nuevos)}")
            for path in nuevos:
                dbg(f"[{nombre}] [{tag}] STEP NUEVO -> {path}")

        if actualizados:
            dbg(f"[{nombre}] [{tag}] STEP actualizados detectados: {len(actualizados)}")
            for path in actualizados:
                dbg(f"[{nombre}] [{tag}] STEP ACTUALIZADO -> {path}")

        if not nuevos and not actualizados:
            dbg(f"[{nombre}] [{tag}] ⚠️ No se detectaron STEP nuevos ni actualizados en: {step_dir}")

        resultados.append((f"{nombre}_{tag}", ok))

    return resultados


def formatear_tiempo(segundos: float) -> str:
    segundos = int(round(segundos))
    horas = segundos // 3600
    minutos = (segundos % 3600) // 60
    segs = segundos % 60
    return f"{horas:02d}:{minutos:02d}:{segs:02d}"


def mostrar_msgbox_resumen(texto: str, titulo: str = "Proceso 3D finalizado"):
    try:
        ctypes.windll.user32.MessageBoxW(0, texto, titulo, 0x40)
    except Exception as e:
        dbg(f"⚠️ No se pudo mostrar el msgbox final: {e}")


def procesar_ruta_nesting(
    ruta_nesting: str,
    *,
    calibre_str: str | None = None,
    actualizar_bd: bool = False,
    ruta_bd: str | None = None,
    cursor=None,
    conexion=None,
    motor_3d: str | None = None,
):
    """
    Procesa una sola carpeta NESTING.
    Puede venir:
    - desde la BD (modo normal)
    - o directo desde selección manual (modo manual)

    ``motor_3d``: 'occt' | 'freecad' | None (env / default OCCT).
    """
    ruta_nesting = norm_path(ruta_nesting)

    dbg("------------------------------------------------------")
    dbg(f"📂 Procesando NESTING: {ruta_nesting}")
    motor_eff = (motor_3d or os.environ.get("ARGA_CREAR_STEPS_MOTOR") or "occt").strip().lower()
    dbg(f"🔧 Motor 3D: {motor_eff}")

    if not os.path.isdir(ruta_nesting):
        dbg(f"❌ La ruta NESTING no existe o no es accesible: {ruta_nesting}")
        return {
            "ok": False,
            "familias_detectadas": 0,
            "familias_con_dxf": 0,
            "total_step_final": 0,
        }

    familias = descubrir_familias(ruta_nesting)
    dbg(f"Familias candidatas detectadas dentro de NESTING: {len(familias)}")

    for fam in familias:
        dbg(
            f"FAMILIA => nombre={fam['nombre']} | tipo={fam['tipo']} | "
            f"DXF={fam['dxf_dir']} | STEP={fam['step_root']}"
        )
        dbg(f"DESTINOS STEP => {fam['destinos_step']}")

    if not familias:
        dbg("❌ No se detectó ninguna subcarpeta candidata (NESTEOS DE COBRE / CAMA LASER / ROBOT LASER / ROBOT PLASMA) dentro de NESTING.")
        return {
            "ok": False,
            "familias_detectadas": 0,
            "familias_con_dxf": 0,
            "total_step_final": 0,
        }

    if calibre_str:
        dbg(f"📦 Calibre recibido: {calibre_str}")
        espesor_in = 0.25
        try:
            espesor_in = float(str(calibre_str).split("_")[0])
        except Exception:
            dbg(f"⚠️ No se pudo interpretar el calibre '{calibre_str}', usando 0.25 in por default.")
    else:
        espesor_in, espesor_txt = inferir_espesor_desde_dxf(familias)
        if espesor_in is None and buscar_manifest_en_nesting is not None:
            manifest_thk = buscar_manifest_en_nesting(ruta_nesting)
            if manifest_thk and manifest_thk.get("espesor_in") is not None:
                try:
                    espesor_in = float(manifest_thk["espesor_in"])
                    espesor_txt = str(espesor_in)
                    dbg(f"📦 Espesor desde manifiesto cobre: {espesor_txt}\"")
                except Exception:
                    espesor_in = None
        if espesor_in is None:
            dbg("⚠️ No se pudo inferir el espesor desde los DXF. Usando 0.25 in por default.")
            espesor_in = 0.25
        else:
            dbg(f"📦 Espesor inferido desde nombre DXF: {espesor_txt}\"")

    thk_mm = espesor_in * 25.4
    from modules.plasma_compensator import compute_plasma_offset_mm

    plasma_off = float(compute_plasma_offset_mm(float(espesor_in)))

    dbg(f"📐 Espesor detectado: {espesor_in}\" ({thk_mm} mm)")
    dbg(f"🔥 Offset plasma calculado: {plasma_off} mm")

    resultados = []
    familias_con_dxf = 0

    for fam in familias:
        dxf_count = contar_archivos(fam["dxf_dir"], "*.dxf")
        if dxf_count > 0:
            familias_con_dxf += 1
        resultados.extend(procesar_familia(fam, thk_mm, plasma_off, motor_3d=motor_3d))

    if buscar_manifest_en_nesting is not None:
        manifest = buscar_manifest_en_nesting(ruta_nesting)
    else:
        manifest = None
    if manifest:
        dbg(
            f"📋 Manifiesto cobre: {manifest.get('_manifest_path', 'cobre_dxf_fuentes.json')} | "
            f"fuentes={len(manifest.get('fuentes') or [])}"
        )
        from modules.cobre_step_fuentes import procesar_steps_cobre_en_ubicacion_fuentes

        fuente_res = procesar_steps_cobre_en_ubicacion_fuentes(
            manifest,
            thk_mm=thk_mm,
            log_fn=dbg,
        )
        for carpeta, ok, _ in fuente_res:
            resultados.append((f"COBRE_FUENTE_{os.path.basename(carpeta)}", ok))
    else:
        dbg("ℹ️ Sin manifiesto cobre_dxf_fuentes.json (solo STEP de barras nesteadas).")

    if not resultados:
        dbg("❌ No hubo ninguna conversión intentada.")
        return {
            "ok": False,
            "familias_detectadas": len(familias),
            "familias_con_dxf": familias_con_dxf,
            "total_step_final": 0,
        }

    fallo = any(not ok for _, ok in resultados)

    total_step_final = 0
    for fam in familias:
        for dest in fam["destinos_step"]:
            total_step_final += contar_archivos(dest["dir"], "*.step")

    dbg(f"Total STEP encontrados al final: {total_step_final}")

    if fallo:
        dbg("❌ Al menos una llamada a FreeCAD devolvió False.")
        return {
            "ok": False,
            "familias_detectadas": len(familias),
            "familias_con_dxf": familias_con_dxf,
            "total_step_final": total_step_final,
        }

    if total_step_final == 0:
        dbg("❌ No se detectó ningún archivo STEP en salidas.")
        return {
            "ok": False,
            "familias_detectadas": len(familias),
            "familias_con_dxf": familias_con_dxf,
            "total_step_final": total_step_final,
        }

    if actualizar_bd and ruta_bd and cursor and conexion:
        cursor.execute("""
            UPDATE reporte_cortes
            SET estado_3d = 'COMPLETADO'
            WHERE ruta_exportacion = %s AND estado_3d = 'PENDIENTE'
        """, (ruta_bd,))
        conexion.commit()
        dbg("✅ Trabajo completado y sellado en BD.")
    else:
        dbg("✅ Trabajo completado en modo manual (sin tocar BD).")

    return {
        "ok": True,
        "familias_detectadas": len(familias),
        "familias_con_dxf": familias_con_dxf,
        "total_step_final": total_step_final,
    }


def procesar_pendientes_3d():
    inicio_total = time.perf_counter()

    rutas_detectadas = 0
    rutas_completadas = 0
    rutas_con_error = 0
    familias_detectadas_total = 0
    familias_con_dxf_total = 0
    cancelado_por_usuario = False

    dbg("======================================================")
    dbg("🌙 INICIANDO TURNO NOCTURNO: Escaneo de Modelos 3D")
    dbg("======================================================")

    opcion = elegir_modo_operacion()
    dbg(f"Modo seleccionado: {opcion}")

    conexion = None
    cursor = None

    try:
        if opcion == "CANCELADO":
            cancelado_por_usuario = True
            dbg("⛔ Proceso cancelado por el usuario desde la ventana de selección.")
            return

        if opcion == "AUTO":
            conexion = psycopg2.connect(**DB_CONFIG)
            cursor = conexion.cursor(cursor_factory=RealDictCursor)

            cursor.execute("""
                SELECT ruta_exportacion, MAX(calibre) AS calibre
                FROM reporte_cortes
                WHERE estado_3d = 'PENDIENTE' AND ruta_exportacion IS NOT NULL
                GROUP BY ruta_exportacion;
            """)
            trabajos_pendientes = cursor.fetchall()

            rutas_detectadas = len(trabajos_pendientes)
            dbg(f"Trabajos pendientes detectados: {rutas_detectadas}")

            if not trabajos_pendientes:
                dbg("💤 No hay trabajos pendientes. El sistema volverá a dormir.")
            else:
                for trabajo in trabajos_pendientes:
                    ruta_bd = norm_path(trabajo["ruta_exportacion"])
                    calibre_str = trabajo["calibre"] or "0.25_CARBONO"
                    ruta_nesting = norm_path(os.path.join(ruta_bd, "NESTING"))

                    dbg("------------------------------------------------------")
                    dbg(f"🚀 Procesando ruta_exportacion: {ruta_bd}")

                    res = procesar_ruta_nesting(
                        ruta_nesting,
                        calibre_str=calibre_str,
                        actualizar_bd=True,
                        ruta_bd=ruta_bd,
                        cursor=cursor,
                        conexion=conexion,
                    )

                    familias_detectadas_total += res["familias_detectadas"]
                    familias_con_dxf_total += res["familias_con_dxf"]

                    if res["ok"]:
                        rutas_completadas += 1
                    else:
                        rutas_con_error += 1

        else:
            ruta_nesting = pedir_ruta_nesting()
            ruta_nesting = norm_path(ruta_nesting.strip().strip('"')) if ruta_nesting else ""

            if not ruta_nesting:
                cancelado_por_usuario = True
                dbg("⛔ Modo manual cancelado: no se seleccionó ninguna carpeta NESTING.")
                return

            rutas_detectadas = 1
            dbg(f"Ruta NESTING seleccionada manualmente: {ruta_nesting}")

            res = procesar_ruta_nesting(
                ruta_nesting,
                calibre_str=None,
                actualizar_bd=False,
                ruta_bd=None,
                cursor=None,
                conexion=None,
            )

            familias_detectadas_total += res["familias_detectadas"]
            familias_con_dxf_total += res["familias_con_dxf"]

            if res["ok"]:
                rutas_completadas += 1
            else:
                rutas_con_error += 1

    except Exception as e:
        dbg(f"❌ ERROR FATAL en el Despachador: {e}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

        tiempo_total = time.perf_counter() - inicio_total
        tiempo_fmt = formatear_tiempo(tiempo_total)

        dbg("🏁 Turno Nocturno Finalizado.")
        dbg(
            f"RESUMEN => pendientes={rutas_detectadas} | "
            f"completadas={rutas_completadas} | "
            f"con_error={rutas_con_error} | "
            f"familias_detectadas={familias_detectadas_total} | "
            f"familias_con_dxf={familias_con_dxf_total} | "
            f"tiempo_total={tiempo_fmt}"
        )

        if cancelado_por_usuario:
            resumen = (
                "Proceso 3D cancelado\n\n"
                "No se ejecutó ningún procesamiento.\n"
                f"Tiempo transcurrido: {tiempo_fmt}"
            )
            mostrar_msgbox_resumen(resumen, "Proceso 3D cancelado")
        else:
            resumen = (
                "Proceso 3D finalizado\n\n"
                f"Trabajos/rutas detectadas: {rutas_detectadas}\n"
                f"Trabajos completados: {rutas_completadas}\n"
                f"Trabajos con error o pendientes: {rutas_con_error}\n"
                f"Familias detectadas: {familias_detectadas_total}\n"
                f"Familias con DXF: {familias_con_dxf_total}\n"
                f"Tiempo total: {tiempo_fmt}"
            )
            mostrar_msgbox_resumen(resumen)


if __name__ == "__main__":
    procesar_pendientes_3d()