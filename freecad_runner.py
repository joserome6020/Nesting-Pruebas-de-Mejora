import glob
import os
import shutil
import subprocess
import time
from pathlib import Path

import config


def _norm_path(p: str) -> str:
    return os.path.normpath(str(p or "").strip())


def _equivalente_ruta_mapeada(p: str) -> str:
    """Si la ruta UNC no es accesible, intenta la misma ruta vía unidad mapeada (X:, Y:, …)."""
    p = _norm_path(p)
    if not p:
        return p
    if os.path.isdir(p):
        return p

    raiz = _norm_path(getattr(config, "RUTA_SERVIDOR_RAIZ", "") or "")
    if not raiz:
        return p

    try:
        rel = os.path.relpath(p, raiz)
    except ValueError:
        return p

    if rel.startswith(".."):
        return p

    for letra in ("X", "Y", "Z", "W"):
        candidato = _norm_path(os.path.join(f"{letra}:\\", rel))
        if os.path.isdir(candidato):
            return candidato
    return p


def _variantes_ruta_acceso(p: str) -> list[str]:
    p = _norm_path(p)
    if not p:
        return []
    variantes = [p]
    alt = _equivalente_ruta_mapeada(p)
    if alt and alt not in variantes:
        variantes.append(alt)
    return variantes


def _listar_dxfs_en_carpeta(carpeta: str) -> list[str]:
    carpeta = _norm_path(carpeta)
    if not carpeta or not os.path.isdir(carpeta):
        return []
    archivos = sorted(glob.glob(os.path.join(carpeta, "*.dxf")))
    archivos.extend(sorted(glob.glob(os.path.join(carpeta, "*.DXF"))))
    vistos = set()
    unicos = []
    for path in archivos:
        key = os.path.normcase(path)
        if key in vistos:
            continue
        vistos.add(key)
        unicos.append(path)
    return unicos


def _esperar_dxfs_en_carpeta(carpeta: str, *, timeout_sec: float = 12.0) -> list[str]:
    deadline = time.time() + max(0.5, float(timeout_sec))
    ultimos: list[str] = []
    while time.time() < deadline:
        ultimos = _listar_dxfs_en_carpeta(carpeta)
        if ultimos and all(os.path.getsize(f) > 0 for f in ultimos):
            return ultimos
        time.sleep(0.25)
    return ultimos


def _iter_freecad_candidates() -> list[str]:
    candidates: list[str] = []

    # 1) Config/env explícitos primero.
    configured = [
        os.getenv("FREECAD_EXE"),
        os.getenv("FREECAD_CMD"),
        getattr(config, "FREECAD_EXE", None),
        getattr(config, "FREECAD_CMD", None),
    ]
    for item in configured:
        if item:
            candidates.append(str(item))

    # 2) PATH del sistema.
    for binary in ("FreeCAD.exe", "FreeCADCmd.exe", "freecad.exe", "freecadcmd.exe"):
        hit = shutil.which(binary)
        if hit:
            candidates.append(hit)

    # 3) Rutas típicas de instalación (distintas versiones).
    program_roots = [
        os.getenv("ProgramFiles"),
        os.getenv("ProgramFiles(x86)"),
        r"C:\Program Files",
        r"C:\Program Files (x86)",
    ]
    known_versions = ("1.0", "0.22", "0.21", "0.20")
    for root in program_roots:
        if not root:
            continue
        for ver in known_versions:
            candidates.append(os.path.join(root, f"FreeCAD {ver}", "bin", "FreeCAD.exe"))
            candidates.append(os.path.join(root, f"FreeCAD {ver}", "bin", "FreeCADCmd.exe"))
        candidates.append(os.path.join(root, "FreeCAD", "bin", "FreeCAD.exe"))
        candidates.append(os.path.join(root, "FreeCAD", "bin", "FreeCADCmd.exe"))

    # 4) Búsqueda glob de respaldo (sin find/grep).
    for root in program_roots:
        if not root or not os.path.isdir(root):
            continue
        for pattern in (
            os.path.join(root, "FreeCAD*", "bin", "FreeCAD.exe"),
            os.path.join(root, "FreeCAD*", "bin", "FreeCADCmd.exe"),
        ):
            candidates.extend(glob.glob(pattern))

    # Quitar duplicados preservando orden.
    unique: list[str] = []
    seen = set()
    for raw in candidates:
        norm = os.path.normcase(os.path.normpath(str(raw)))
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(os.path.normpath(str(raw)))
    return unique


def _resolve_freecad_executable() -> str | None:
    valid = [p for p in _iter_freecad_candidates() if os.path.isfile(p)]
    if not valid:
        return None

    # Preferir GUI para el flujo actual (usa módulos GUI/importDXF).
    for p in valid:
        if os.path.basename(p).lower() == "freecad.exe":
            return p
    return valid[0]


