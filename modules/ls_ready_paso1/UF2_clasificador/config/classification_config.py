# -*- coding: utf-8 -*-
"""Parametros de clasificacion de piezas (CARLOS RPA, independiente del RPA antiguo)."""
import os

try:
    _CFG_ROOT = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )
except NameError:
    _CFG_ROOT = r"C:\Users\RPA\Desktop\CARLOS RPA"

CAROUSEL_ROOT = _CFG_ROOT
CLASSIFICATION_DIR = os.path.join(CAROUSEL_ROOT, "classification")
CLASSIFIED_JSON_DIR = os.path.join(CAROUSEL_ROOT, "json", "classified")
CLASSIFICATION_LOG_DIR = os.path.join(CLASSIFICATION_DIR, "LOGS")

# JSON crudo del lector DXF (solo lectura; no modifica CORTE AUTOMATICO).
JSON_SOURCE_DIR = os.environ.get(
    "CARLOS_RPA_JSON_SOURCE",
    r"E:\CORTE AUTOMATICO\CORTE AUTOMATICO\LECTOR DXF",
)

SCHEMA_VERSION = "carlos_rpa.piece_plan.v3.uf2_b"

# Asignacion geometria -> pieza
PLATE_ASSIGN_MARGIN_MM = 5.0
POINTS_MAJORITY_RATIO = 0.5

# Reglas base para preparacion LS / robot.
# El clasificador solo declara la intencion; la conversion final a P[n] ocurre en el generador LS.
LS_MARGIN_MM = 3.0
CUT_IN_MM = 3.0
DEFAULT_UFRAME_NUM = 2
DEFAULT_UTOOL_NUM = 1  # Se conserva la herramienta original; solo cambia User Frame.

# Marcaje: texto vs figuras (misma logica que marcaje_texto.py)
MARK_DISABLE_TEXT = False
FIGURE_ONE_STEP_PER_COMPONENT = True

# Optimización LS para marcaje
TEXT_LASER_MODE = "continuous"
TEXT_CONTINUOUS_DROP_DUPLICATE_TOL_MM = 0.01
FIGURE_LASER_MODE = "stroke"
FIGURE_STROKE_CONNECT_TOL_MM = 0.05
# Reconstrucción de figuras de grabado desde DXF abierto/explotado.
FIGURE_COMPONENT_RECONSTRUCT = True
FIGURE_COMPONENT_ENDPOINT_TOL_MM = 0.05
FIGURE_COMPONENT_MICRO_GAP_MERGE_MM = 1.0
FIGURE_COMPONENT_MICRO_GAP_MIN_OVERLAP_MM = 5.0
TEXT_COMPONENT_MIN_STROKES = 80
TEXT_COMPONENT_MAX_THICKNESS_MM = 90.0
TEXT_COMPONENT_MAX_LONG_DIM_MM = 700.0
TEXT_COMPONENT_EXCLUSION_PAD_MM = 2.0
MIN_STROKE_LENGTH_MM = 0.8
TEXT_MAX_DIM_MULT = 3.0
TEXT_MAX_LEN_MULT = 4.0
TEXT_MAX_DIM_ABS = 35.0
TEXT_MAX_LEN_ABS = 100.0
# Agrupacion de lineas de figura (marcaje) — valores por defecto genericos.
# Perfil 240X48 sobreescribe en config/plate_profiles/profile_240x48.py
FIGURE_LINE_X_TOL_MM = 40.0
FIGURE_LINE_Y_TOL_MM = 40.0
FIGURE_LINE_ORIENTATION_RATIO = 1.5
FIGURE_LINE_MIN_LENGTH_MM = 0.0
FIGURE_COMPOSITE_MERGE = False
FIGURE_BBOX_PAD_MM = 2.0
FIGURE_COMPOSITE_OVERLAP_MIN_MM = 30.0
FIGURE_COMPOSITE_MAX_GAP_X_MM = 450.0
FIGURE_COMPOSITE_MAX_GAP_Y_MM = 500.0
TEXT_ORIENTATION_RATIO = 1.2
TEXT_ORIENTATION_PERCENTILE_LO = 0.10
TEXT_ORIENTATION_PERCENTILE_HI = 0.90
TEXT_CLUSTER_CELL_MM = 80.0
TEXT_CLUSTER_MIN_FRACTION = 0.45
MIN_INNER_POINTS = 4
MIN_INNER_AREA = 1.0
MIN_BARRENO_POINTS = 6
BARRENO_ASPECT_MIN = 0.80
BARRENO_ASPECT_MAX = 1.25
BARRENO_CIRCULARITY_MIN = 0.84
MIN_OVAL_POINTS = 10
OVAL_ELONGATION_MIN = 1.25
OVAL_ELONGATION_MAX = 6.00
OVAL_CIRCULARITY_MIN = 0.60

# Grupos de barrenos (mm) — perfil 240X48 define 800 x 1300
HOLE_GROUP_MAX_DX_MM = 800.0
HOLE_GROUP_MAX_DY_MM = 1300.0

# Filtro placa completa en cut_outer
SHEET_PLATE_MATCH_TOL_MM = 2.0

# Filtro franjas de borde de placa (margenes del DXF, no piezas de nesting)
PLATE_EDGE_STRIP_FILTER = True
PLATE_EDGE_TOUCH_TOL_MM = 5.0
PLATE_EDGE_MAX_STRIP_THICKNESS_MM = 650.0
PLATE_EDGE_MIN_SPAN_RATIO = 0.80

# Reglas opcionales de normalización/orden para perfiles altos.
NORMALIZE_NESTING = False
NORMALIZATION_MARGIN_MM = 10.0
COLUMN_BAND_MM = 100.0
HOLE_GROUPING_MODE = "grouped"
J2_WARNING_DEG = 55.0
J2_PHYSICAL_LIMIT_DEG = 65.0
