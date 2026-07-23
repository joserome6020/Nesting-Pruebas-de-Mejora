"""Descubrimiento de carpetas/archivos STEP del job de nesting (local / remoto, WO / SWO)."""
from __future__ import annotations

import os
import re
from pathlib import Path

_STEP_REL_DIRS = (
    ("NESTEOS DE COBRE", "STEP"),
    ("ROBOT LASER + MINI NEST", "STEP", "Cama A"),
    ("ROBOT LASER + MINI NEST", "STEP", "Cama B"),
    ("ROBOT PLASMA", "STEP", "Cama A"),
    ("ROBOT PLASMA", "STEP", "Cama B"),
)

_WO_NAME_RE = re.compile(r"^W\.O\.\s*\d+", re.IGNORECASE)
_SWO_NAME_RE = re.compile(r"^S\.W\.O\s+\d+", re.IGNORECASE)


def step_dirs_bajo_export_cad(ruta_export_cad: str | Path) -> list[Path]:
    """
    Rutas STEP bajo ARGA MODEL CORE (solo familias de exportación):
    - NESTING/<familia>/STEP[/Cama A|B]
    Sin espejo en 3D NESTING.
    """
    root = Path(ruta_export_cad)
    nest = root / "NESTING"
    dirs: list[Path] = []
    for parts in _STEP_REL_DIRS:
        dirs.append(nest.joinpath(*parts))
    return [d for d in dirs if d.is_dir()]


def listar_steps_en_dirs(dirs: list[Path], *, limit: int = 500) -> list[Path]:
    """Lista .step/.stp únicos, más recientes primero."""
    seen_path: set[str] = set()
    files: list[Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        local_seen: set[str] = set()
        for pat in ("*.step", "*.stp"):
            for p in d.glob(pat):
                key = os.path.normcase(str(p.resolve())) if p.exists() else os.path.normcase(str(p))
                if key in local_seen or key in seen_path:
                    continue
                if not p.is_file() or p.stat().st_size < 64:
                    continue
                local_seen.add(key)
                seen_path.add(key)
                files.append(p)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[: max(1, int(limit))]


def steps_bajo_export_cad(ruta_export_cad: str | Path, *, limit: int = 500) -> list[Path]:
    return listar_steps_en_dirs(step_dirs_bajo_export_cad(ruta_export_cad), limit=limit)


def _norm_key(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(p or "")))


def _job_name_from_app(app) -> str:
    job = str(getattr(app, "job_activo", "") or "").strip()
    if job and not job.upper().startswith("SWO"):
        return job
    return ""


def _ruta_dxf_ref(app) -> str:
    for pieza in getattr(app, "datos_partes_actuales", []) or []:
        if len(pieza) > 5 and pieza[5]:
            return str(pieza[5]).strip()
    return ""


def _job_dir_desde_dxf(ruta_dxf: str) -> Path | None:
    try:
        from interface.qt.export_paths import resolver_carpeta_job_desde_ruta_dxf

        job_dir = resolver_carpeta_job_desde_ruta_dxf(ruta_dxf)
        return Path(job_dir) if job_dir and os.path.isdir(job_dir) else None
    except Exception:
        return None


def _buscar_job_en_raiz(raiz: str | Path, nombre_job: str) -> Path | None:
    """Busca carpeta del job bajo servidor (producto/cliente/job) o Nesteos Locales."""
    nombre = str(nombre_job or "").strip()
    raiz_p = Path(raiz)
    if not nombre or not raiz_p.is_dir():
        return None
    target = nombre.casefold()

    def _es_job(p: Path) -> bool:
        return p.is_dir() and p.name.casefold() == target and (p / "MODEL CORE FILES").is_dir()

    try:
        for nivel1 in raiz_p.iterdir():
            if not nivel1.is_dir():
                continue
            if _es_job(nivel1):
                return nivel1
            for nivel2 in nivel1.iterdir():
                if not nivel2.is_dir():
                    continue
                if _es_job(nivel2):
                    return nivel2
                for nivel3 in nivel2.iterdir():
                    if _es_job(nivel3):
                        return nivel3
    except OSError:
        pass
    return None