def _resolve_macro_script(*, prefer_verde: bool = False) -> str | None:
    explicit = [
        os.getenv("FREECAD_SCRIPT"),
        os.getenv("FREECAD_MACRO"),
        getattr(config, "FREECAD_SCRIPT", None),
        getattr(config, "FREECAD_MACRO", None),
    ]
    for item in explicit:
        if item and os.path.isfile(item):
            return os.path.normpath(str(item))

    verde = os.path.join(os.path.dirname(__file__), "generador_verde.FCMacro")
    batch = os.path.join(os.path.dirname(__file__), "freecad_batch_dxf_to_step.py")
    orden = [verde, batch] if prefer_verde else [batch, verde]
    for item in orden:
        if os.path.isfile(item):
            return os.path.normpath(item)
    return None


def _resolve_log_path(step_folder: str) -> str:
    candidates = [
        os.path.join(step_folder, "_logs"),
        getattr(config, "ruta_persistente", lambda x: os.path.join(os.getcwd(), x))("_logs"),
        os.path.join(os.getcwd(), "_logs"),
    ]
    for folder in candidates:
        try:
            os.makedirs(folder, exist_ok=True)
            return os.path.join(folder, "freecad_runner.log")
        except Exception:
            pass
    return os.path.join(os.getcwd(), "freecad_runner.log")

