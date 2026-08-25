"""Conversión DXF→STEP vía motor OCCT (Arga Nesting Suite), paridad de rutas FreeCAD."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

ProgressCb = Callable[..., None]


def _ensure_cad_engine():
    root = Path(__file__).resolve().parents[2]
    cad = root / "CAD (OCCT)"
    cad_s = str(cad)
    if cad_s not in sys.path:
        sys.path.insert(0, cad_s)
    from engine.dxf_to_step import (  # noqa: WPS433
        export_dxf_to_step_freecad_batch,
        export_dxf_to_step_robot_camas,
        thickness_mm_from_dxf_name,
    )

    return (
        export_dxf_to_step_freecad_batch,
        export_dxf_to_step_robot_camas,
        thickness_mm_from_dxf_name,
    )


def _step_path_for_dxf(dxf_path: str, out_dir: str) -> str:
    base = os.path.splitext(os.path.basename(dxf_path))[0]
    return os.path.join(out_dir, f"{base}.step")


def _convertir_carpeta_occt(
    etiqueta: str,
    dxf_paths: list[str],
    out_dir: str,
    *,
    thk_default_mm: float,
    material: str,
    off_x: float,
    off_y: float,
    off_z: float,
    origen: str | None = None,
    step_done_ref: list[int],
    step_total: int,
    progress_cb: ProgressCb | None = None,
) -> bool:
    """Convierte DXFs uno a uno. Devuelve True si todos OK (o carpeta vacía)."""
    if not dxf_paths:
        print(f"[STEP][OCCT][SKIP] {etiqueta}: sin DXF aplicables")
        return True

    export_fn, _robot_fn, thk_from_name = _ensure_cad_engine()
    os.makedirs(out_dir, exist_ok=True)
    print(f"[STEP][OCCT] {etiqueta}: {len(dxf_paths)} DXF -> {out_dir}")
    ok_all = True
    for dxf_path in dxf_paths:
        nombre = os.path.basename(dxf_path)
        out_step = _step_path_for_dxf(dxf_path, out_dir)
        thk = float(thk_from_name(nombre, default_mm=float(thk_default_mm)))
        if callable(progress_cb):
            try:
                progress_cb(
                    mensaje=f"OCCT STEP [{etiqueta}]: {nombre}",
                    step_done=int(step_done_ref[0]),
                    step_total=int(step_total),
                )
            except Exception:
                pass
        try:
            export_fn(
                dxf_path,
                out_step,
                thk_mm=thk,
                material=material,
                off_x=float(off_x),
                off_y=float(off_y),
                off_z=float(off_z),
                origen=origen,
                mark_mode="ENGRAVE",
                include_plate=False,
            )
            if not os.path.isfile(out_step) or os.path.getsize(out_step) < 64:
                raise RuntimeError(f"STEP vacío o ausente: {out_step}")
            print(f"[STEP][OCCT] OK {nombre} -> {os.path.basename(out_step)}")
        except Exception as exc:
            ok_all = False
            print(f"[STEP][OCCT][ERR] {etiqueta} / {nombre}: {exc}")
        step_done_ref[0] = int(step_done_ref[0]) + 1
        if callable(progress_cb):
            try:
                progress_cb(
                    mensaje=f"OCCT STEP [{etiqueta}]: {nombre}",
                    step_done=int(step_done_ref[0]),
                    step_total=int(step_total),
                )
            except Exception:
                pass
    return ok_all


def _convertir_robot_ab_occt(
    etiqueta_base: str,
    dxf_paths: list[str],
    out_dir_a: str,
    out_dir_b: str,
    *,
    thk_default_mm: float,
    material: str,
    step_done_ref: list[int],
    step_total: int,
    progress_cb: ProgressCb | None = None,
) -> bool:
    """
    Robot A+B: un build OCCT + ancla TR/BR (paridad FreeCAD).
    Cuenta 2 STEP en el progreso por cada DXF.
    """
    if not dxf_paths:
        print(f"[STEP][OCCT][SKIP] {etiqueta_base}: sin DXF aplicables")
        return True

    _export_fn, robot_fn, thk_from_name = _ensure_cad_engine()
    out_a = os.path.normpath(str(out_dir_a or "").strip())
    out_b = os.path.normpath(str(out_dir_b or "").strip())
    os.makedirs(out_a, exist_ok=True)
    os.makedirs(out_b, exist_ok=True)
    print(
        f"[STEP][OCCT] {etiqueta_base} A+B: {len(dxf_paths)} DXF "
        f"(1 build → 2 camas) -> A={out_a} | B={out_b}"
    )
    ok_all = True
    for dxf_path in dxf_paths:
        nombre = os.path.basename(dxf_path)
        step_a = _step_path_for_dxf(dxf_path, out_a)
        step_b = _step_path_for_dxf(dxf_path, out_b)
        thk = float(thk_from_name(nombre, default_mm=float(thk_default_mm)))
        if callable(progress_cb):
            try:
                progress_cb(
                    mensaje=f"OCCT STEP [{etiqueta_base} A+B]: {nombre}",
                    step_done=int(step_done_ref[0]),
                    step_total=int(step_total),
                )
            except Exception:
                pass
        try:
            info = robot_fn(
                dxf_path,
                step_a,
                step_b,
                thk_mm=thk,
                material=material,
                mark_mode="ENGRAVE",
            )
            if not os.path.isfile(step_a) or os.path.getsize(step_a) < 64:
                raise RuntimeError(f"STEP A vacío: {step_a}")
            if not os.path.isfile(step_b) or os.path.getsize(step_b) < 64:
                raise RuntimeError(f"STEP B vacío: {step_b}")
            print(
                f"[STEP][OCCT] OK {nombre} A+B | "
                f"build={info.get('sec_build')}s "
                f"writeA={info.get('sec_write_a')}s "
                f"writeB={info.get('sec_write_b')}s "
                f"total={info.get('sec_total')}s"
            )
        except Exception as exc:
            ok_all = False
            print(f"[STEP][OCCT][ERR] {etiqueta_base} A+B / {nombre}: {exc}")
        # A y B cuentan como 2 unidades de progreso (paridad jobs previos)
        step_done_ref[0] = int(step_done_ref[0]) + 2
        if callable(progress_cb):
            try:
                progress_cb(
                    mensaje=f"OCCT STEP [{etiqueta_base} A+B]: {nombre}",
                    step_done=int(step_done_ref[0]),
                    step_total=int(step_total),
                )
            except Exception:
                pass
    return ok_all


def lanzar_occt_robotica(
    rutas: dict,
    thk: float,
    plasma_off: float,
    job_root: str = "",
    *,
    es_cobre: bool = False,
    cu_formato_por_dxf: dict[str, str] | None = None,
    progress_cb: ProgressCb | None = None,
) -> list[tuple[str, bool, str]]:
    """
    Misma matriz de destinos que lanzar_freecad_robotica.
    Overlay STEP_UNIVERSAL_SIN_CAMAS: 1 STEP/DXF acero sin ancla ni Cama A/B.
    Flujo normal: robot A/B; CAMA LASER solo DXF; cobre según formato 3D.
    """
    from .exporter import (
        RUTA_CAMA_LASER,
        RUTA_CAMA_LASER_12KW,
        RUTA_NESTEOS_COBRE,
        RUTA_ROBOT_LASER,
        RUTA_ROBOT_PLASMA,
        _cu_dxf_requiere_3d,
        _listar_dxfs_en_carpeta,
        _localizar_carpeta_dxf,
        step_universal_sin_camas_activo,
    )

    # plasma_off se conserva por firma/paridad FreeCAD; el DXF plasma ya
    # sale compensado en exportación y OCCT extruye la geometría tal cual.
    _ = plasma_off

    fmt_map = {str(k): str(v).lower() for k, v in (cu_formato_por_dxf or {}).items()}
    resultados: list[tuple[str, bool, str]] = []
    universal = step_universal_sin_camas_activo()

    from .step_export_prefs import step_enabled_for_label

    def _step_ok(label: str) -> bool:
        ok = step_enabled_for_label(label)
        if not ok:
            print(f"[STEP][OCCT][SKIP] {label}: desactivado en Configuración → STEPS")
        return ok

    def _fmt_for_dxf(path: str) -> str:
        nombre = os.path.basename(path)
        if nombre in fmt_map:
            return fmt_map[nombre]
        if es_cobre:
            return "dxf"
        return "step"

    def _dxfs(dxf_key: str) -> tuple[str, list[str]]:
        dxf_dir = _localizar_carpeta_dxf(
            rutas.get(dxf_key, ""),
            job_root,
            {
                "cama_laser_dxf": RUTA_CAMA_LASER,
                "nesteos_cobre_dxf": RUTA_NESTEOS_COBRE,
                "cama_laser_12kw_dxf": RUTA_CAMA_LASER_12KW,
                "robot_laser_dxf": RUTA_ROBOT_LASER,
                "robot_plasma_dxf": RUTA_ROBOT_PLASMA,
            }.get(dxf_key, ""),
        )
        return dxf_dir, _listar_dxfs_en_carpeta(dxf_dir)

    jobs: list[tuple] = []
    robot_jobs: list[tuple] = []

    if es_cobre and rutas.get("nesteos_cobre_dxf") and _step_ok("NESTEOS DE COBRE"):
        dxf_dir, candidatos = _dxfs("nesteos_cobre_dxf")
        if fmt_map:
            candidatos = [p for p in candidatos if _cu_dxf_requiere_3d(_fmt_for_dxf(p))]
            jobs.append(
                (
                    "NESTEOS DE COBRE STEP",
                    candidatos,
                    rutas.get("nesteos_cobre_step", ""),
                    "CU",
                    0.0,
                    0.0,
                    0.0,
                    None,  # cobre OCCT: sin ancla (comportamiento previo)
                    dxf_dir,
                )
            )
        else:
            print(
                "[STEP][OCCT][SKIP] NESTEOS DE COBRE: cobre solo DXF "
                "(todas las barras sin_gap / sin conversión 3D)"
            )

    if universal:
        print(
            "[STEP][OCCT] Overlay STEP_UNIVERSAL_SIN_CAMAS activo: "
            "todas las carpetas DXF acero → 1 STEP sin desfase de camas"
        )
        for etiqueta, dxf_key, step_key in (
            ("CAMA LASER", "cama_laser_dxf", "cama_laser_step"),
            ("CAMA LASER 12KW", "cama_laser_12kw_dxf", "cama_laser_12kw_step"),
            ("ROBOT LASER", "robot_laser_dxf", "robot_laser_step"),
            ("ROBOT PLASMA", "robot_plasma_dxf", "robot_plasma_step"),
        ):
            if not _step_ok(etiqueta):
                continue
            if not rutas.get(dxf_key) and not rutas.get(step_key):
                continue
            dxf_dir, candidatos = _dxfs(dxf_key)
            jobs.append(
                (
                    etiqueta,
                    list(candidatos),
                    rutas.get(step_key, ""),
                    "STEEL",
                    0.0,
                    0.0,
                    0.0,
                    "NONE",  # coords DXF 1:1
                    dxf_dir,
                )
            )
    else:
        # Robot: jobs emparejados A+B (1 build → 2 camas).
        if rutas.get("robot_laser_dxf") and _step_ok("ROBOT LASER"):
            dxf_dir, candidatos = _dxfs("robot_laser_dxf")
            robot_jobs.append(
                (
                    "ROBOT LASER",
                    list(candidatos),
                    rutas.get("robot_laser_step_A", ""),
                    rutas.get("robot_laser_step_B", ""),
                    "STEEL",
                    dxf_dir,
                )
            )

        if rutas.get("robot_plasma_dxf") and _step_ok("ROBOT PLASMA"):
            dxf_dir, candidatos = _dxfs("robot_plasma_dxf")
            robot_jobs.append(
                (
                    "ROBOT PLASMA",
                    list(candidatos),
                    rutas.get("robot_plasma_step_A", ""),
                    rutas.get("robot_plasma_step_B", ""),
                    "STEEL",
                    dxf_dir,
                )
            )

    step_total = sum(len(j[1]) for j in jobs) + sum(2 * len(j[1]) for j in robot_jobs)
    step_done_ref = [0]
    if callable(progress_cb):
        try:
            progress_cb(
                mensaje="Iniciando conversión 3D (Arga Nesting Suite / OCCT)…",
                step_done=0,
                step_total=step_total,
            )
        except Exception:
            pass

    for etiqueta, candidatos, out_dir, material, ox, oy, oz, origen, dxf_dir in jobs:
        ok = _convertir_carpeta_occt(
            etiqueta,
            candidatos,
            os.path.normpath(str(out_dir or "").strip()),
            thk_default_mm=float(thk),
            material=material,
            off_x=ox,
            off_y=oy,
            off_z=oz,
            origen=origen,
            step_done_ref=step_done_ref,
            step_total=step_total,
            progress_cb=progress_cb,
        )
        resultados.append((etiqueta, ok, dxf_dir))
        if not ok:
            print(
                f"[STEP][OCCT][WARN] {etiqueta}: conversión incompleta "
                f"(revisar log de consola)"
            )

    for etiqueta, candidatos, out_a, out_b, material, dxf_dir in robot_jobs:
        ok = _convertir_robot_ab_occt(
            etiqueta,
            candidatos,
            out_a,
            out_b,
            thk_default_mm=float(thk),
            material=material,
            step_done_ref=step_done_ref,
            step_total=step_total,
            progress_cb=progress_cb,
        )
        resultados.append((f"{etiqueta} A+B", ok, dxf_dir))
        if not ok:
            print(
                f"[STEP][OCCT][WARN] {etiqueta} A+B: conversión incompleta "
                f"(revisar log de consola)"
            )

    ok_total = sum(1 for _, ok, _ in resultados if ok)
    if resultados:
        print(f"[STEP][OCCT] Resumen: {ok_total}/{len(resultados)} conversiones exitosas")
    return resultados


def generar_steps_cobre_fuentes_occt(
    resultados: dict,
    job_root_dir: str,
    *,
    log_fn=None,
    progress_cb: ProgressCb | None = None,
) -> int:
    """STEP in-place junto a cada DXF fuente cobre (paridad FreeCAD)."""
    from .step_export_prefs import step_enabled_for_label

    _log = log_fn or (lambda _msg: None)
    if not step_enabled_for_label("NESTEOS DE COBRE"):
        _log("STEP cobre OCCT in-place: SKIP (desactivado en Configuración → STEPS)")
        return 0

    from modules.cobre_step_fuentes import (
        escribir_manifest_cobre,
        recolectar_fuentes_cobre_desde_resultados,
    )

    from .exporter import RUTA_NESTEOS_COBRE

    manifest = recolectar_fuentes_cobre_desde_resultados(resultados or {})
    fuentes = manifest.get("fuentes") or []
    if not fuentes:
        return 0

    dest_manifest = os.path.join(job_root_dir, RUTA_NESTEOS_COBRE)
    path = escribir_manifest_cobre(dest_manifest, manifest)
    _log(f"Manifiesto cobre (OCCT): {path} ({len(fuentes)} DXF fuente)")

    esp_in = float(manifest.get("espesor_in") or 0.25)
    thk_mm = esp_in * 25.4
    export_fn, _robot_fn, thk_from_name = _ensure_cad_engine()

    rutas_ok = 0
    total = 0
    pendientes: list[tuple[str, str]] = []
    for item in fuentes:
        if not isinstance(item, dict):
            continue
        ruta = os.path.normpath(str(item.get("ruta_dxf") or "").strip())
        if not ruta or not os.path.isfile(ruta):
            _log(f"[COBRE-FUENTE][OCCT][SKIP] DXF no accesible: {ruta or item.get('nombre', '?')}")
            continue
        out_step = _step_path_for_dxf(ruta, os.path.dirname(ruta))
        pendientes.append((ruta, out_step))
        total += 1

    for i, (ruta, out_step) in enumerate(pendientes, start=1):
        nombre = os.path.basename(ruta)
        thk = float(thk_from_name(nombre, default_mm=float(thk_mm)))
        if callable(progress_cb):
            try:
                progress_cb(mensaje=f"OCCT STEP cobre fuente ({i}/{total}): {nombre}")
            except Exception:
                pass
        try:
            export_fn(
                ruta,
                out_step,
                thk_mm=thk,
                material="CU",
                mark_mode="ENGRAVE",
                include_plate=False,
            )
            if os.path.isfile(out_step) and os.path.getsize(out_step) >= 64:
                rutas_ok += 1
                _log(f"[COBRE-FUENTE][OCCT] OK {nombre}")
            else:
                _log(f"[COBRE-FUENTE][OCCT][ERR] STEP vacío: {out_step}")
        except Exception as exc:
            _log(f"[COBRE-FUENTE][OCCT][ERR] {nombre}: {exc}")

    _log(f"STEP cobre in-place (OCCT): {rutas_ok}/{total} DXF fuente")
    return rutas_ok