def _mcf_desde_job_dir(job_dir: Path | None) -> Path | None:
    if job_dir is None:
        return None
    mcf = job_dir / "MODEL CORE FILES"
    return mcf if mcf.is_dir() else None


def resolver_model_core_files(app, *, modo_servidor: bool) -> tuple[Path | None, str]:
    """
    Resuelve MODEL CORE FILES del job actual (local o remoto).
    Devuelve (ruta_mcf | None, nota).
    """
    notas: list[str] = []
    job = _job_name_from_app(app)
    dxf = _ruta_dxf_ref(app)

    if modo_servidor:
        import config

        if job:
            ruta_p = _buscar_job_en_raiz(config.RUTA_SERVIDOR_RAIZ, job)
            mcf = _mcf_desde_job_dir(ruta_p)
            if mcf:
                return mcf, f"remoto | job={job} | {mcf}"
            notas.append(f"job remoto no encontrado: {job}")

        if dxf:
            jd = _job_dir_desde_dxf(dxf)
            mcf = _mcf_desde_job_dir(jd)
            if mcf:
                return mcf, f"remoto | desde DXF | {mcf}"
    else:
        from interface.qt.export_paths import desktop_nesteos_locales, relativa_desde_cliente

        base_local = desktop_nesteos_locales()
        if job:
            jd = _buscar_job_en_raiz(base_local, job)
            mcf = _mcf_desde_job_dir(jd)
            if mcf:
                return mcf, f"local | job={job} | {mcf}"
            notas.append(f"job local no encontrado: {job}")

        if dxf:
            jd = _job_dir_desde_dxf(dxf)
            if jd is not None:
                try:
                    rel = relativa_desde_cliente(str(jd))
                    mcf = Path(base_local) / rel / "MODEL CORE FILES"
                    if mcf.is_dir():
                        return mcf, f"local | espejo DXF | {mcf}"
                except Exception:
                    pass
            # Si el DXF ya vive bajo Nesteos Locales
            try:
                jd2 = _job_dir_desde_dxf(dxf)
                mcf = _mcf_desde_job_dir(jd2)
                if mcf and _norm_key(mcf).startswith(_norm_key(base_local)):
                    return mcf, f"local | desde DXF | {mcf}"
            except Exception:
                pass

    # Fallback: última exportación / workspace
    ultima = str(getattr(app, "ultima_ruta_export_cad", "") or "").strip()
    if ultima:
        p = Path(ultima)
        for parent in [p, *p.parents]:
            if parent.name.upper() == "MODEL CORE FILES" and parent.is_dir():
                return parent, f"fallback última export | {parent}"

    ws = str(getattr(app, "ruta_workspace_actual", "") or getattr(app, "ultimo_arganest", "") or "").strip()
    if ws:
        p = Path(ws)
        for parent in [p.parent, *p.parents]:
            if parent.name.upper() == "MODEL CORE FILES" and parent.is_dir():
                return parent, f"fallback workspace | {parent}"

    return None, " | ".join(notas) if notas else "sin MODEL CORE FILES"


def listar_wo_con_steps(mcf: str | Path, *, limit_por_wo: int = 400) -> list[dict]:
    """
    Lista carpetas W.O. * bajo MODEL CORE FILES que tengan al menos un STEP.
    Cada ítem: {nombre, export_cad, steps, n_steps}
    """
    root = Path(mcf)
    out: list[dict] = []
    if not root.is_dir():
        return out
    try:
        hijos = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for wo_dir in hijos:
        if not wo_dir.is_dir() or not _WO_NAME_RE.match(wo_dir.name):
            continue
        export_cad = wo_dir / "ARGA MODEL CORE"
        if not export_cad.is_dir():
            continue
        steps = steps_bajo_export_cad(export_cad, limit=limit_por_wo)
        if not steps:
            continue
        out.append(
            {
                "nombre": wo_dir.name,
                "export_cad": export_cad,
                "steps": steps,
                "n_steps": len(steps),
            }
        )
    # Más recientes primero (mtime del STEP más nuevo)
    out.sort(
        key=lambda it: max((s.stat().st_mtime for s in it["steps"]), default=0.0),
        reverse=True,
    )
    return out


