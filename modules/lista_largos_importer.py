import csv
import hashlib
import json
import re
from io import StringIO
from pathlib import Path

import psycopg2


CANDIDATOS_CSV = (
    "Lista_Perfiles_Clasificados.csv",
    "Lista_Perfiles_Clasificado.csv",  # variante sin 's'
    "materiales_input.csv",
    "Lista_Largos.csv",
)

# Formatos legacy que pudieran existir en algunos proyectos
CANDIDATOS_JOB_DATA = (
    "job_data_job.json",
    "job_data_job.txt",
    "job_data_job",
)


def _norm_text(value: str) -> str:
    return str(value or "").strip()


def _norm_key(value: str) -> str:
    text = _norm_text(value).lower().lstrip("\ufeff")
    text = (
        text.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )
    return text


def _norm_job(value: str) -> str:
    return re.sub(r"\s+", " ", _norm_text(value)).upper()


def _job_compact(value: str) -> str:
    """Clave VSM/carpeta: ignora espacios, guiones y underscores (GIGA BOARD 5 ≡ GIGABOARD5)."""
    return re.sub(r"[\s_\-]+", "", _norm_job(value))


def _jobs_equivalentes(job_a: str, job_b: str) -> bool:
    """
    Compara job VSM (ej. 251007 / GIGABOARD5) vs carpeta/job_data
    (ej. 06_30_2322_TANK_251007 / GIGA BOARD 5).
    """
    a = _norm_job(job_a)
    b = _norm_job(job_b)
    if not a or not b:
        return True
    if a == b:
        return True
    if _job_compact(a) == _job_compact(b):
        return True
    if a.endswith("_" + b) or b.endswith("_" + a):
        return True
    # Sufijo numérico de tarjeta VSM dentro del nombre de carpeta.
    if a.isdigit() and (b.endswith(a) or f"TANK_{a}" in b or f"TANK{a}" in b):
        return True
    if b.isdigit() and (a.endswith(b) or f"TANK_{b}" in a or f"TANK{b}" in a):
        return True
    return False


def _path_is_dir(path: Path) -> bool:
    try:
        return path.exists() and path.is_dir()
    except OSError:
        return False


def _path_is_file(path: Path) -> bool:
    try:
        return path.exists() and path.is_file()
    except OSError:
        return False


def _iter_context_paths(ruta_exportacion: str):
    base = Path(_norm_text(ruta_exportacion))

    if _path_is_file(base):
        base = base.parent

    vistos = set()
    for actual in [base, *base.parents]:
        clave = str(actual).lower()
        if clave not in vistos:
            vistos.add(clave)
            yield actual


def _resolver_ruta_autodxf(ruta_exportacion: str) -> Path:
    for actual in _iter_context_paths(ruta_exportacion):
        if actual.name.strip().lower() == "autodxf":
            return actual

        if actual.name.strip().lower() == "processed files":
            padre = actual.parent
            if padre.name.strip().lower() == "autodxf":
                return padre

        candidata_1 = actual / "AutoDXF"
        if _path_is_dir(candidata_1):
            return candidata_1

        candidata_2 = actual / "MODEL CORE FILES" / "AutoDXF"
        if _path_is_dir(candidata_2):
            return candidata_2

    base = Path(_norm_text(ruta_exportacion))
    if _path_is_file(base):
        base = base.parent

    return base / "AutoDXF"


TANKS_CORPORATE_ROOTS = (
    Path(
        r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
        r"\ARGA METALS CORPORATE SYSTEM\TANKS"
    ),
)


def _es_carpeta_job_corporate(nombre: str, job_n: str, job_key: str) -> bool:
    """Match de carpeta TANKS/*/*/job sin recorrer el árbol (GIGA BOARD 5 ≡ GIGABOARD5)."""
    if not nombre:
        return False
    if _jobs_equivalentes(nombre, job_n):
        return True
    return bool(job_key) and _job_compact(nombre) == job_key


