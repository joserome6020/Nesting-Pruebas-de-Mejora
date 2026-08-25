"""Runtime mínimo: detectar OCP (Open CASCADE) y escribir STEP."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence


def ensure_ocp() -> None:
    """Importa OCP o lanza error claro si falta cadquery-ocp."""
    try:
        import OCP  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Falta Open CASCADE vía Python (paquete cadquery-ocp / OCP).\n"
            'Instalar: python -m pip install -r "CAD (OCCT)/requirements-occt.txt"'
        ) from exc


def write_step_shape(shape: Any, out_path: str | Path) -> Path:
    """Exporta un TopoDS_Shape a archivo STEP (sin colores XCAF)."""
    ensure_ocp()
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    status = writer.Write(str(path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"OCCT STEP write falló (status={int(status)}): {path}")
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"STEP vacío o no creado: {path}")
    return path


def _as_quantity_color(rgb: tuple[float, float, float]):
    from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

    return Quantity_Color(float(rgb[0]), float(rgb[1]), float(rgb[2]), Quantity_TOC_sRGB)


def _apply_vis_material(doc, label, appearance) -> bool:
    """Aplica VisMaterial estilo FreeCAD (diffuse/ambient/specular/shininess)."""
    if appearance is None:
        return False
    try:
        from OCP.XCAFDoc import (
            XCAFDoc_VisMaterial,
            XCAFDoc_VisMaterialCommon,
            XCAFDoc_VisMaterialTool,
        )

        vis_tool = XCAFDoc_VisMaterialTool.Set_s(doc.Main())
        common = XCAFDoc_VisMaterialCommon()
        common.DiffuseColor = _as_quantity_color(appearance.diffuse)
        common.AmbientColor = _as_quantity_color(
            getattr(appearance, "ambient", appearance.diffuse)
        )
        common.SpecularColor = _as_quantity_color(
            getattr(appearance, "specular", (0.9, 0.9, 0.9))
        )
        common.EmissiveColor = _as_quantity_color((0.0, 0.0, 0.0))
        common.Shininess = float(getattr(appearance, "shininess", 0.8))
        common.Transparency = 0.0

        mat = XCAFDoc_VisMaterial()
        mat.SetCommonMaterial(common)
        mat_label = vis_tool.AddMaterial(mat)
        vis_tool.SetShapeMaterial(label, mat_label)
        return True
    except Exception:
        return False


def _configure_step_writer_static(*, linear_unit: str = "MM") -> None:
    """Fija unidad y surfacecurve=0 (STEP liviano, paridad tamaño FreeCAD)."""
    from OCP.Interface import Interface_Static

    unit = str(linear_unit or "MM").strip().upper()
    unit_val = "INCH" if unit in ("IN", "INCH", "INCHES") else "MM"
    for setter_name, key, val in (
        ("SetCVal_s", "write.step.unit", unit_val),
        ("SetCVal", "write.step.unit", unit_val),
        ("SetIVal_s", "write.surfacecurve.mode", 0),
        ("SetIVal", "write.surfacecurve.mode", 0),
    ):
        setter = getattr(Interface_Static, setter_name, None)
        if setter is None:
            continue
        try:
            setter(key, val)
        except Exception:
            try:
                # Algunas builds OCP exponen *_s como función de módulo
                setter(key, val)
            except Exception:
                pass
    # Leer para forzar commit del static
    try:
        Interface_Static.IVal_s("write.surfacecurve.mode")
    except Exception:
        try:
            Interface_Static.IVal("write.surfacecurve.mode")
        except Exception:
            pass


def _prime_step_writer() -> None:
    """Primer Transfer/Write de OCCT suele ignorar static; cebamos con un box."""
    import os
    import tempfile

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    _configure_step_writer_static(linear_unit="MM")
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    shape_tool.AddShape(BRepPrimAPI_MakeBox(1.0, 1.0, 1.0).Shape(), False)
    writer = STEPCAFControl_Writer()
    if not writer.Transfer(doc, STEPControl_AsIs):
        return
    fd, tmp = tempfile.mkstemp(prefix="arga_occt_prime_", suffix=".step")
    os.close(fd)
    try:
        writer.Write(tmp)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


_STEP_WRITER_PRIMED = False


def write_step_xcaf(
    items: Sequence[tuple],
    out_path: str | Path,
    *,
    as_multibody: bool = False,
    linear_unit: str = "MM",
) -> Path:
    """
    Escribe STEP AP214 con colores / VisMaterial.

    Cada item:
      (shape, (r,g,b), kind)
      (shape, (r,g,b), kind, appearance)
    kind: 'surf' | 'curve' | 'gen'
    appearance: objeto con diffuse/ambient/specular/shininess (opcional)

    as_multibody=True: si hay varios items, los junta en un Compound y un solo
    label XCAF (Inventor → IPT multibody). Varios labels sueltos → ensamble.
    """
    global _STEP_WRITER_PRIMED
    import os
    import tempfile

    ensure_ocp()
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import (
        XCAFDoc_ColorCurv,
        XCAFDoc_ColorGen,
        XCAFDoc_ColorSurf,
        XCAFDoc_DocumentTool,
    )

    if not _STEP_WRITER_PRIMED:
        try:
            _prime_step_writer()
        except Exception:
            pass
        _STEP_WRITER_PRIMED = True

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    kind_map = {
        "surf": XCAFDoc_ColorSurf,
        "surface": XCAFDoc_ColorSurf,
        "curve": XCAFDoc_ColorCurv,
        "curv": XCAFDoc_ColorCurv,
        "gen": XCAFDoc_ColorGen,
        "general": XCAFDoc_ColorGen,
    }

    parsed: list[tuple] = []
    for item in items:
        if not item:
            continue
        shape = item[0]
        rgb = item[1] if len(item) > 1 else (0.7, 0.7, 0.7)
        kind = item[2] if len(item) > 2 else "surf"
        appearance = item[3] if len(item) > 3 else None
        if shape is None:
            continue
        try:
            if shape.IsNull():
                continue
        except Exception:
            pass
        parsed.append((shape, rgb, kind, appearance))

    if not parsed:
        raise RuntimeError("write_step_xcaf: sin shapes para exportar")

    if as_multibody and len(parsed) > 1:
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        builder = BRep_Builder()
        comp = TopoDS_Compound()
        builder.MakeCompound(comp)
        for sh, *_rest in parsed:
            builder.Add(comp, sh)
        _sh0, rgb0, kind0, app0 = parsed[0]
        parsed = [(comp, rgb0, kind0, app0)]

    n = 0
    for shape, rgb, kind, appearance in parsed:
        label = shape_tool.AddShape(shape, False)
        color = _as_quantity_color(tuple(rgb))
        ctype = kind_map.get(str(kind or "surf").lower(), XCAFDoc_ColorSurf)
        color_tool.SetColor(label, color, ctype)
        _apply_vis_material(doc, label, appearance)
        n += 1

    if n <= 0:
        raise RuntimeError("write_step_xcaf: sin shapes para exportar")

    _configure_step_writer_static(linear_unit=linear_unit)

    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    try:
        writer.SetNameMode(True)
    except Exception:
        pass
    if not writer.Transfer(doc, STEPControl_AsIs):
        raise RuntimeError(f"STEPCAF Transfer falló: {path}")

    fd, tmp_name = tempfile.mkstemp(prefix="arga_occt_", suffix=".step")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        status = writer.Write(str(tmp_path))
        if status != IFSelect_RetDone:
            raise RuntimeError(f"STEPCAF Write falló (status={int(status)}): {path}")
        if not tmp_path.is_file() or tmp_path.stat().st_size <= 0:
            raise RuntimeError(f"STEP vacío o no creado: {path}")
        # Escribir siempre en disco local y copiar al destino (UNC/red).
        # Evita dumps XCAF lentísimos directo al share.
        import shutil

        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(tmp_path), str(path))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"STEP vacío o no creado: {path}")
    return path


def solid_volume(shape: Any) -> float:
    """Volumen de un sólido BRep (unidades del modelo)."""
    ensure_ocp()
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())
