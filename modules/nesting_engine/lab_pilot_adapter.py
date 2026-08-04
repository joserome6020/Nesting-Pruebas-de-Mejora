"""Adaptador aislado del piloto rápido empaquetado.

El binario se compila desde la ruta timeline validada del LAB, pero se instala
en ``modules/nesting_engine`` y no depende del ``.pyd`` del simulador.
"""
from __future__ import annotations

import copy
from collections import Counter


class LabPilotUnavailableError(RuntimeError):
    """El binario aislado del piloto no está listo para una prueba."""


_PILOT_MODULE = None


def _load_pilot_module():
    global _PILOT_MODULE
    if _PILOT_MODULE is not None:
        return _PILOT_MODULE
    try:
        from . import algorithm_cpp_lab_pilot
    except ImportError as exc:
        raise LabPilotUnavailableError(
            "Piloto rápido no compilado. Ejecuta "
            "modules\\nesting_engine\\build_lab_pilot_engine.ps1."
        ) from exc
    if hasattr(algorithm_cpp_lab_pilot, "empaquetar_una_hoja_timeline"):
        _PILOT_MODULE = algorithm_cpp_lab_pilot
        return _PILOT_MODULE
    raise LabPilotUnavailableError(
        "algorithm_cpp_lab_pilot.pyd no expone la ruta timeline requerida."
    )


def is_ready() -> bool:
    try:
        _load_pilot_module()
        return True
    except Exception:
        return False


def pack_one_sheet(
    pieces: list[dict],
    *,
    plate_w_mm: float,
    plate_h_mm: float,
    kerf_in: float,
    margin_in: float,
    opt: str,
    corner: str,
    mc_iterations: int = 1,
    limite_poly=None,
) -> tuple[dict, list]:
    """Empaqueta una placa madre o un retazo con el contrato de producción."""
    from .algorithm_bridge import (
        _assemble_pack_result,
        _piece_to_native,
        _rings_from_shapely_polygon,
    )

    module = _load_pilot_module()
    native_pieces = [_piece_to_native(piece) for piece in pieces or []]
    limite_rings = (
        _rings_from_shapely_polygon(limite_poly)
        if limite_poly is not None
        else None
    )
    # La ruta timeline es el kernel rápido validado por el simulador. El
    # empaquetador MC general explora de más para este lote y no es candidato
    # de tiempo real.
    raw = module.empaquetar_una_hoja_timeline(
        native_pieces,
        float(plate_w_mm),
        float(plate_h_mm),
        float(kerf_in),
        float(margin_in),
        str(opt),
        str(corner),
        limite_rings,
        max(1, min(int(mc_iterations or 1), 4)),
    )
    sheet, _ = _assemble_pack_result(
        dict(raw.get("hoja") or {}),
        list(raw.get("restos") or []),
        pieces,
    )
    if isinstance(raw.get("cuda_screen"), dict):
        sheet["cuda_screen"] = dict(raw.get("cuda_screen") or {})
    # El contrato timeline identifica la continuidad por las piezas realmente
    # colocadas. Es más fiable que la lista `restos` nativa cuando hay muchas
    # instancias con el mismo nombre.
    placed = Counter(str(piece.get("nombre") or "") for piece in sheet.get("piezas") or [])
    rests = []
    for piece in pieces or []:
        name = str(piece.get("nombre") or "")
        if placed.get(name, 0) > 0:
            placed[name] -= 1
        else:
            rests.append(copy.deepcopy(piece))
            
    from .ai_heuristic import record_telemetry, get_last_seed_info
    eff = float(sheet.get("eficiencia") or 0.0)

    # --- VENOM POLISHER AI ---
    import os
    engine_id = os.environ.get("ARGA_MOTOR_NESTING", "svgnest_ultra")
    try:
        from . import venom_ai
        venom_ai.apply_smart_polisher(sheet, engine_id)
    except Exception as e:
        import traceback
        with open(r"c:\Proyectos\New Arga Nesting Suite\_logs\venom_debug.log", "a") as f:
            f.write(f"ERROR EN VENOM LAB PILOT: {e}\n{traceback.format_exc()}\n")

    seed_info = get_last_seed_info(engine_id)
    if "venom_reward" in sheet:
        record_telemetry(
            sheet.get("piezas") or [],
            eff,
            engine_id,
            compactness_pre=float(sheet.get("venom_compactness_pre") or 0.0),
            compactness_post=float(sheet.get("venom_compactness_post") or 0.0),
            seed_policy=seed_info.get("policy"),
            nest_reward=float(eff or 0.0) + float(sheet.get("venom_reward") or 0.0),
        )
    elif eff > 0.5:
        record_telemetry(
            sheet.get("piezas") or [],
            eff,
            engine_id,
            seed_policy=seed_info.get("policy"),
            nest_reward=eff,
        )

    return sheet, rests
