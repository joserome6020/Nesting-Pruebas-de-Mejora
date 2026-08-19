import os
import re

MM_PER_INCH = 25.4

DEFAULT_ESCENAS_ROOT = os.path.join(
    os.path.expanduser("~"), "Desktop", "ESCENAS CORTE"
)

# Anchos de placa reconocidos en el nombre de escena (240X48, 240X60, …)
SCENE_WIDTHS_IN = (48, 60, 72, 96)

LASER_LINE_CONFIG = {
    "L4": {
        "folder": "ESCENAS CALIBRADAS L4",
        "name_re": re.compile(
            r"Robot\s*Laser\s*4\s+240\s*[xX]\s*(\d+)\s+CAMA\s*([AB])",
            re.IGNORECASE,
        ),
    },
    "L3": {
        "folder": "ESCENAS CALIBRADAS L3",
        "name_re": re.compile(
            r"Robot\s*Laser\s*3\s+240\s*[xX]\s*(\d+)\s+CAMA\s*([AB])",
            re.IGNORECASE,
        ),
    },
}

DEFAULT_LASER_LINE = "L3"
DEFAULT_CAMA = "A"
# Orden del pipeline maestro histórico; este paquete clasifica para cama A.
PIPELINE_LASER_LINES = ("L4", "L3")
INCH_MATCH_TOLERANCE = 2.0


def normalize_laser_line(laser_line):
    value = (laser_line or DEFAULT_LASER_LINE).strip().upper()
    if value not in LASER_LINE_CONFIG:
        raise ValueError("Línea laser no soportada: {0}".format(value))
    return value


def get_active_laser_line():
    """Lee NESTING_LASER_LINE del entorno; el maestro la fija antes de cada corrida."""
    value = os.environ.get("NESTING_LASER_LINE", "").strip().upper()
    if value in LASER_LINE_CONFIG:
        return value
    return DEFAULT_LASER_LINE


def laser_stage_prefix(laser_line=None):
    return normalize_laser_line(laser_line or get_active_laser_line()).lower()


def laser_stage_key(base_key, laser_line=None):
    return "{0}_{1}".format(laser_stage_prefix(laser_line), base_key)


def mm_to_inches(mm):
    return float(mm) / MM_PER_INCH


def snap_inches(value_in, allowed=SCENE_WIDTHS_IN, tolerance=INCH_MATCH_TOLERANCE):
    best = min(allowed, key=lambda x: abs(x - value_in))
    if abs(best - value_in) > tolerance:
        raise ValueError(
            "Dimensión de placa {0:.1f}\" no coincide con anchos de escena {1}".format(
                value_in, list(allowed)
            )
        )
    return best


def plate_inches_from_mm(width_mm, height_mm):
    w_in = mm_to_inches(width_mm)
    h_in = mm_to_inches(height_mm)
    short_in = min(w_in, h_in)
    long_in = max(w_in, h_in)
    return {
        "width_in": round(w_in, 2),
        "height_in": round(h_in, 2),
        "short_in": round(short_in, 2),
        "long_in": round(long_in, 2),
    }


def scene_size_key_from_plate(width_mm, height_mm):
    """
    Mapea dimensiones de placa (mm) al token de escena 240X{width}.

    Ejemplos:
      120x48  -> 240X48
      120x60  -> 240X60
      240x48  -> 240X48
      240x96  -> 240X96
    """
    dims = plate_inches_from_mm(width_mm, height_mm)
    width_code = snap_inches(dims["short_in"])
    return "240X{0}".format(width_code), width_code, dims


def _list_scene_files(escenas_root, laser_line):
    cfg = LASER_LINE_CONFIG[laser_line]
    folder = os.path.join(escenas_root, cfg["folder"])
    if not os.path.isdir(folder):
        raise FileNotFoundError(
            "No existe la carpeta de escenas: {0}".format(folder)
        )

    scenes = []
    for name in os.listdir(folder):
        if not name.lower().endswith(".robx"):
            continue
        full = os.path.join(folder, name)
        m = cfg["name_re"].search(name)
        if not m:
            continue
        scenes.append(
            {
                "path": full,
                "filename": name,
                "width_code": int(m.group(1)),
                "cama": m.group(2).upper(),
                "size_key": "240X{0}".format(m.group(1)),
            }
        )
    return scenes


def find_scene_file(
    width_mm,
    height_mm,
    escenas_root=DEFAULT_ESCENAS_ROOT,
    laser_line=DEFAULT_LASER_LINE,
    cama=DEFAULT_CAMA,
):
    size_key, width_code, dims = scene_size_key_from_plate(width_mm, height_mm)
    cama = (cama or DEFAULT_CAMA).upper()
    laser_line = normalize_laser_line(laser_line)

    scenes = _list_scene_files(escenas_root, laser_line)
    matches = [
        s
        for s in scenes
        if s["width_code"] == width_code and s["cama"] == cama
    ]

    if not matches:
        available = sorted(
            {"{0} CAMA {1}".format(s["size_key"], s["cama"]) for s in scenes}
        )
        raise FileNotFoundError(
            "No se encontró escena {0} CAMA {1} en {2}. Disponibles: {3}".format(
                size_key,
                cama,
                os.path.join(escenas_root, LASER_LINE_CONFIG[laser_line]["folder"]),
                ", ".join(available) or "(ninguna)",
            )
        )

    if len(matches) > 1:
        matches.sort(key=lambda s: s["filename"].lower())

    chosen = matches[0]
    return {
        "scene_path": chosen["path"],
        "scene_filename": chosen["filename"],
        "scene_size_key": size_key,
        "scene_width_code_in": width_code,
        "laser_line": laser_line,
        "cama": cama,
        "escenas_root": escenas_root,
        "plate": dims,
    }


def resolve_scene_from_sheet(
    sheet_info,
    escenas_root=DEFAULT_ESCENAS_ROOT,
    laser_line=DEFAULT_LASER_LINE,
    cama=DEFAULT_CAMA,
):
    if not sheet_info:
        raise ValueError("El JSON no trae información de placa (meta.sheet).")

    width_mm = sheet_info.get("width_mm")
    height_mm = sheet_info.get("height_mm")
    if width_mm is None or height_mm is None:
        raise ValueError("meta.sheet no trae width_mm / height_mm.")

    result = find_scene_file(
        width_mm=width_mm,
        height_mm=height_mm,
        escenas_root=escenas_root,
        laser_line=laser_line,
        cama=cama,
    )
    result["source_layer"] = sheet_info.get("source_layer")
    return result


def resolve_scene_from_json_data(data, **kwargs):
    meta = data.get("meta") or {}
    sheet = meta.get("sheet")
    return resolve_scene_from_sheet(sheet, **kwargs)
