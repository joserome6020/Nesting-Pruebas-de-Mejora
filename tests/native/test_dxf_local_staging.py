"""Candado: DXF en red/UNC se stagea a %TEMP% antes de ezdxf/OCCT."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAD = ROOT / "CAD (OCCT)"
if str(CAD) not in sys.path:
    sys.path.insert(0, str(CAD))


def test_needs_staging_unc_and_force() -> None:
    from engine.local_staging import needs_local_staging

    os.environ.pop("ARGA_STAGE_DXF", None)
    assert needs_local_staging(r"\\192.168.2.80\share\nest\a.dxf") is True
    assert needs_local_staging(r"C:\local\nest\a.dxf") is False

    os.environ["ARGA_STAGE_DXF"] = "0"
    assert needs_local_staging(r"\\server\share\a.dxf") is False

    os.environ["ARGA_STAGE_DXF"] = "1"
    assert needs_local_staging(r"C:\local\nest\a.dxf") is True
    os.environ.pop("ARGA_STAGE_DXF", None)


def test_stage_copies_then_cleans(tmp: Path) -> None:
    from engine.local_staging import stage_file_to_temp

    src = tmp / "pieza.dxf"
    payload = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"
    src.write_bytes(payload)

    held: Path | None = None
    with stage_file_to_temp(
        src, prefix="arga_dxf_test_", suffix=".dxf", force=True
    ) as local:
        held = local
        assert local != src
        assert local.is_file()
        assert local.read_bytes() == payload
        assert "arga_dxf_test_" in local.name

    assert held is not None
    assert not held.exists(), "temp DXF debe borrarse al salir del staging"


def test_collect_stages_when_forced(tmp: Path) -> None:
    """collect_dxf_nest con ARGA_STAGE_DXF=1 copia a TEMP aunque el path sea local."""
    import ezdxf

    os.environ["ARGA_STAGE_DXF"] = "1"
    try:
        dxf = tmp / "mini_outer.dxf"
        doc = ezdxf.new("R2010")
        doc.layers.add("CUT_OUTER")
        doc.modelspace().add_lwpolyline(
            [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        doc.saveas(str(dxf))

        copied: list[str] = []
        import shutil
        from engine import local_staging as staging

        real_copy2 = shutil.copy2

        def _spy_copy2(src, dst, *a, **kw):
            copied.append(str(dst))
            return real_copy2(src, dst, *a, **kw)

        staging.shutil.copy2 = _spy_copy2  # type: ignore[method-assign]
        try:
            from engine.dxf_to_step import collect_dxf_nest

            geom = collect_dxf_nest(dxf)
            assert len(copied) >= 1, "debió stagear DXF a TEMP"
            assert len(geom.outer_wires) >= 1
        finally:
            staging.shutil.copy2 = real_copy2  # type: ignore[method-assign]
    finally:
        os.environ.pop("ARGA_STAGE_DXF", None)


def main() -> int:
    import tempfile

    test_needs_staging_unc_and_force()
    print("needs_staging OK")
    with tempfile.TemporaryDirectory() as td:
        test_stage_copies_then_cleans(Path(td))
        print("stage_copies_then_cleans OK")
    with tempfile.TemporaryDirectory() as td:
        test_collect_stages_when_forced(Path(td))
        print("collect_stages_when_forced OK")
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
