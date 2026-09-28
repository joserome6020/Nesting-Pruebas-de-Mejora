#!/usr/bin/env python3
"""Repara DXF viejos de nest/Amada para que AutoCAD/TrueView abran con zoom correcto.

Problema típico: EXTMIN/EXTMAX en ±1e20 (ezdxf) → pantalla negra hasta ZOOM E.
Uso:
  python tools/repair_dxf_view_header.py archivo.dxf
  python tools/repair_dxf_view_header.py "Z:\\...\\DXF PARA LA FIXTURA AMADA\\*.dxf"
  python tools/repair_dxf_view_header.py carpeta\\ --in-place
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ezdxf
from ezdxf.audit import Auditor

from modules.nest_exporter import _save_dxf_atomic, _sync_dxf_header_view

_KEEP_LAYERS = frozenset({"CUT_OUTER", "CUT_INNER", "MARK", "FIXTURE"})


def _export_amada_inner(msp, ent, *, layer: str = "CUT_INNER") -> bool:
    """CIRCLE o LWPOLY nativa; ranuras facetadas → bulge de estadio."""
    from modules.dxf_export.amada_esp import (
        _write_amada_inner_preserved,
        export_amada_esp_inner_closed,
    )

    typ = ent.dxftype()
    if typ == "CIRCLE":
        return bool(_write_amada_inner_preserved(msp, ent, layer))
    if typ == "LWPOLYLINE":
        pts = list(ent.get_points("xyb"))
        bulges = [abs(float(p[2] or 0.0)) for p in pts]
        if len(pts) <= 6 or max(bulges or [0.0]) > 1e-9:
            return bool(_write_amada_inner_preserved(msp, ent, layer))
        ring = [(float(p[0]), float(p[1])) for p in pts]
        return bool(export_amada_esp_inner_closed(msp, ring, layer=layer))
    return bool(_write_amada_inner_preserved(msp, ent, layer))


def _rewrite_autocad_clean(doc, *, dxfversion: str = "R2000") -> ezdxf.document.Drawing:
    """Nuevo DXF solo con geometría de corte (sin ARGA_META / BAR_START)."""
    src = doc.modelspace()
    keep = [
        e
        for e in src
        if str(getattr(e.dxf, "layer", "") or "").upper() in _KEEP_LAYERS
        or str(getattr(e.dxf, "layer", "") or "").upper().startswith("CUT_")
    ]
    if not keep:
        keep = list(src)
    out = ezdxf.new(dxfversion)
    out.header["$INSUNITS"] = int(doc.header.get("$INSUNITS", 4) or 4)
    out.header["$MEASUREMENT"] = 1
    for name, color in (
        ("CUT_OUTER", 1),
        ("CUT_INNER", 3),
        ("MARK", 4),
        ("FIXTURE", 5),
    ):
        if name not in out.layers:
            out.layers.new(name, dxfattribs={"color": color})
    msp = out.modelspace()
    for ent in keep:
        layer = str(getattr(ent.dxf, "layer", "") or "").upper()
        if layer == "CUT_OUTER" and ent.dxftype() == "LWPOLYLINE":
            pts = list(ent.get_points("xyb"))
            msp.add_lwpolyline(
                pts,
                format="xyb",
                dxfattribs={"layer": "CUT_OUTER", "closed": bool(ent.closed)},
            )
        elif layer == "CUT_INNER":
            if not _export_amada_inner(msp, ent, layer="CUT_INNER"):
                try:
                    copy = ent.copy()
                    msp.add_entity(copy)
                    copy.dxf.layer = "CUT_INNER"
                except Exception:
                    pass
        else:
            try:
                copy = ent.copy()
                msp.add_entity(copy)
            except Exception:
                pass
    for layer in out.layers:
        layer.on()
        layer.thaw()
    return out


def _header_ext(doc) -> tuple:
    h = doc.header
    return h.get("$EXTMIN"), h.get("$EXTMAX")


def _needs_repair(doc) -> bool:
    mn, mx = _header_ext(doc)
    if mn is None or mx is None:
        return True
    try:
        vals = [float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1])]
    except (TypeError, ValueError, IndexError):
        return True
    if any(abs(v) > 1e10 for v in vals):
        return True
    if vals[0] > vals[2] or vals[1] > vals[3]:
        return True
    return False


def repair_file(
    path: Path,
    *,
    in_place: bool,
    suffix: str,
    force: bool = False,
    clean: bool = False,
    output: Path | None = None,
    dxfversion: str = "R2000",
) -> bool:
    path = path.resolve()
    if not path.is_file():
        print(f"[SKIP] no existe: {path}")
        return False
    try:
        doc = ezdxf.readfile(str(path))
    except Exception as exc:
        print(f"[FAIL] no se pudo leer {path.name}: {exc}")
        return False

    msp = doc.modelspace()
    ent_count = sum(1 for _ in msp)
    if ent_count == 0:
        print(f"[WARN] {path.name}: sin entidades en modelspace")
        return False

    aud = Auditor(doc)
    aud.run()
    try:
        aud.fix_errors()
    except Exception:
        pass

    if clean:
        doc = _rewrite_autocad_clean(doc, dxfversion=dxfversion)
        aud = Auditor(doc)
        aud.run()
        try:
            aud.fix_errors()
        except Exception:
            pass

    if int(doc.header.get("$INSUNITS", 0) or 0) == 0:
        doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1

    before = _header_ext(doc)
    if not force and not _needs_repair(doc):
        print(f"[OK] {path.name}: header ya válido {before[0]} -> {before[1]}")
        return True

    _sync_dxf_header_view(doc)
    after = _header_ext(doc)
    if _needs_repair(doc):
        print(f"[FAIL] {path.name}: no se pudo calcular bbox")
        return False

    if output is not None:
        out = output.resolve()
    elif in_place:
        out = path
    else:
        out = path.with_name(f"{path.stem}{suffix}{path.suffix}")
    _save_dxf_atomic(doc, str(out))
    print(
        f"[FIX] {path.name} -> {out.name} | ent={ent_count} | "
        f"EXT {before[0]}->{after[0]} ... {before[1]}->{after[1]} | "
        f"audit_err={len(aud.errors)}"
    )
    return True


def collect(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(p.glob("*.dxf")))
            continue
        if any(ch in raw for ch in "*?[]"):
            out.extend(Path(x) for x in glob.glob(raw))
            continue
        out.append(p)
    # dedupe case-insensitive on Windows
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(description="Repara EXTMIN/EXTMAX en DXF para AutoCAD.")
    ap.add_argument("paths", nargs="+", help="Archivo(s), carpeta o glob *.dxf")
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="Sobrescribe el archivo original (hace backup .bak)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Reescribe header aunque parezca válido",
    )
    ap.add_argument(
        "--clean",
        action="store_true",
        help="Reescribe DXF R2000 solo capas de corte (mejor para AutoCAD 2026)",
    )
    ap.add_argument(
        "--suffix",
        default="_AUTOCAD",
        help="Sufijo si no es --in-place (default: _AUTOCAD)",
    )
    ap.add_argument(
        "--output",
        default="",
        help="Ruta de salida exacta (ignora --suffix)",
    )
    ap.add_argument(
        "--dxfversion",
        default="R2000",
        help="Versión DXF con --clean (default: R2000; usar R2010 para AC1024)",
    )
    args = ap.parse_args()
    files = collect(args.paths)
    if not files:
        print("No se encontraron DXF.")
        return 1
    ok = 0
    for fp in files:
        if args.in_place:
            bak = fp.with_suffix(fp.suffix + ".bak")
            if not bak.exists():
                import shutil

                shutil.copy2(fp, bak)
        out_path = Path(args.output).resolve() if str(args.output or "").strip() else None
        if repair_file(
            fp,
            in_place=args.in_place,
            suffix=args.suffix,
            force=args.force,
            clean=args.clean,
            output=out_path,
            dxfversion=str(args.dxfversion or "R2000"),
        ):
            ok += 1
    print(f"Listo: {ok}/{len(files)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