def ejecutar_macro_freecad(
    dxf_folder: str,
    step_folder: str,
    thickness_mm: float,
    origen: str = "TR",
    off_x: float = 0.0,
    off_y: float = 0.0,
    off_z: float = 0.0,
    *,
    prefer_verde: bool = False,
    max_intentos: int = 2,
    material: str = "",
) -> bool:

    def snapshot_steps(folder: str):
        data = {}
        folder = os.path.normpath(folder)
        if not folder or not os.path.isdir(folder):
            return data

        for path in glob.glob(os.path.join(folder, "*.step")):
            try:
                data[os.path.normpath(path)] = os.path.getmtime(path)
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

    # 1) Normalización de rutas y variantes (UNC ↔ unidad mapeada).
    dxf_folder = _norm_path(dxf_folder)
    step_folder = _norm_path(step_folder)
    dxf_resuelta = None
    for candidato in _variantes_ruta_acceso(dxf_folder):
        dxfs = _esperar_dxfs_en_carpeta(candidato)
        if dxfs:
            dxf_resuelta = candidato
            break
    if dxf_resuelta is None:
        dxf_resuelta = dxf_folder

    step_resuelta = step_folder
    for candidato in _variantes_ruta_acceso(step_folder):
        try:
            os.makedirs(candidato, exist_ok=True)
            step_resuelta = candidato
            break
        except Exception:
            continue

    ruta_exe = _resolve_freecad_executable()
    ruta_macro = _resolve_macro_script(prefer_verde=prefer_verde)

    # 2) Log portable (evita depender de C:\NEST_EXPORTS).
    log_path = _resolve_log_path(step_resuelta)
    log_dir = os.path.dirname(log_path)

    dxf_files = _esperar_dxfs_en_carpeta(dxf_resuelta)
    n_dxf = len(dxf_files)
    # Cobre / nests grandes: un FreeCAD por lote; ~2 min/archivo, tope 6 h.
    timeout_sec = max(1800.0, min(21600.0, n_dxf * 120.0))

    with open(log_path, "a", encoding="utf-8") as f:
        def _log(msg: str) -> None:
            f.write(msg)
            f.flush()

        _log(f"\n--- INICIANDO CONVERSIÓN STEP ({origen}) ---\n")
        f.write(f"Ejecutable: {ruta_exe}\n")
        f.write(f"Macro: {ruta_macro}\n")
        f.write(f"DXF folder (solicitado): {dxf_folder}\n")
        f.write(f"DXF folder (resuelto): {dxf_resuelta}\n")
        f.write(f"STEP folder (solicitado): {step_folder}\n")
        f.write(f"STEP folder (resuelto): {step_resuelta}\n")

        if not ruta_exe or not os.path.exists(ruta_exe):
            f.write("ERROR FATAL: No se encontró ejecutable de FreeCAD.\n")
            f.write("Candidatos evaluados:\n")
            for c in _iter_freecad_candidates():
                f.write(f" - {c}\n")
            f.write(
                "TIP: define FREECAD_EXE o FREECAD_CMD en variables de entorno "
                "o en config.py con la ruta real del ejecutable.\n"
            )
            return False

        if not ruta_macro or not os.path.exists(ruta_macro):
            f.write("ERROR FATAL: No se encontró el archivo de la macro/script.\n")
            f.write(
                "TIP: define FREECAD_SCRIPT/FREECAD_MACRO o verifica "
                "freecad_batch_dxf_to_step.py y generador_verde.FCMacro.\n"
            )
            return False

        if not os.path.isdir(dxf_resuelta):
            f.write("ERROR FATAL: La carpeta DXF no existe o no es accesible.\n")
            f.write("Variantes probadas:\n")
            for cand in _variantes_ruta_acceso(dxf_folder):
                f.write(f" - {cand} (existe={os.path.isdir(cand)})\n")
            return False

        dxf_files = _esperar_dxfs_en_carpeta(dxf_resuelta)
        n_dxf = len(dxf_files)
        _log(f"DXF detectados: {n_dxf}\n")
        _log(f"Timeout FreeCAD: {int(timeout_sec)} s\n")
        for path in dxf_files[:20]:
            f.write(f" - {path}\n")
        if len(dxf_files) > 20:
            f.write(f" ... (+{len(dxf_files) - 20} más)\n")

        if not dxf_files:
            f.write("ERROR FATAL: No se encontraron DXF para procesar.\n")
            return False

        # 3) Asegurar carpeta STEP sin abortar duro.
        try:
            if not os.path.isdir(step_resuelta):
                os.makedirs(step_resuelta, exist_ok=True)
                f.write("STEP folder asegurada/creada desde Python.\n")
            else:
                f.write("STEP folder ya existe.\n")
        except Exception as e:
            f.write(f"[WARN] No se pudo asegurar la carpeta STEP desde Python: {e}\n")
            f.write("[WARN] Se continuará y se dejará que FreeCAD intente escribir directamente.\n")

        before_snapshot = snapshot_steps(step_resuelta)
        f.write(f"STEP antes: {len(before_snapshot)}\n")

        env = os.environ.copy()
        env["FREECAD_DXF_IN"] = dxf_resuelta
        env["FREECAD_STEP_OUT"] = step_resuelta
        env["FREECAD_LOG_DIR"] = log_dir
        env["FREECAD_LOG_PATH"] = os.path.join(log_dir, "freecad_macro.log")
        env["FREECAD_THK_MM"] = str(thickness_mm)
        env["FREECAD_SCALE"] = str(getattr(config, 'FREECAD_SCALE', 1.0))
        env["FREECAD_ORIGIN"] = origen
        env["FREECAD_OFFSET_X"] = str(off_x)
        env["FREECAD_OFFSET_Y"] = str(off_y)
        env["FREECAD_OFFSET_Z"] = str(off_z)
        if material:
            env["FREECAD_MATERIAL"] = str(material).strip().upper()

        cmd = [ruta_exe, ruta_macro]
        _log(f"Comando lanzado: {' '.join(cmd)}\n")

        for intento in range(1, max(1, int(max_intentos)) + 1):
            if intento > 1:
                _log(f"\n--- REINTENTO STEP {intento}/{max_intentos} ---\n")
                time.sleep(1.0)
                dxf_files = _esperar_dxfs_en_carpeta(dxf_resuelta, timeout_sec=5.0)
                _log(f"DXF detectados (reintento): {len(dxf_files)}\n")

            try:
                timeout_sec = max(1800.0, min(21600.0, len(dxf_files) * 120.0))
                _log(f"Timeout FreeCAD (intento {intento}): {int(timeout_sec)} s\n")
                stdout_log = os.path.join(log_dir, "freecad_stdout.log")
                stderr_log = os.path.join(log_dir, "freecad_stderr.log")
                with open(stdout_log, "w", encoding="utf-8") as out_f, open(
                    stderr_log, "w", encoding="utf-8"
                ) as err_f:
                    proc = subprocess.run(
                        cmd,
                        stdout=out_f,
                        stderr=err_f,
                        env=env,
                        timeout=timeout_sec,
                    )

                try:
                    with open(stdout_log, "r", encoding="utf-8", errors="replace") as out_f:
                        stdout_text = out_f.read()
                except Exception:
                    stdout_text = ""
                try:
                    with open(stderr_log, "r", encoding="utf-8", errors="replace") as err_f:
                        stderr_text = err_f.read()
                except Exception:
                    stderr_text = ""

                _log("=== SALIDA DE FREECAD ===\n")
                _log((stdout_text or "") + "\n")
                _log("=== ERRORES DE FREECAD ===\n")
                _log((stderr_text or "") + "\n")
                _log(f"Return code: {proc.returncode}\n")

                after_snapshot = snapshot_steps(step_resuelta)
                nuevos, actualizados = diff_steps(before_snapshot, after_snapshot)

                _log(f"STEP después: {len(after_snapshot)}\n")
                _log(f"STEP nuevos: {len(nuevos)}\n")
                _log(f"STEP actualizados: {len(actualizados)}\n")

                for path in nuevos:
                    _log(f"STEP NUEVO -> {path}\n")

                for path in actualizados:
                    _log(f"STEP ACTUALIZADO -> {path}\n")

                ok = (
                    proc.returncode == 0
                    and (nuevos or actualizados or len(after_snapshot) > 0)
                )
                if ok:
                    _log("RESULTADO FINAL: OK\n")
                    return True

                if proc.returncode != 0:
                    _log("RESULTADO: FAIL (FreeCAD devolvió código distinto de 0)\n")
                elif not nuevos and not actualizados and len(after_snapshot) == 0:
                    _log("RESULTADO: FAIL (no se detectó ningún STEP en la carpeta destino)\n")
                else:
                    _log("RESULTADO: FAIL (FreeCAD terminó pero no dejó STEP nuevos ni actualizados)\n")

            except subprocess.TimeoutExpired as e:
                _log(f"TIMEOUT FreeCAD tras {int(timeout_sec)}s: {e}\n")
            except Exception as e:
                _log(f"EXCEPCIÓN DE WINDOWS: {e}\n")

        _log("RESULTADO FINAL: FAIL (agotados reintentos)\n")
        return False