import os
import shutil
import sys


APP_NAME = "ArgaNestingSuite"


def _repo_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _exe_dir() -> str:
    """Carpeta del .exe (o del intérprete en dev)."""
    try:
        return os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        return _repo_root()


def _local_appdata_root() -> str:
    """Raíz del perfil del usuario para instalación por-usuario (sin admin)."""
    for env in ("LOCALAPPDATA", "APPDATA"):
        v = str(os.environ.get(env) or "").strip()
        if v:
            return v
    return os.path.expanduser("~")


def data_dir() -> str:
    """
    Directorio persistente donde viven los mutables de la app
    (historial_jobs.json, inventario_remanentes.csv, _config, cache, _logs, ...).

    Resolución:
      1) ARGA_NEST_DATA_DIR  (override manual, útil para pruebas / portable).
      2) Frozen .exe        →  %LOCALAPPDATA%\\ArgaNestingSuite\\data
      3) Dev (python main.py) →  raíz del repo (compat).

    En modo frozen deja de escribir al lado del .exe: los updates ya no
    pueden pisar datos del usuario, y todas las PCs miran al mismo lugar.
    """
    override = str(os.environ.get("ARGA_NEST_DATA_DIR") or "").strip()
    if override:
        return override
    if _is_frozen():
        return os.path.join(_local_appdata_root(), APP_NAME, "data")
    return _repo_root()


def install_root() -> str:
    """
    Raíz del "producto instalado" (por-usuario). Contiene:
      - app\\<version>\\   (inmutable, viene de cada release)
      - data\\              (persistente, sobrevive updates)
      - updates\\           (descargas)
      - logs\\updater.log
      - install.json
    En dev retorna la raíz del repo (para no crear basura en LOCALAPPDATA).
    """
    if _is_frozen():
        return os.path.join(_local_appdata_root(), APP_NAME)
    return _repo_root()


def app_search_roots():
    """
    Raíces donde buscar recursos / datos.
    Orden:
      1) data_dir()           (mutables actuales)
      2) carpeta del .exe     (sidecars legacy y app\\<version>\\ en releases nuevos)
      3) bundle PyInstaller   (_MEIPASS)
      4) raíz del repo        (dev)
    """
    roots: list[str] = [data_dir()]
    if _is_frozen():
        exe_d = _exe_dir()
        if exe_d:
            roots.append(exe_d)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(str(meipass))
    roots.append(_repo_root())
    out: list[str] = []
    seen: set[str] = set()
    for r in roots:
        try:
            key = os.path.normcase(os.path.abspath(r))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def ruta_recurso(ruta_relativa):
    """Para archivos estáticos empaquetados dentro del .exe (imágenes, scripts, macros)."""
    try:
        # PyInstaller guarda los archivos empaquetados en esta ruta temporal
        ruta_base = sys._MEIPASS
    except Exception:
        ruta_base = _repo_root()
    return os.path.join(ruta_base, ruta_relativa)


def ruta_persistente(ruta_relativa):
    """
    Para archivos que el sistema/usuario modifica.

    Frozen: %LOCALAPPDATA%\\ArgaNestingSuite\\data\\<rel>  (sobrevive updates).
    Dev:    raíz del repo (compat con `python main.py`).
    """
    return os.path.join(data_dir(), ruta_relativa)


# Mutables que la app debe conservar entre versiones.
# Se usan para migrar del layout legacy (junto al .exe) al nuevo data_dir.
_LEGACY_MUTABLE_ENTRIES: tuple[tuple[str, bool], ...] = (
    ("historial_jobs.json", False),
    ("inventario_remanentes.csv", False),
    ("herinox_sync.local.json", False),
    ("configuracion_nesting.json", False),
    ("Plates.xlsx", False),
    ("cache", True),
    ("TEMP_PROCESSED", True),
    ("_logs", True),
    ("_config", True),
)


def _migrate_legacy_data(dst_dir: str) -> None:
    """
    One-shot: si el layout anterior (mutables al lado del .exe) tiene datos,
    los mueve a data_dir en el primer arranque de la versión nueva.

    - Best-effort: cualquier fallo se ignora para no romper el arranque.
    - No sobreescribe archivos ya presentes en data_dir.
    - Solo aplica en frozen; en dev el data_dir coincide con la raíz del repo.
    """
    if not _is_frozen():
        return
    src_dir = _exe_dir()
    if not src_dir:
        return
    try:
        same = os.path.normcase(os.path.abspath(src_dir)) == os.path.normcase(
            os.path.abspath(dst_dir)
        )
    except Exception:
        same = False
    if same:
        return
    for rel, is_dir in _LEGACY_MUTABLE_ENTRIES:
        src = os.path.join(src_dir, rel)
        dst = os.path.join(dst_dir, rel)
        try:
            if is_dir:
                if os.path.isdir(src) and not os.path.isdir(dst):
                    shutil.copytree(src, dst)
            else:
                if os.path.isfile(src) and not os.path.isfile(dst):
                    parent = os.path.dirname(dst)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    shutil.copy2(src, dst)
        except Exception:
            continue


# Mutables que se siembran desde `defaults/` (bundle o sidecar) al data_dir
# en primer arranque. Solo archivos (no directorios); los dirs se crean lazy
# cuando alguien escribe.
_SEED_FROM_DEFAULTS: tuple[str, ...] = (
    "inventario_remanentes.csv",
    "configuracion_nesting.json",
    os.path.join("_config", "step_export_folders.json"),
)


def _seed_defaults_into_data_dir(dst_dir: str) -> None:
    """
    Copia plantillas del release al data_dir en primer arranque.
    - En frozen: fuentes = `_MEIPASS/defaults/<rel>` y `<exe_dir>/defaults/<rel>`.
    - En dev: no aplica; el repo ya es el data_dir.
    Nunca sobreescribe archivos existentes.
    """
    if not _is_frozen():
        return
    for rel in _SEED_FROM_DEFAULTS:
        try:
            asegurar_archivo_persistente(rel)
        except Exception:
            continue


def bootstrap_data_dir() -> str:
    """
    Garantiza que data_dir exista, migra el layout anterior (mutables junto al
    .exe) y siembra plantillas del release (`defaults/`) que aún no existan.
    Devuelve el path absoluto de data_dir.
    """
    d = data_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    _migrate_legacy_data(d)
    _seed_defaults_into_data_dir(d)
    return d


def asegurar_archivo_persistente(ruta_relativa: str) -> str:
    """
    Ruta persistente en data_dir; si no existe, siembra desde (en orden):
      1) Bundle PyInstaller (_MEIPASS/defaults/<rel>)  — plantilla del release.
      2) Bundle PyInstaller (_MEIPASS/<rel>)           — plantilla legacy.
      3) Sidecar junto al .exe (compat con instalaciones actuales).
    """
    destino = ruta_persistente(ruta_relativa)
    if os.path.exists(destino):
        return destino
    fuentes: list[str] = []
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            fuentes.append(os.path.join(str(meipass), "defaults", ruta_relativa))
            fuentes.append(os.path.join(str(meipass), ruta_relativa))
    except Exception:
        pass
    if _is_frozen():
        exe_d = _exe_dir()
        if exe_d:
            fuentes.append(os.path.join(exe_d, "defaults", ruta_relativa))
            fuentes.append(os.path.join(exe_d, ruta_relativa))
    for src in fuentes:
        try:
            if os.path.isfile(src):
                parent = os.path.dirname(destino)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                shutil.copy2(src, destino)
                break
        except Exception:
            continue
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