def _buscar_carpeta_job_corporate(
    job: str,
    roots: list[Path] | tuple[Path, ...] | None = None,
) -> Path | None:
    """
    Localiza carpeta de job en ARGA METALS CORPORATE SYSTEM/TANKS
    cuando la ruta de export no tiene AutoDXF (p. ej. duplicado «GIGA BOARD 5»
    vs carpeta VSM «GIGABOARD5»).
    Prefiere la carpeta que sí tenga MODEL CORE FILES/AutoDXF.

    Solo recorre 3 niveles (producto / cliente / job). Un rglob sobre TANKS
    en SMB congela el export de SWO (caso SWO-022 / 9919-11CABINET, 10+ min).
    """
    job_n = _norm_job(job)
    if not job_n:
        return None
    job_key = _job_compact(job_n)

    candidatos_con_autodxf: list[Path] = []
    candidatos_sin_autodxf: list[Path] = []

    def _registrar(hit: Path) -> None:
        autodxf = hit / "MODEL CORE FILES" / "AutoDXF"
        if _path_is_dir(autodxf):
            candidatos_con_autodxf.append(hit)
        else:
            candidatos_sin_autodxf.append(hit)

    for root in list(roots if roots is not None else TANKS_CORPORATE_ROOTS):
        if not _path_is_dir(root):
            continue

        vistos: set[str] = set()
        try:
            productos = list(root.iterdir())
        except OSError:
            continue

        for producto in productos:
            if not _path_is_dir(producto):
                continue
            try:
                clientes = list(producto.iterdir())
            except OSError:
                continue
            for cliente in clientes:
                if not _path_is_dir(cliente):
                    continue
                try:
                    jobs_dirs = list(cliente.iterdir())
                except OSError:
                    continue
                for hit in jobs_dirs:
                    if not _path_is_dir(hit):
                        continue
                    if not _es_carpeta_job_corporate(hit.name, job_n, job_key):
                        continue
                    clave = str(hit).lower()
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    _registrar(hit)

    if candidatos_con_autodxf:
        return sorted(candidatos_con_autodxf, key=lambda p: str(p).lower())[0]
    if candidatos_sin_autodxf:
        return sorted(candidatos_sin_autodxf, key=lambda p: str(p).lower())[0]
    return None


def _resolver_csv_lista_largos(ruta_autodxf: Path) -> Path | None:
    if not _path_is_dir(ruta_autodxf):
        return None

    for nombre in CANDIDATOS_CSV:
        candidato = ruta_autodxf / nombre
        if _path_is_file(candidato):
            return candidato

    for archivo in sorted(ruta_autodxf.glob("*.csv")):
        nombre = archivo.name.lower()
        if "lista" in nombre and ("perfil" in nombre or "larg" in nombre):
            return archivo

    return None


def _buscar_job_data_csv(ruta_exportacion: str) -> Path | None:
    for actual in _iter_context_paths(ruta_exportacion):
        try:
            if not actual.exists() or not actual.is_dir():
                continue

            candidatos = []
            for p in actual.iterdir():
                if p.is_file():
                    nombre = p.name.strip().lower()
                    if nombre.startswith("job_data_") and nombre.endswith(".csv"):
                        candidatos.append(p)

            if candidatos:
                return sorted(candidatos)[0]
        except Exception:
            pass
    return None


def _leer_tabla_csv_flexible(ruta_csv: Path) -> list[list[str]]:
    encodings = ("utf-8-sig", "cp1252", "latin-1")
    ultimo_error = None

    for enc in encodings:
        try:
            contenido = ruta_csv.read_text(encoding=enc, errors="ignore")
            if not contenido.strip():
                return []

            try:
                dialect = csv.Sniffer().sniff(contenido[:4096], delimiters=",;\t|")
            except Exception:
                dialect = csv.excel

            f = StringIO(contenido)
            reader = csv.reader(f, dialect)

            rows = []
            for row in reader:
                fila = [_norm_text(c).lstrip("\ufeff") for c in row]
                if any(fila):
                    rows.append(fila)

            return rows
        except Exception as e:
            ultimo_error = e

    raise RuntimeError(f"No se pudo leer el CSV '{ruta_csv}'. Error: {ultimo_error}")


def _primer_registro_job_data_csv(ruta_exportacion: str) -> dict:
    ruta_csv = _buscar_job_data_csv(ruta_exportacion)
    if ruta_csv is None:
        return {}

    try:
        filas = _leer_tabla_csv_flexible(ruta_csv)
        if len(filas) < 2:
            return {}

        encabezados = filas[0]
        datos = filas[1]

        registro = {}
        for i, encabezado in enumerate(encabezados):
            key = _norm_key(encabezado)
            valor = datos[i] if i < len(datos) else ""
            registro[key] = _norm_text(valor)

        return registro
    except Exception:
        return {}


