"""Auditoría estática: export cobre IGES (sin_gap) vs STEP (con separación) + unidades."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISSUES: list[str] = []
WARNINGS: list[str] = []
OK: list[str] = []


def issue(msg: str) -> None:
    ISSUES.append(msg)


def warn(msg: str) -> None:
    WARNINGS.append(msg)


def ok(msg: str) -> None:
    OK.append(msg)


def audit_units_module() -> None:
    from freecad_export_units import (
        MM_PER_INCH,
        resolve_export_linear_unit,
        resolve_geometry_scale,
    )

    if resolve_export_linear_unit("step") != "IN":
        issue("STEP debería resolver unidad IN")
    else:
        ok("STEP -> unidad IN")

    if resolve_export_linear_unit("iges") != "MM":
        issue("IGES debería resolver unidad MM")
    else:
        ok("IGES -> unidad MM")

    if resolve_geometry_scale(1.0, "step", "IN") != 1.0:
        issue("STEP no debe alterar FREECAD_SCALE base")
    else:
        ok("STEP mantiene escala 1.0")

    iges_mm = resolve_geometry_scale(1.0, "iges", "MM")
    if iges_mm != 1.0:
        issue(f"IGES con modelo mm debería escala 1.0, obtuvo {iges_mm}")
    else:
        ok("IGES (modelo mm) mantiene escala 1.0")

    iges_in = resolve_geometry_scale(1.0, "iges", "MM")
    os.environ["FREECAD_MODEL_LINEAR_UNIT"] = "in"
    iges_from_in = resolve_geometry_scale(1.0, "iges", "MM")
    del os.environ["FREECAD_MODEL_LINEAR_UNIT"]
    if abs(iges_from_in - MM_PER_INCH) > 1e-9:
        issue(f"IGES modelo in debería ×25.4, obtuvo {iges_from_in}")
    else:
        ok("IGES (modelo in opcional) escala ×25.4")


def audit_iges_routing() -> None:
    from modules.nesting_engine.exporter import (
        _build_cu_formato_por_dxf,
        _cu_dxf_requiere_3d,
        _hoja_cobre_export_3d,
        _hoja_cobre_export_iges,
    )
    from modules.nesting_engine.cu_largos_nesting import _modo_separacion_barra

    hoja_sin = {
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "sheet_code": "W.O. 1 X1-H1",
        "pqart_exports": [],
    }
    hoja_con = {
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "con_gap",
        "sheet_code": "W.O. 1 X1-H2",
        "pqart_exports": [],
    }
    hoja_acero = {"modo_largos_cu": False, "cu_modo_separacion_barra": "sin_gap"}

    if _hoja_cobre_export_3d(hoja_sin) != "dxf":
        issue("sin_gap cobre debería exportar solo DXF")
    else:
        ok("sin_gap cobre -> solo DXF")

    if _hoja_cobre_export_3d(hoja_con) != "step":
        issue("con_gap cobre debería activar STEP")
    else:
        ok("con_gap cobre -> STEP")

    if _hoja_cobre_export_iges(hoja_sin) or _hoja_cobre_export_iges(hoja_con):
        issue("IGES ya no debe activarse en cobre largos")
    else:
        ok("cobre largos sin IGES automático")

    if _hoja_cobre_export_iges(hoja_acero):
        issue("acero no debería activar IGES")
    else:
        ok("no cobre -> sin IGES automático")

    if _cu_dxf_requiere_3d("dxf"):
        issue("formato dxf no debería requerir 3D")
    elif not _cu_dxf_requiere_3d("step"):
        issue("formato step debería requerir 3D")
    else:
        ok("Helper requiere-3D coherente")

    cortas = [{"len_mm": 10 * 25.4} for _ in range(9)] + [{"len_mm": 20 * 25.4}]
    if _modo_separacion_barra(cortas) != "sin_gap":
        issue("9 cortas + 1 larga debería dar sin_gap (80%)")
    else:
        ok("Regla 80% cortas -> sin_gap")

    largas = [{"len_mm": 20 * 25.4} for _ in range(9)] + [{"len_mm": 10 * 25.4}]
    if _modo_separacion_barra(largas) != "con_gap":
        issue("9 largas + 1 corta debería dar con_gap")
    else:
        ok("Regla 80% largas -> con_gap")

    resultados = {
        "0.25_CU": {
            "modo_largos_cu": True,
            "hojas": [
                {
                    **hoja_sin,
                    "pqart_exports": [
                        {
                            "nombre_dxf": "NEST_0.25_W.O. 1 X1-H1.dxf",
                            "export_3d_format": "dxf",
                        }
                    ],
                },
                {
                    **hoja_con,
                    "pqart_exports": [
                        {
                            "nombre_dxf": "NEST_0.25_W.O. 1 X1-H2.dxf",
                            "export_3d_format": "step",
                        }
                    ],
                },
            ],
        }
    }
    fmt = _build_cu_formato_por_dxf(resultados)
    if "NEST_0.25_W.O. 1 X1-H1.dxf" in fmt:
        issue(f"sin_gap no debería entrar al manifiesto 3D: {fmt}")
    elif fmt.get("NEST_0.25_W.O. 1 X1-H2.dxf") != "step":
        issue(f"Manifiesto STEP incorrecto: {fmt}")
    else:
        ok("Manifiesto pqart_exports -> solo con_gap en conversión 3D")


def audit_cad_paths() -> None:
    from freecad_runner import _cad_path_for_dxf, _cad_extension

    dxf = r"C:\tmp\NESTING_0.25_W.O. 99 X1-H7.dxf"
    step = _cad_path_for_dxf(dxf, r"C:\out\STEP", "step")
    iges = _cad_path_for_dxf(dxf, r"C:\out\IGES", "iges")

    if not step.endswith(os.path.join("STEP", "W.O. 99 X1-H7.step")):
        issue(f"Ruta STEP inesperada: {step}")
    else:
        ok("Nombre STEP usa sufijo W.O. (no prefijo NESTING)")

    if _cad_extension("iges") != ".igs":
        issue("Extensión IGES debería ser .igs")
    elif not iges.endswith(".igs"):
        issue(f"Ruta IGES inesperada: {iges}")
    else:
        ok("Ruta IGES coherente (.igs, base W.O.)")


def audit_dxf_sample() -> None:
    try:
        import ezdxf
    except ImportError:
        warn("ezdxf no disponible; omitiendo muestra DXF")
        return

    samples = list((ROOT / "_logs").rglob("NESTING_*.dxf"))[:3]
    if not samples:
        warn("Sin DXF de muestra en _logs")
        return

    for path in samples:
        doc = ezdxf.readfile(str(path))
        ins = int(doc.header.get("$INSUNITS", 0) or 0)
        if ins != 4:
            warn(f"{path.name}: $INSUNITS={ins} (esperado 4=mm)")
        else:
            ok(f"{path.name}: DXF en mm ($INSUNITS=4)")

        xs, ys = [], []
        msp = doc.modelspace()
        for e in msp:
            try:
                if e.dxftype() == "LINE":
                    xs.extend([float(e.dxf.start.x), float(e.dxf.end.x)])
                    ys.extend([float(e.dxf.start.y), float(e.dxf.end.y)])
                elif e.dxftype() == "LWPOLYLINE":
                    for p in e.get_points("xy"):
                        xs.append(float(p[0]))
                        ys.append(float(p[1]))
            except Exception:
                pass
        if xs and ys:
            span_x = max(xs) - min(xs)
            span_y = max(ys) - min(ys)
            # Barra cobre 144" ≈ 3657 mm; si >> 5000 probable pulgadas crudas mal etiquetadas
            if span_x > 5000 or span_y > 5000:
                warn(
                    f"{path.name}: bbox grande X={span_x:.0f} Y={span_y:.0f} "
                    "(revisar si FreeCAD interpreta como in)"
                )
            elif span_x < 200 and span_y < 50:
                warn(f"{path.name}: bbox pequeño X={span_x:.1f} Y={span_y:.1f} (¿pulgadas?)")
            else:
                ok(
                    f"{path.name}: bbox X={span_x:.0f} mm Y={span_y:.0f} mm "
                    "(coherente con barra cobre)"
                )


def audit_macro_parity() -> None:
    batch = (ROOT / "freecad_batch_dxf_to_step.py").read_text(encoding="utf-8")
    verde = (ROOT / "generador_verde.FCMacro").read_text(encoding="utf-8")
    for name, src in (("freecad_batch", batch), ("generador_verde", verde)):
        if "_configure_export_units" not in src:
            issue(f"{name} falta _configure_export_units")
        elif "FREECAD_EXPORT_LINEAR_UNIT" not in src:
            issue(f"{name} falta FREECAD_EXPORT_LINEAR_UNIT")
        elif "SetCVal" not in src:
            issue(f"{name} falta SetCVal OCCT")
        else:
            ok(f"{name} configura unidades antes de export")


def audit_risks() -> None:
    warn(
        "cobre_step_fuentes sigue generando solo STEP junto al DXF fuente "
        "(no IGES); solo aplica a hojas de nesteo."
    )
    warn(
        "OCCT puede ignorar prefs si Interface_Static no está disponible; "
        "revisar freecad_macro.log: 'OCCT write.iges.unit=MM'."
    )
    warn(
        "FreeCAD no reescala geometría al cambiar unidad de export; "
        "IGES mm asume DXF nest en mm con FREECAD_SCALE=1.0."
    )


def main() -> int:
    print("=" * 60)
    print("AUDITORÍA EXPORT COBRE IGES / STEP")
    print("=" * 60)

    audit_units_module()
    audit_iges_routing()
    audit_cad_paths()
    audit_dxf_sample()
    audit_macro_parity()
    audit_risks()

    print(f"\n[OK] {len(OK)}")
    for line in OK:
        print(f"  + {line}")

    if WARNINGS:
        print(f"\n[WARN] {len(WARNINGS)}")
        for line in WARNINGS:
            print(f"  ! {line}")

    if ISSUES:
        print(f"\n[FAIL] {len(ISSUES)}")
        for line in ISSUES:
            print(f"  x {line}")
        print("\nRESULTADO: NO APTO")
        return 1

    print("\nRESULTADO: APTO (con advertencias arriba si las hay)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
