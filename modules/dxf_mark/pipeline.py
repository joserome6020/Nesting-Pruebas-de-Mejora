"""Presets de marcaje: Nesting Suite (MARK) vs AutoDXF (IV_MARK_SURFACE_BACK)."""

from __future__ import annotations

from pathlib import Path

from modules.dxf_mark.inject import (
    AUTODXF_MARK_LAYER,
    DEFAULT_CLEARANCE_IN,
    DEFAULT_TEXT_HEIGHT_IN,
    MARK_LAYER,
    InjectResult,
    inject_mark_into_dxf,
    tiene_marcaje_stick,
)


def aplicar_marcaje_nesting(
    dxf_path: str | Path,
    *,
    text_height_in: float = DEFAULT_TEXT_HEIGHT_IN,
    clearance_in: float = DEFAULT_CLEARANCE_IN,
    skip_if_present: bool = True,
    origen_ya_marcado: bool | None = None,
) -> InjectResult:
    """
    Inyecta marcaje stick en un DXF ya procesado del Nesting Suite.
    Capa destino: MARK. Sobrescribe el mismo archivo.
    Si ya tiene marcaje stick (o origen_ya_marcado=True), no reinyecta.
    """
    path = Path(dxf_path)
    if origen_ya_marcado or (skip_if_present and tiene_marcaje_stick(path)):
        from modules.dxf_mark.inject import mark_text_from_dxf_path

        return InjectResult(
            input_path=path,
            output_path=path,
            mark_text=mark_text_from_dxf_path(path),
            height_du=float(text_height_in),
            components_marked=0,
            components_skipped=0,
            already_marked=True,
        )
    return inject_mark_into_dxf(
        path,
        path,
        text_height_in=text_height_in,
        clearance_in=clearance_in,
        mark_layer=MARK_LAYER,
        replace_existing_mark=False,
        skip_if_present=skip_if_present,
    )


def aplicar_marcaje_autodxf(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    text_height_in: float = DEFAULT_TEXT_HEIGHT_IN,
    clearance_in: float = DEFAULT_CLEARANCE_IN,
    replace_existing_mark: bool = False,
    skip_if_present: bool = True,
) -> InjectResult:
    """
    Inyecta marcaje stick para DXF crudos de AutoDXF/Inventor.
    Capa destino: IV_MARK_SURFACE_BACK.
    Si ya está marcado por el script, no vuelve a marcar.
    """
    return inject_mark_into_dxf(
        input_path,
        output_path,
        text_height_in=text_height_in,
        clearance_in=clearance_in,
        mark_layer=AUTODXF_MARK_LAYER,
        replace_existing_mark=replace_existing_mark,
        skip_if_present=skip_if_present,
    )
