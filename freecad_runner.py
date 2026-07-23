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


def freecad_listo_para_step(*, prefer_verde: bool = True) -> bool:
    """
    True si hay FreeCAD.exe + macro (generador_verde / batch) usables.
    Sin conflicto con el Suite: FreeCAD corre en proceso aparte (LGPL).
    """
    return bool(
        _resolve_freecad_executable()
        and _resolve_macro_script(prefer_verde=prefer_verde)
    )


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


def _cad_base_from_dxf(dxf_path: str) -> str:
    name = os.path.splitext(os.path.basename(dxf_path))[0]
    idx = name.upper().find("W.O.")
    if idx != -1:
        return name[idx:].strip()
    return name


def _cad_extension(export_format: str = "step") -> str:
    fmt = str(export_format or "step").strip().lower()
    return ".igs" if fmt in ("iges", "igs") else ".step"


def _cad_path_for_dxf(dxf_path: str, out_folder: str, export_format: str = "step") -> str:
    ext = _cad_extension(export_format)
    return os.path.join(out_folder, f"{_cad_base_from_dxf(dxf_path)}{ext}")


def _step_base_from_dxf(dxf_path: str) -> str:
    return _cad_base_from_dxf(dxf_path)


def _step_path_for_dxf(dxf_path: str, step_folder: str) -> str:
    return _cad_path_for_dxf(dxf_path, step_folder, "step")


def _cad_is_current(dxf_path: str, cad_path: str) -> bool:
    if not os.path.isfile(cad_path):
        return False
    try:
        if os.path.getsize(cad_path) < 512:
            return False
        return os.path.getmtime(cad_path) >= os.path.getmtime(dxf_path)
    except OSError:
        return False


def _step_is_current(dxf_path: str, step_path: str) -> bool:
    return _cad_is_current(dxf_path, step_path)


def _timeout_for_dxf(dxf_path: str) -> float:
    """Timeout por DXF según tamaño (barras cobre densas pueden tardar >30 min)."""
    try:
        kb = max(1.0, os.path.getsize(dxf_path) / 1024.0)
    except OSError:
        kb = 100.0
    # Mín 15 min; ~4 s/KB; tope 3 h por archivo.
    return max(900.0, min(10800.0, 300.0 + kb * 4.0))


def _pendientes_cad(
    dxf_files: list[str],
    out_folder: str,
    export_format: str = "step",
) -> list[str]:
    pending: list[str] = []
    for dxf_path in dxf_files:
        cad_path = _cad_path_for_dxf(dxf_path, out_folder, export_format)
        if not _cad_is_current(dxf_path, cad_path):
            pending.append(dxf_path)
    return pending


