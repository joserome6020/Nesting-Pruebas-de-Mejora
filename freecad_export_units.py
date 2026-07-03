"""Helpers for FreeCAD STEP/IGES export unit metadata (OCCT + prefs)."""
from __future__ import annotations

MM_PER_INCH = 25.4


def resolve_export_linear_unit(export_format: str, explicit: str = "") -> str:
    unit = str(explicit or "").strip().upper()
    if unit in ("MM", "IN", "INCH", "INCHES"):
        return "MM" if unit == "MM" else "IN"
    fmt = str(export_format or "step").strip().lower()
    return "MM" if fmt in ("iges", "igs") else "IN"


def resolve_geometry_scale(base_scale: float, export_format: str, linear_unit: str) -> float:
    """
  Escala lineal aplicada al importar wires antes de extruir.

  - STEP (in): escala base (típ. 1.0) — sin cambio.
  - IGES (mm): si la geometría base está en pulgadas, multiplica ×25.4.
    Con nest DXF en mm (INSUNITS=4) y escala base 1.0, IGES queda en mm sin factor extra.
    """
    scale = float(base_scale or 1.0)
    if resolve_export_linear_unit(export_format) != "MM":
        return scale
    model_unit = str(
        __import__("os").getenv("FREECAD_MODEL_LINEAR_UNIT", "mm") or "mm"
    ).strip().lower()
    if model_unit in ("in", "inch", "inches"):
        return scale * MM_PER_INCH
    return scale


def configure_export_units_in_freecad(app, export_format: str, explicit_unit: str = "") -> str:
    """
    Configura prefs de FreeCAD + OCCT antes de ImportGui.export.
    Devuelve la unidad lineal efectiva ('MM' o 'IN').
    """
    unit = resolve_export_linear_unit(export_format, explicit_unit)
    is_iges = str(export_format or "").strip().lower() in ("iges", "igs")
    occt_unit = "MM" if unit == "MM" else "IN"

    try:
        h_part = app.ParamGet("User parameter:BaseApp/Preferences/Mod/Part")
        if is_iges:
            h_part.SetInt("UnitIges", 0)
            try:
                h_iges = h_part.GetGroup("IGES")
                h_iges.SetInt("Unit", 0)
            except Exception:
                pass
        else:
            h_part.SetInt("Unit", 2)
            try:
                h_part.SetInt("UnitStep", 2)
            except Exception:
                pass
    except Exception as exc:
        print(f"WARN Part unit prefs: {exc}")

    _occt_set_write_unit("iges" if is_iges else "step", occt_unit)
    print(f"Unidades export {('IGES' if is_iges else 'STEP')}: {unit}")
    return unit


def _occt_set_write_unit(kind: str, unit: str) -> bool:
    key = f"write.{kind}.unit"
    values = [unit]
    if unit == "IN":
        values.extend(["INCH", "Inch"])
    elif unit == "MM":
        values.extend(["MM", "Mm"])

    static_setters = []
    try:
        from OCC.Core.Interface import Interface_Static as OccStatic

        static_setters.append(OccStatic)
    except Exception:
        pass
    try:
        import Part

        if hasattr(Part, "Interface_Static"):
            static_setters.append(Part.Interface_Static)
    except Exception:
        pass
    try:
        from Part.App import Interface

        static_setters.append(Interface.Interface_Static)
    except Exception:
        pass

    for static in static_setters:
        for val in values:
            try:
                static.SetCVal(key, val)
                print(f"OCCT {key}={val}")
                return True
            except Exception:
                continue
    print(f"WARN: no se pudo fijar OCCT {key}={unit}")
    return False
