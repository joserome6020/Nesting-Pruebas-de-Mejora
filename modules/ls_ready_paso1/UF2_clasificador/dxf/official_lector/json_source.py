import os

JSON_OUTPUT_SUBDIR = "JSON GENERADO"
LECTOR_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_JSON_POINTER = os.path.join(LECTOR_DIR, "ultimo_json.txt")


def resolve_output_folder(dxf_file, output_folder=None):
    """
    Por defecto guarda el JSON en una subcarpeta junto al DXF:
    <carpeta_del_dxf>/JSON GENERADO/
    """
    if output_folder:
        return output_folder

    dxf_dir = os.path.dirname(os.path.abspath(dxf_file))
    return os.path.join(dxf_dir, JSON_OUTPUT_SUBDIR)


def json_file_from_dxf(dxf_file):
    base = os.path.splitext(os.path.basename(dxf_file))[0]
    return os.path.join(resolve_output_folder(dxf_file), base + ".json")


def get_latest_json_file(folder, name_contains=None):
    if not os.path.isdir(folder):
        raise Exception("La carpeta de JSON no existe: {0}".format(folder))

    candidates = []

    for name in os.listdir(folder):
        full_path = os.path.join(folder, name)

        if not os.path.isfile(full_path):
            continue

        if not name.lower().endswith(".json"):
            continue

        if name_contains and name_contains.lower() not in name.lower():
            continue

        try:
            mtime = os.path.getmtime(full_path)
        except Exception:
            continue

        candidates.append((mtime, full_path))

    if not candidates:
        if name_contains:
            raise Exception(
                "No se encontraron archivos .json en la carpeta '{0}' que contengan '{1}'".format(
                    folder, name_contains
                )
            )
        raise Exception(
            "No se encontraron archivos .json en la carpeta '{0}'".format(folder)
        )

    candidates.sort(key=lambda x: (x[0], x[1].lower()), reverse=True)
    return candidates[0][1]


def read_last_json_pointer():
    if not os.path.isfile(LAST_JSON_POINTER):
        return None

    try:
        with open(LAST_JSON_POINTER, "r", encoding="utf-8") as f:
            path = f.read().strip().strip('"').strip("'")
    except Exception:
        return None

    if path and os.path.isfile(path):
        return path

    return None


def write_last_json_pointer(json_file):
    if not json_file:
        return

    os.makedirs(os.path.dirname(LAST_JSON_POINTER), exist_ok=True)
    with open(LAST_JSON_POINTER, "w", encoding="utf-8") as f:
        f.write(os.path.abspath(json_file))


def resolve_json_file(name_contains=None):
    """
    Resuelve qué JSON usar para los generadores de paths.

    Prioridad:
    1) NESTING_JSON_FILE
    2) NESTING_DXF_FILE -> <DXF>/JSON GENERADO/<nombre>.json
    3) NESTING_JSON_DIR
    4) ultimo_json.txt escrito por lector_dxf
    """
    env_file = os.environ.get("NESTING_JSON_FILE", "").strip()
    if env_file and os.path.isfile(env_file):
        return os.path.abspath(env_file)

    env_dxf = os.environ.get("NESTING_DXF_FILE", "").strip()
    if env_dxf and os.path.isfile(env_dxf):
        expected = json_file_from_dxf(env_dxf)
        if os.path.isfile(expected):
            return expected

        json_dir = resolve_output_folder(env_dxf)
        if os.path.isdir(json_dir):
            return get_latest_json_file(json_dir, name_contains)

    env_dir = os.environ.get("NESTING_JSON_DIR", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return get_latest_json_file(env_dir, name_contains)

    pointer_path = read_last_json_pointer()
    if pointer_path:
        if name_contains and name_contains.lower() not in os.path.basename(pointer_path).lower():
            pointer_dir = os.path.dirname(pointer_path)
            if os.path.isdir(pointer_dir):
                return get_latest_json_file(pointer_dir, name_contains)
        return pointer_path

    raise Exception(
        "No se pudo resolver el JSON. Ejecuta lector_dxf.py primero "
        "o define NESTING_JSON_FILE / NESTING_DXF_FILE / NESTING_JSON_DIR."
    )