def _raices_maxima_optimizacion(*, modo_servidor: bool) -> list[Path]:
    roots: list[Path] = []
    if modo_servidor:
        import config

        texto = str(config.RUTA_SERVIDOR_RAIZ or "")
        if "ARGA METALS CORPORATE SYSTEM" in texto:
            raiz_arga = texto.split("ARGA METALS CORPORATE SYSTEM")[0]
            roots.append(Path(raiz_arga) / "Máxima Optimización")
        # UNC típico del despachador (Grupo Arga Metals)
        roots.append(Path(r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals\Máxima Optimización"))
        roots.append(Path(r"\\192.168.2.80\Grupo Arga Metals\Máxima Optimización"))
    else:
        from interface.qt.export_paths import desktop_nesteos_locales

        roots.append(Path(desktop_nesteos_locales()) / "Máxima Optimización")
    # Únicos existentes
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = _norm_key(r)
        if key in seen:
            continue
        seen.add(key)
        if r.is_dir():
            out.append(r)
    return out


def listar_swo_con_steps(*, modo_servidor: bool, limit_por_swo: int = 400) -> list[dict]:
    """
    Lista S.W.O * bajo Máxima Optimización con al menos un STEP
    (export interactivo o despachador_nocturno).
    """
    out: list[dict] = []
    for root in _raices_maxima_optimizacion(modo_servidor=modo_servidor):
        try:
            hijos = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for swo_dir in hijos:
            if not swo_dir.is_dir() or not _SWO_NAME_RE.match(swo_dir.name):
                continue
            export_cad = swo_dir / "ARGA MODEL CORE"
            if not export_cad.is_dir():
                continue
            steps = steps_bajo_export_cad(export_cad, limit=limit_por_swo)
            if not steps:
                continue
            out.append(
                {
                    "nombre": swo_dir.name,
                    "export_cad": export_cad,
                    "steps": steps,
                    "n_steps": len(steps),
                    "raiz": root,
                }
            )
    out.sort(
        key=lambda it: max((s.stat().st_mtime for s in it["steps"]), default=0.0),
        reverse=True,
    )
    # Deduplicar por nombre (preferir el primero = más reciente)
    seen: set[str] = set()
    uniq: list[dict] = []
    for it in out:
        key = it["nombre"].casefold()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq


def descubrir_steps_para_app(app, *, limit: int = 400) -> tuple[list[Path], str]:
    """
    Busca STEPs del último export / job (compat).
    Preferir listar_wo_con_steps / listar_swo_con_steps en la UI nueva.
    """
    dirs: list[Path] = []
    notas: list[str] = []

    ultima = str(getattr(app, "ultima_ruta_export_cad", "") or "").strip()
    if ultima and os.path.isdir(ultima):
        found = step_dirs_bajo_export_cad(ultima)
        dirs.extend(found)
        notas.append(f"último export: {ultima}")

    ws = str(getattr(app, "ruta_workspace_actual", "") or getattr(app, "ultimo_arganest", "") or "").strip()
    if ws:
        p = Path(ws)
        for parent in [p.parent, *p.parents]:
            name = parent.name.upper()
            if name == "ARGA MODEL CORE" or (parent / "NESTING").is_dir():
                extra = step_dirs_bajo_export_cad(parent)
                for d in extra:
                    if d not in dirs:
                        dirs.append(d)
                if extra:
                    notas.append(f"workspace: {parent}")
                break

    files = listar_steps_en_dirs(dirs, limit=limit)
    ctx = " | ".join(notas) if notas else "sin carpeta de export reciente"
    return files, ctx