def _extraer_job_desde_job_data_legacy(ruta_exportacion: str) -> str:
    for actual in _iter_context_paths(ruta_exportacion):
        for nombre in CANDIDATOS_JOB_DATA:
            ruta = actual / nombre
            if not ruta.exists() or not ruta.is_file():
                continue

            try:
                contenido = ruta.read_text(encoding="utf-8").strip()
                if contenido.startswith("{"):
                    data = json.loads(contenido)
                    if isinstance(data, dict):
                        for key in ("job", "nombre_job", "job_name"):
                            valor = _norm_text(data.get(key))
                            if valor:
                                return valor
            except Exception:
                pass

            try:
                contenido = ruta.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"(?im)^\s*job\s*[:=]\s*(.+?)\s*$", contenido)
                if m:
                    return _norm_text(m.group(1))

                for linea in contenido.splitlines():
                    linea = _norm_text(linea)
                    if linea:
                        return linea
            except Exception:
                pass

    return ""


def _extraer_job_desde_job_data_csv(ruta_exportacion: str) -> str:
    registro = _primer_registro_job_data_csv(ruta_exportacion)
    if registro:
        for key in (
            "job",
            "nombre_job",
            "job_name",
            "job_number",
            "job number",
        ):
            valor = _norm_text(registro.get(_norm_key(key)))
            if valor:
                return valor

    ruta_csv = _buscar_job_data_csv(ruta_exportacion)
    if ruta_csv is not None:
        stem = ruta_csv.stem
        if stem.lower().startswith("job_data_"):
            return _norm_text(stem[len("job_data_"):])

    return ""


def _extraer_job_desde_job_data(ruta_exportacion: str) -> str:
    valor = _extraer_job_desde_job_data_legacy(ruta_exportacion)
    if valor:
        return valor

    valor = _extraer_job_desde_job_data_csv(ruta_exportacion)
    if valor:
        return valor

    return ""


def _extraer_cantidad_job_desde_job_data_legacy(ruta_exportacion: str) -> int:
    """
    Regresa 0 si no encuentra nada.
    OJO: antes regresaba 1 por default y eso impedía caer al parser de job_data_*.csv.
    """
    for actual in _iter_context_paths(ruta_exportacion):
        for nombre in CANDIDATOS_JOB_DATA:
            ruta = actual / nombre
            if not ruta.exists() or not ruta.is_file():
                continue

            try:
                contenido = ruta.read_text(encoding="utf-8").strip()
                if contenido.startswith("{"):
                    data = json.loads(contenido)
                    if isinstance(data, dict):
                        for key in ("cantidad", "qty", "quantity", "cantidad_total"):
                            valor = data.get(key)
                            if valor is not None and str(valor).strip():
                                try:
                                    return max(1, int(float(str(valor).strip())))
                                except Exception:
                                    pass
            except Exception:
                pass

            try:
                contenido = ruta.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"(?im)^\s*(qty|cantidad|quantity)\s*[:=]\s*(.+?)\s*$", contenido)
                if m:
                    try:
                        return max(1, int(float(_norm_text(m.group(2)))))
                    except Exception:
                        pass
            except Exception:
                pass

    return 0


def _extraer_cantidad_job_desde_job_data_csv(ruta_exportacion: str) -> int:
    registro = _primer_registro_job_data_csv(ruta_exportacion)
    if not registro:
        return 0

    claves_preferidas = (
        "qty",
        "cantidad",
        "quantity",
        "cantidad_total",
        "qty_tanks",
        "qty tanque",
        "qty tanques",
    )

    for key in claves_preferidas:
        valor = registro.get(_norm_key(key))
        if valor is not None and str(valor).strip():
            try:
                return max(1, int(float(str(valor).strip())))
            except Exception:
                pass

    # fallback: cualquier columna cuyo encabezado huela a qty/cantidad
    for key_norm, valor in registro.items():
        if ("qty" in key_norm or "cantidad" in key_norm or "quantity" in key_norm) and str(valor).strip():
            try:
                return max(1, int(float(str(valor).strip())))
            except Exception:
                pass

    return 0


def _extraer_cantidad_job_desde_job_data(ruta_exportacion: str) -> int:
    cantidad = _extraer_cantidad_job_desde_job_data_legacy(ruta_exportacion)
    if cantidad and cantidad > 0:
        return cantidad

    cantidad = _extraer_cantidad_job_desde_job_data_csv(ruta_exportacion)
    if cantidad and cantidad > 0:
        return cantidad

    return 1


