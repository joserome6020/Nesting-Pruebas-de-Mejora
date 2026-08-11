import os
import shutil
import sys


def _repo_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def app_search_roots():
    """
    Raíces donde buscar recursos nativos / datos (exe, _MEIPASS, repo).
    Orden: carpeta del .exe (sidecars) → bundle PyInstaller → raíz del repo.
    """
    roots: list[str] = []
    if getattr(sys, "frozen", False):
        try:
            roots.append(os.path.dirname(os.path.abspath(sys.executable)))
        except Exception:
            pass
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(str(meipass))
    roots.append(_repo_root())
    # Dedup preservando orden
    out: list[str] = []
    seen: set[str] = set()
    for r in roots:
        key = os.path.normcase(os.path.abspath(r))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def ruta_recurso(ruta_relativa):
    """Para archivos estáticos empaquetados dentro del .exe (imágenes, scripts, macros)"""
    try:
        # PyInstaller guarda los archivos empaquetados en esta ruta temporal
        ruta_base = sys._MEIPASS
    except Exception:
        ruta_base = _repo_root()
    return os.path.join(ruta_base, ruta_relativa)


def ruta_persistente(ruta_relativa):
    """Para archivos que el sistema/usuario modifica y deben guardarse junto al .exe (JSON, Excel)"""
    if getattr(sys, "frozen", False):
        # Si es un .exe compilado, usa la carpeta donde está guardado el ejecutable
        ruta_base = os.path.dirname(sys.executable)
    else:
        # Entorno de desarrollo normal
        ruta_base = _repo_root()
    return os.path.join(ruta_base, ruta_relativa)


def asegurar_archivo_persistente(ruta_relativa: str) -> str:
    """
    Ruta persistente junto al .exe; si no existe, copia la plantilla empaquetada (_MEIPASS).
    Útil para inventario_remanentes.csv, herinox_sync.local.json, etc. en otras PCs.
    """
    destino = ruta_persistente(ruta_relativa)
    if os.path.exists(destino):
        return destino
    try:
        plantilla = ruta_recurso(ruta_relativa)
        if os.path.isfile(plantilla):
            os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
            shutil.copy2(plantilla, destino)
    except Exception:
        pass
    return destino

# --- RUTAS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Archivos locales (Usamos ruta_persistente para no perder datos al cerrar el .exe)
DB_HISTORIAL = ruta_persistente("historial_jobs.json")
TEMP_DIR = ruta_persistente("TEMP_PROCESSED")
INVENTARIO_REMANENTES_CSV = ruta_persistente("inventario_remanentes.csv")


# --- RUTA DEL SERVIDOR (AJUSTAR AQUÍ) ---
RUTA_SERVIDOR_RAIZ = r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals\ARGA METALS CORPORATE SYSTEM"

# --- COLORES Y ESTILO ---
COLOR_BG = "#121212"
COLOR_CARD = "#1E1E1E"
COLOR_ACCENT = "#3A7EBF"
COLOR_SUCCESS = "#2EA043"
COLOR_TEXT = "#E0E0E0"


def setup_theme():
    """Legacy Tk — la app Qt usa interface.qt.theme.apply_theme."""
    pass

# =========================================================
# --- INTEGRACIÓN FREECAD (DXF -> STEP) ---
# =========================================================
AUTO_STEP_AFTER_DXF = True
STEP_SUBFOLDER_NAME = "" # Déjalo vacío ("") si quieres los STEP junto a los DXF.

# OJO: Usamos FreeCAD.exe (programa completo) para que funcione el motor de color
FREECAD_EXE = r"C:\Program Files\FreeCAD 1.0\bin\FreeCAD.exe"

# Apuntamos al nuevo script robusto (Usamos ruta_recurso porque se empaquetará oculto)
FREECAD_MACRO = ruta_recurso("generador_verde.FCMacro")

# Valores por defecto para la conversión
FREECAD_THK_MM = 6.35 
FREECAD_SCALE = 1.0

# Overlay encima del flujo robot Cama A/B:
# True  → TODAS las carpetas DXF de acero generan 1 STEP (sin A/B, coords 1:1).
#         Cobre no aplica (sigue su lógica).
# False → flujo normal (solo robot láser/plasma con Cama A + Cama B).
# Override: STEP_UNIVERSAL_SIN_CAMAS=0|1 en el entorno.
_STEP_UNIVERSAL_ENV = os.getenv("STEP_UNIVERSAL_SIN_CAMAS", "").strip().lower()
if _STEP_UNIVERSAL_ENV in {"0", "false", "no", "off"}:
    STEP_UNIVERSAL_SIN_CAMAS = False
elif _STEP_UNIVERSAL_ENV in {"1", "true", "yes", "si", "on"}:
    STEP_UNIVERSAL_SIN_CAMAS = True
else:
    STEP_UNIVERSAL_SIN_CAMAS = True  # activo hasta que volvamos al flujo A/B

# =========================================================
# --- INTEGRACION REACT-HERINOX (SYNC DE PLACAS) ---
# =========================================================
# Archivo local persistente para guardar configuracion del sync (incluye credenciales).
HERINOX_SYNC_SETTINGS_FILE = ruta_persistente("herinox_sync.local.json")

# Activa/desactiva sincronizacion al iniciar la app.
HERINOX_SYNC_ENABLED = os.getenv("HERINOX_SYNC_ENABLED", "1").strip().lower() in {"1", "true", "yes", "si"}

# URL base del backend de react-Herinox.
HERINOX_API_BASE_URL = os.getenv("HERINOX_API_BASE_URL", "http://192.168.2.80:4000").strip()

# Credenciales para /api/auth/login.
# Recomendado: definirlas como variables de entorno del sistema.
HERINOX_SYNC_EMAIL = os.getenv("HERINOX_SYNC_EMAIL", "").strip()
HERINOX_SYNC_PASSWORD = os.getenv("HERINOX_SYNC_PASSWORD", "").strip()

# Timeout de llamadas HTTP.
HERINOX_SYNC_TIMEOUT_SECONDS = int(os.getenv("HERINOX_SYNC_TIMEOUT_SECONDS", "8"))

# Fallback directo a PostgreSQL Herinox (si API no responde).
HERINOX_DB_HOST = os.getenv("HERINOX_DB_HOST", "192.168.2.80").strip()
HERINOX_DB_PORT = int(os.getenv("HERINOX_DB_PORT", "5439"))
HERINOX_DB_NAME = os.getenv("HERINOX_DB_NAME", "herinox").strip()
HERINOX_DB_USER = os.getenv("HERINOX_DB_USER", "herinox").strip()
HERINOX_DB_PASSWORD = os.getenv("HERINOX_DB_PASSWORD", "herinox_password_2024").strip()
HERINOX_DB_CONNECT_TIMEOUT = int(os.getenv("HERINOX_DB_CONNECT_TIMEOUT", "5"))

# Respaldo local de catálogo Herinox (precios / dimensiones).
HERINOX_CACHE_DIR = ruta_persistente(os.path.join("cache", ""))
HERINOX_LARGOS_CACHE_FILE = os.path.join(HERINOX_CACHE_DIR, "herinox_catalog_largos.json")
HERINOX_PLATES_CACHE_FILE = os.path.join(HERINOX_CACHE_DIR, "herinox_plates_snapshot.json")
HERINOX_PLATES_XLSX_FILE = ruta_persistente("Plates.xlsx")