def _pendientes_step(dxf_files: list[str], step_folder: str) -> list[str]:
    return _pendientes_cad(dxf_files, step_folder, "step")


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
    export_format: str = "step",
    dxf_filter=None,
) -> bool:
    cad_fmt = str(export_format or "step").strip().lower()
    cad_ext = _cad_extension(cad_fmt)
    cad_label = "IGES" if cad_ext == ".igs" else "STEP"
    from freecad_export_units import resolve_export_linear_unit, resolve_geometry_scale

    linear_unit = resolve_export_linear_unit(cad_fmt)
    geom_scale = resolve_geometry_scale(float(getattr(config, "FREECAD_SCALE", 1.0)), cad_fmt, linear_unit)

    def snapshot_cad(folder: str):
        data = {}
        folder = os.path.normpath(folder)
        if not folder or not os.path.isdir(folder):
            return data

        for path in glob.glob(os.path.join(folder, f"*{cad_ext}")):
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
    cmd: list[str] = []

    with open(log_path, "a", encoding="utf-8") as f:
        def _log(msg: str) -> None:
            f.write(msg)
            f.flush()

        def _prepare_import_paths(dxf_path: str) -> tuple[str, str | None, str]:
            try:
                from modules.dxf_slim_for_freecad import prepare_dxf_for_freecad

                import_path, mark_json, note = prepare_dxf_for_freecad(dxf_path, log_dir)
                return import_path, mark_json, note
            except Exception as exc:
                return dxf_path, None, f"slim omitido: {exc}"

        def _run_single_dxf(
            dxf_path: str,
            env_base: dict,
            *,
            append_log: bool,
            timeout_sec: float,
        ) -> tuple[bool, str]:
            env = dict(env_base)
            import_path, mark_json, slim_note = _prepare_import_paths(dxf_path)
            env["FREECAD_DXF_SINGLE"] = dxf_path
            env["FREECAD_DXF_IMPORT"] = import_path
            if mark_json:
                env["FREECAD_MARK_JSON"] = mark_json
                env["FREECAD_PHASE_MODE"] = "PER_PIECE"
            else:
                env.pop("FREECAD_MARK_JSON", None)
                env.pop("FREECAD_PHASE_MODE", None)
            env["FREECAD_SKIP_EXISTING"] = "1"
            env["FREECAD_LOG_APPEND"] = "1" if append_log else "0"
            stdout_log = os.path.join(log_dir, "freecad_stdout.log")
            stderr_log = os.path.join(log_dir, "freecad_stderr.log")
            try:
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
            except subprocess.TimeoutExpired as e:
                return False, f"TIMEOUT tras {int(timeout_sec)}s: {e}"
            except Exception as e:
                return False, f"EXCEPCIÓN: {e}"

            cad_path = _cad_path_for_dxf(dxf_path, step_resuelta, cad_fmt)
            if _cad_is_current(dxf_path, cad_path):
                return True, f"OK (rc={proc.returncode})"
            if proc.returncode != 0:
                return False, f"FreeCAD rc={proc.returncode}"
            return False, f"sin {cad_label} válido al terminar"

        _log(f"\n--- INICIANDO CONVERSIÓN {cad_label} ({origen}) ---\n")
        f.write(f"Ejecutable: {ruta_exe}\n")
        f.write(f"Macro: {ruta_macro}\n")
        f.write(f"Modo: 1 FreeCAD por DXF (reanuda {cad_label} existentes)\n")
        f.write(f"Formato 3D: {cad_fmt}\n")
        f.write(f"Unidades export: {linear_unit}\n")
        f.write(f"FREECAD_SCALE: {geom_scale}\n")
        f.write(f"DXF folder (solicitado): {dxf_folder}\n")
        f.write(f"DXF folder (resuelto): {dxf_resuelta}\n")
        f.write(f"{cad_label} folder (solicitado): {step_folder}\n")
        f.write(f"{cad_label} folder (resuelto): {step_resuelta}\n")

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

        cmd = [ruta_exe, ruta_macro]

        if not os.path.isdir(dxf_resuelta):
            f.write("ERROR FATAL: La carpeta DXF no existe o no es accesible.\n")
            f.write("Variantes probadas:\n")
            for cand in _variantes_ruta_acceso(dxf_folder):
                f.write(f" - {cand} (existe={os.path.isdir(cand)})\n")
            return False

        dxf_files = _esperar_dxfs_en_carpeta(dxf_resuelta)
        n_dxf = len(dxf_files)
        _log(f"DXF detectados: {n_dxf}\n")
        for path in dxf_files[:20]:
            f.write(f" - {path}\n")
        if len(dxf_files) > 20:
            f.write(f" ... (+{len(dxf_files) - 20} más)\n")

        if not dxf_files:
            f.write("ERROR FATAL: No se encontraron DXF para procesar.\n")
            return False

        try:
            if not os.path.isdir(step_resuelta):
                os.makedirs(step_resuelta, exist_ok=True)
                f.write(f"{cad_label} folder asegurada/creada desde Python.\n")
            else:
                f.write(f"{cad_label} folder ya existe.\n")
        except Exception as e:
            f.write(f"[WARN] No se pudo asegurar la carpeta {cad_label} desde Python: {e}\n")
            f.write("[WARN] Se continuará y se dejará que FreeCAD intente escribir directamente.\n")

        before_snapshot = snapshot_cad(step_resuelta)
        f.write(f"{cad_label} antes: {len(before_snapshot)}\n")

        env_base = os.environ.copy()
        env_base["FREECAD_DXF_IN"] = dxf_resuelta
        env_base["FREECAD_STEP_OUT"] = step_resuelta
        env_base["FREECAD_EXPORT_FORMAT"] = cad_fmt
        env_base["FREECAD_EXPORT_LINEAR_UNIT"] = linear_unit
        env_base["FREECAD_SCALE"] = str(geom_scale)
        env_base["FREECAD_LOG_DIR"] = log_dir
        env_base["FREECAD_LOG_PATH"] = os.path.join(log_dir, "freecad_macro.log")
        env_base["FREECAD_THK_MM"] = str(thickness_mm)
        env_base["FREECAD_ORIGIN"] = origen
        env_base["FREECAD_OFFSET_X"] = str(off_x)
        env_base["FREECAD_OFFSET_Y"] = str(off_y)
        env_base["FREECAD_OFFSET_Z"] = str(off_z)
        if material:
            env_base["FREECAD_MATERIAL"] = str(material).strip().upper()

        _log(f"Comando lanzado: {' '.join(cmd)}\n")

        candidatos = list(dxf_files)
        if callable(dxf_filter):
            candidatos = [p for p in candidatos if dxf_filter(p)]
        n_candidatos = len(candidatos)
        ya_vigentes = n_candidatos - len(_pendientes_cad(candidatos, step_resuelta, cad_fmt))
        if ya_vigentes:
            _log(f"{cad_label} vigentes (se omiten): {ya_vigentes}/{n_candidatos}\n")

        macro_append = ya_vigentes > 0 or os.path.isfile(
            env_base.get("FREECAD_LOG_PATH", "")
        )
        fallidos: list[str] = []

        for intento in range(1, max(1, int(max_intentos)) + 1):
            pending = _pendientes_cad(candidatos, step_resuelta, cad_fmt)
            if not pending:
                break
            if intento > 1:
                _log(
                    f"\n--- REINTENTO {cad_label} {intento}/{max_intentos} "
                    f"({len(pending)} pendientes) ---\n"
                )
                time.sleep(1.0)
            else:
                _log(f"Pendientes de convertir: {len(pending)}/{n_candidatos}\n")

            for idx, dxf_path in enumerate(pending):
                nombre = os.path.basename(dxf_path)
                timeout_sec = _timeout_for_dxf(dxf_path)
                import_path, mark_json, slim_note = _prepare_import_paths(dxf_path)
                slim_info = f" | import={os.path.basename(import_path)}"
                if mark_json:
                    slim_info += f" | marks_json={os.path.basename(mark_json)}"
                _log(
                    f"\n[{idx + 1}/{len(pending)}] {nombre} "
                    f"(timeout {int(timeout_sec)} s){slim_info}\n"
                )
                if slim_note:
                    _log(f"  slim: {slim_note}\n")
                ok_one, detalle = _run_single_dxf(
                    dxf_path,
                    env_base,
                    append_log=macro_append,
                    timeout_sec=timeout_sec,
                )
                macro_append = True
                if ok_one:
                    _log(f"  -> {detalle}\n")
                else:
                    _log(f"  -> FAIL: {detalle}\n")
                    if dxf_path not in fallidos:
                        fallidos.append(dxf_path)

        pending_final = _pendientes_cad(candidatos, step_resuelta, cad_fmt)
        after_snapshot = snapshot_cad(step_resuelta)
        nuevos, actualizados = diff_steps(before_snapshot, after_snapshot)

        _log(f"\n{cad_label} después: {len(after_snapshot)}/{n_candidatos}\n")
        _log(f"{cad_label} nuevos: {len(nuevos)}\n")
        _log(f"{cad_label} actualizados: {len(actualizados)}\n")
        if pending_final:
            _log(f"{cad_label} faltantes ({len(pending_final)}):\n")
            for dxf_path in pending_final:
                _log(f" - {os.path.basename(dxf_path)}\n")

        ok = len(pending_final) == 0
        if ok and cad_fmt == "step" and str(material or "").strip().upper() in ("CU", "COBRE", "COPPER"):
            try:
                from modules.cobre_step_audit import audit_macro_log_for_losses

                macro_log = env_base.get("FREECAD_LOG_PATH") or os.path.join(
                    log_dir, "freecad_macro.log"
                )
                issues = audit_macro_log_for_losses(macro_log, material=material)
                for issue in issues:
                    _log(f"ERROR AUDIT COBRE: {issue}\n")
                if issues:
                    ok = False
            except Exception as e:
                _log(f"[WARN] No se pudo auditar piezas STEP cobre: {e}\n")

        if ok:
            _log("RESULTADO FINAL: OK\n")
            return True

        if pending_final:
            _log(f"RESULTADO FINAL: FAIL ({cad_label} incompletos tras reintentos)\n")
        else:
            _log(f"RESULTADO FINAL: FAIL (auditoría o conteo {cad_label})\n")
        return False