def _mapear_columnas(fieldnames: list[str]) -> dict:
    mapa = {_norm_key(c): c for c in (fieldnames or [])}

    return {
        "nombre": mapa.get("nombre"),
        "clasificacion": mapa.get("clasificacion") or mapa.get("clasificación"),
        "largo_in": mapa.get("largo (in)") or mapa.get("largo"),
        "cantidad": mapa.get("cantidad") or mapa.get("qty"),
        "proceso": mapa.get("proceso"),
        "codigo": mapa.get("codigo herinox")
        or mapa.get("codigo_herinox")
        or mapa.get("codigo"),
        "perfil": mapa.get("perfil") or mapa.get("perfil_estructural"),
        "material": mapa.get("material") or mapa.get("material_grade"),
    }


def _leer_csv_lista_largos(csv_path: Path) -> list[dict]:
    encodings = ("utf-8-sig", "cp1252", "latin-1")
    ultimo_error = None

    for enc in encodings:
        try:
            with csv_path.open("r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                columnas = _mapear_columnas(reader.fieldnames or [])

                if not columnas["nombre"] or not columnas["cantidad"]:
                    raise ValueError(
                        f"CSV sin columnas mínimas esperadas. Detectadas: {reader.fieldnames}"
                    )

                rows = []
                for raw in reader:
                    nombre = _norm_text(raw.get(columnas["nombre"]))
                    clasificacion = _norm_text(raw.get(columnas["clasificacion"])) if columnas["clasificacion"] else ""
                    largo_txt = _norm_text(raw.get(columnas["largo_in"])) if columnas["largo_in"] else "0"
                    cantidad_txt = _norm_text(raw.get(columnas["cantidad"]))

                    if not nombre:
                        continue

                    try:
                        largo_in = round(float(largo_txt or 0), 3)
                    except Exception:
                        largo_in = 0.0

                    try:
                        cantidad_base = int(float(cantidad_txt or 0))
                    except Exception:
                        cantidad_base = 0

                    codigo_csv = (
                        _norm_text(raw.get(columnas["codigo"]))
                        if columnas.get("codigo")
                        else ""
                    )
                    perfil_csv = (
                        _norm_text(raw.get(columnas["perfil"]))
                        if columnas.get("perfil")
                        else ""
                    )
                    material_csv = (
                        _norm_text(raw.get(columnas["material"]))
                        if columnas.get("material")
                        else ""
                    )
                    proceso_csv = (
                        _norm_text(raw.get(columnas["proceso"]))
                        if columnas.get("proceso")
                        else ""
                    )

                    rows.append(
                        {
                            "nombre": nombre,
                            "clasificacion": clasificacion,
                            "largo_in": largo_in,
                            "cantidad_base": cantidad_base,
                            "proceso": proceso_csv,
                            "herinox_codigo": codigo_csv,
                            "perfil_estructural": perfil_csv or None,
                            "material_grade": material_csv or None,
                        }
                    )

                return rows

        except Exception as e:
            ultimo_error = e

    raise RuntimeError(f"No se pudo leer el CSV '{csv_path}'. Error: {ultimo_error}")


def _row_hash(job: str, row: dict) -> str:
    cantidad_base = int(row.get("cantidad_base", row.get("cantidad", 0)) or 0)

    base = "|".join(
        [
            _norm_job(job),
            _norm_text(row.get("nombre")),
            _norm_text(row.get("clasificacion")),
            f"{float(row.get('largo_in', 0)):.3f}",
            str(cantidad_base),
            _norm_text(row.get("proceso")),
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def asegurar_tabla_lista_largos(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS public.lista_largos_job (
            id SERIAL PRIMARY KEY,
            job TEXT NOT NULL,
            source_csv_name TEXT NOT NULL,
            source_csv_path TEXT NOT NULL,
            nombre TEXT NOT NULL,
            clasificacion TEXT,
            largo_in NUMERIC(12,3) NOT NULL DEFAULT 0,
            cantidad INTEGER NOT NULL DEFAULT 0,
            row_hash TEXT NOT NULL,
            importado_el TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        ALTER TABLE public.lista_largos_job
        ADD COLUMN IF NOT EXISTS cantidad_base INTEGER
        """
    )

    cursor.execute(
        """
        ALTER TABLE public.lista_largos_job
        ADD COLUMN IF NOT EXISTS cantidad_job INTEGER
        """
    )

    cursor.execute(
        """
        ALTER TABLE public.lista_largos_job
        ADD COLUMN IF NOT EXISTS cantidad_total INTEGER
        """
    )

    cursor.execute(
        """
        ALTER TABLE public.lista_largos_job
        ADD COLUMN IF NOT EXISTS job_key TEXT
        """
    )

    cursor.execute(
        """
        UPDATE public.lista_largos_job
        SET job_key = UPPER(REGEXP_REPLACE(BTRIM(job), '\\s+', ' ', 'g'))
        WHERE job IS NOT NULL
        AND (job_key IS NULL OR BTRIM(job_key) = '')
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lista_largos_job_job_key
        ON public.lista_largos_job(job_key)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lista_largos_job_job
        ON public.lista_largos_job(job)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lista_largos_job_job_clasificacion
        ON public.lista_largos_job(job, clasificacion)
        """
    )


def importar_lista_largos_job(
    job: str,
    ruta_exportacion: str,
    db_config: dict,
    work_order_alcance: str | None = None,
    propagar_material: bool = True,
) -> dict:
    job = _norm_text(job)
    job_key = _norm_job(job)
    ruta_exportacion = _norm_text(ruta_exportacion)
    cantidad_job = _extraer_cantidad_job_desde_job_data(ruta_exportacion)

    print(f"[IMPORTADOR_LARGOS] job={job}")
    print(f"[IMPORTADOR_LARGOS] ruta_exportacion={ruta_exportacion}")
    print(f"[IMPORTADOR_LARGOS] cantidad_job={cantidad_job}")

    if not job:
        return {"ok": False, "status": "job_vacio", "insertados": 0}

    if not ruta_exportacion:
        return {"ok": False, "status": "ruta_exportacion_vacia", "insertados": 0}

    job_job_data = _extraer_job_desde_job_data(ruta_exportacion)
    if job_job_data and not _jobs_equivalentes(job_job_data, job):
        return {
            "ok": False,
            "status": "job_mismatch",
            "job_funcion": job,
            "job_job_data": job_job_data,
            "insertados": 0,
        }

    ruta_autodxf = _resolver_ruta_autodxf(ruta_exportacion)
    print(f"[IMPORTADOR_LARGOS] ruta_autodxf_resuelta={ruta_autodxf}")
    if not _path_is_dir(ruta_autodxf):
        print(
            "[IMPORTADOR_LARGOS] AutoDXF ausente junto al export; "
            "búsqueda corporativa TANKS (3 niveles, sin rglob)..."
        )
        job_folder = _buscar_carpeta_job_corporate(job)
        if job_folder is not None:
            print(f"[IMPORTADOR_LARGOS] fallback carpeta job={job_folder}")
            ruta_exportacion = str(job_folder)
            ruta_autodxf = _resolver_ruta_autodxf(ruta_exportacion)
            print(f"[IMPORTADOR_LARGOS] ruta_autodxf_fallback={ruta_autodxf}")
            # Releer qty del job real (job_data en carpeta corporate).
            cantidad_job = _extraer_cantidad_job_desde_job_data(ruta_exportacion)
            print(f"[IMPORTADOR_LARGOS] cantidad_job_fallback={cantidad_job}")
            job_job_data = _extraer_job_desde_job_data(ruta_exportacion)
            if job_job_data and not _jobs_equivalentes(job_job_data, job):
                return {
                    "ok": False,
                    "status": "job_mismatch",
                    "job_funcion": job,
                    "job_job_data": job_job_data,
                    "insertados": 0,
                }
        else:
            print("[IMPORTADOR_LARGOS] sin carpeta job en TANKS; se omite lista de largos.")
    if not _path_is_dir(ruta_autodxf):
        return {
            "ok": False,
            "status": "autodxf_no_existe",
            "ruta_autodxf": str(ruta_autodxf),
            "insertados": 0,
        }

    csv_path = _resolver_csv_lista_largos(ruta_autodxf)
    print(f"[IMPORTADOR_LARGOS] csv_path_resuelto={csv_path}")
    if csv_path is None:
        return {
            "ok": False,
            "status": "csv_no_encontrado",
            "ruta_autodxf": str(ruta_autodxf),
            "insertados": 0,
        }

    rows = _leer_csv_lista_largos(csv_path)
    print(f"[IMPORTADOR_LARGOS] filas_csv={len(rows)}")
    if not rows:
        return {
            "ok": True,
            "status": "csv_vacio",
            "csv_path": str(csv_path),
            "insertados": 0,
        }

    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**db_config)
        cursor = conexion.cursor()

        asegurar_tabla_lista_largos(cursor)
        try:
            from catalogo_largos import asegurar_columnas_lista_largos_job

            asegurar_columnas_lista_largos_job(cursor)
        except ImportError:
            pass

        # Si reimportas el mismo job, reemplazas el snapshot completo
        cursor.execute(
            """
            DELETE FROM public.lista_largos_job
            WHERE job_key = %s
            OR UPPER(REGEXP_REPLACE(BTRIM(job), '\\s+', ' ', 'g')) = %s
            """,
            (job_key, job_key)
        )

        insertados = 0
        catalogo_herinox = None
        try:
            from catalogo_largos import (
                _cargar_placas_largos_desde_herinox,
                buscar_placa_herinox,
                normalizar_fila_lista_largos,
            )

            catalogo_herinox = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
        except ImportError:
            buscar_placa_herinox = None  # type: ignore[assignment]
            normalizar_fila_lista_largos = None  # type: ignore[assignment]

        for row in rows:
            cantidad_base = int(row.get("cantidad_base", 0) or 0)
            cantidad_total = cantidad_base * max(1, int(cantidad_job or 1))

            perfil_estructural = row.get("perfil_estructural")
            material_grade = row.get("material_grade")
            ancho_in = None
            espesor_in = None
            herinox_codigo = str(row.get("herinox_codigo") or "").strip()
            material_key = row.get("clasificacion") or ""

            try:
                if normalizar_fila_lista_largos is None:
                    raise ImportError

                norm = normalizar_fila_lista_largos(
                    {
                        "clasificacion": row.get("clasificacion"),
                        "largo_in": row.get("largo_in"),
                    }
                )
                material_key = norm.get("material_key") or material_key
                if not perfil_estructural:
                    perfil_estructural = norm.get("perfil_estructural")
                if not material_grade:
                    material_grade = norm.get("material_grade")
                ancho_in = norm.get("ancho_in")
                espesor_in = norm.get("espesor_in")

                if not herinox_codigo and buscar_placa_herinox is not None:
                    placa = buscar_placa_herinox(
                        material_key,
                        float(row.get("largo_in") or 0),
                        solo_disponibles=False,
                        catalogo=catalogo_herinox,
                    )
                    if placa:
                        herinox_codigo = placa.get("codigo") or ""
                        if not perfil_estructural:
                            perfil_estructural = placa.get("perfil_estructural")
                        if not material_grade:
                            material_grade = placa.get("material_grade")
            except ImportError:
                pass

            cursor.execute(
                """
                INSERT INTO public.lista_largos_job (
                    job,
                    job_key,
                    source_csv_name,
                    source_csv_path,
                    nombre,
                    clasificacion,
                    largo_in,
                    cantidad,
                    cantidad_base,
                    cantidad_job,
                    cantidad_total,
                    row_hash,
                    perfil_estructural,
                    material_grade,
                    ancho_in,
                    espesor_in,
                    herinox_codigo,
                    material_key,
                    proceso
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job,
                    job_key,
                    csv_path.name,
                    str(csv_path),
                    row["nombre"],
                    row["clasificacion"],
                    row["largo_in"],
                    cantidad_base,
                    cantidad_base,
                    cantidad_job,
                    cantidad_total,
                    _row_hash(job, row),
                    perfil_estructural,
                    material_grade,
                    ancho_in,
                    espesor_in,
                    herinox_codigo,
                    material_key,
                    str(row.get("proceso") or "").strip() or None,
                ),
            )
            insertados += 1

        conexion.commit()

        pedidos_material: list = []
        if propagar_material:
            try:
                import api_server

                wo_scope = str(work_order_alcance or "").strip() or None
                print(
                    f"[IMPORTADOR_LARGOS] propagar_material job={job} "
                    f"wo_alcance={wo_scope or 'TODAS'}"
                )
                pedidos_material = api_server._propagar_material_requerido_por_job(
                    db_config,
                    job,
                    solo_work_order=wo_scope,
                )
            except Exception as e_ped:
                print(
                    f"[IMPORTADOR_LARGOS][WARN] material requerido tras import job '{job}': {e_ped}"
                )

        return {
            "ok": True,
            "status": "importado",
            "job": job,
            "csv_path": str(csv_path),
            "cantidad_job": cantidad_job,
            "insertados": insertados,
            "pedidos_material": pedidos_material,
        }

    except Exception:
        if conexion:
            conexion.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()