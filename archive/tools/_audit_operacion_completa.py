"""Auditoría operativa: DXF/STEP cobre, nesting CU, y logs FreeCAD."""
from __future__ import annotations

import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from freecad_runner import _listar_dxfs_en_carpeta, _pendientes_step, _step_path_for_dxf
from modules.cobre_step_audit import (
    audit_macro_log_for_losses,
    count_cut_cu_piece_islands,
)
from modules.nesting_engine.cu_largos_nesting import (
    DEFAULT_SEPARACION_CU_IN,
    _modo_separacion_barra,
    _pieza_cu_exime_separacion,
    empaquetar_largos_cu,
)
from shapely.geometry import box

WO_BASE = os.environ.get(
    "AUDIT_WO_BASE",
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\PRODUCTO_TEST\CLIENTE_TEST\GIGA COBRE PRUEBA\MODEL CORE FILES\W.O. 4 X1"
    r"\ARGA MODEL CORE\NESTING\CAMA LASER SIN MINI NEST",
)


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def audit_step_coverage(dxf_dir: str, step_dir: str) -> list[str]:
    issues: list[str] = []
    dxfs = _listar_dxfs_en_carpeta(dxf_dir)
    pending = _pendientes_step(dxfs, step_dir)
    n_step = len(glob.glob(os.path.join(step_dir, "*.step")))
    print(f"\n=== STEP COBERTURA ===")
    print(f"  DXF: {len(dxfs)} | STEP en disco: {n_step} | Pendientes: {len(pending)}")
    if len(dxfs) == 0:
        issues.append("Sin DXF en carpeta")
        _fail("Sin DXF")
        return issues
    if pending:
        issues.append(f"STEP incompletos: {len(dxfs) - len(pending)}/{len(dxfs)}")
        _warn(f"Faltan {len(pending)} STEP:")
        for p in pending[:15]:
            _warn(f"  - {os.path.basename(p)}")
        if len(pending) > 15:
            _warn(f"  ... +{len(pending) - 15} más")
    else:
        _ok(f"30/30 equivalente: {len(dxfs)}/{len(dxfs)} STEP vigentes")
    return issues


def audit_dxf_cut_cu(dxf_dir: str, sample: int = 8) -> list[str]:
    issues: list[str] = []
    print(f"\n=== DXF CUT_CU (muestra) ===")
    dxfs = _listar_dxfs_en_carpeta(dxf_dir)
    if not dxfs:
        return ["Sin DXF"]
    import ezdxf

    bad = 0
    for path in dxfs[:sample]:
        name = os.path.basename(path)
        try:
            doc = ezdxf.readfile(path)
            islands = count_cut_cu_piece_islands(doc.modelspace())
            if islands < 0:
                bad += 1
                issues.append(f"{name}: CUT_CU con {abs(islands)} entidades sueltas (sin LWPOLYLINE)")
                _fail(f"{name}: LINE/ARC sueltos en CUT_CU")
            elif islands == 0:
                bad += 1
                issues.append(f"{name}: CUT_CU sin islas")
                _fail(f"{name}: CUT_CU vacío")
            else:
                _ok(f"{name}: {islands} isla(s) LWPOLYLINE")
        except Exception as e:
            bad += 1
            issues.append(f"{name}: error leyendo DXF: {e}")
            _fail(f"{name}: {e}")
    if bad == 0:
        _ok(f"Muestra {min(sample, len(dxfs))} DXF sin problemas CUT_CU")
    return issues


def audit_macro_log(step_dir: str) -> list[str]:
    print(f"\n=== LOG FREECAD ===")
    log_path = os.path.join(step_dir, "_logs", "freecad_macro.log")
    if not os.path.isfile(log_path):
        _warn(f"No existe {log_path}")
        return ["Log macro ausente"]
    issues = audit_macro_log_for_losses(log_path, material="CU")
    runner = os.path.join(step_dir, "_logs", "freecad_runner.log")
    if os.path.isfile(runner):
        with open(runner, "r", encoding="utf-8", errors="replace") as f:
            tail = f.read()[-4000:]
        if "RESULTADO FINAL: OK" in tail:
            _ok("freecad_runner.log: última corrida OK")
        elif "STEP faltantes" in tail:
            _warn("freecad_runner.log: STEP faltantes en última corrida")
            issues.append("Runner reportó STEP faltantes")
        elif "TIMEOUT" in tail:
            _warn("freecad_runner.log: timeout detectado")
            issues.append("Timeout FreeCAD")
    if issues:
        for i in issues:
            _fail(i)
    else:
        _ok("Sin pérdida de piezas en log macro")
    return issues


def audit_nesting_logic() -> list[str]:
    print(f"\n=== NESTING CU (lógica) ===")
    issues: list[str] = []

    def it(l_in: float) -> dict:
        return {"len_mm": l_in * 25.4}

    if _modo_separacion_barra([it(10)] * 13 + [it(20)] * 2) != "sin_gap":
        issues.append("Regla 80% cortas")
        _fail("80% cortas no -> sin_gap")
    else:
        _ok("80% cortas -> sin_gap")

    if _modo_separacion_barra([it(20)] * 13 + [it(10)] * 2) != "con_gap":
        issues.append("Regla 80% largas")
        _fail("80% largas no -> con_gap")
    else:
        _ok("80% largas -> con_gap")

    if _modo_separacion_barra([it(10)] * 5 + [it(20)] * 5) != "hibrido":
        issues.append("Barra mixta sin mayoría")
        _fail("50/50 no -> hibrido")
    else:
        _ok("Sin mayoria -> hibrido (gap uniforme)")

    placa = {"id": 1, "w": 144 * 25.4, "h": 4 * 25.4, "origen": "E", "material": "CU", "espesor": 0.25}

    def pieza(n: str, l: float) -> dict:
        poly = box(0, 0, l * 25.4, 4 * 25.4)
        return {"nombre": n, "poly": poly, "area": poly.area, "material": "CU"}

    piezas = [pieza(f"C{i}", 10) for i in range(6)] + [pieza(f"L{i}", 20) for i in range(6)]
    hojas, sin = empaquetar_largos_cu(piezas, [placa], separacion_in=DEFAULT_SEPARACION_CU_IN)
    if sin:
        issues.append(f"Empaquetado sintético dejó {len(sin)} sin colocar")
        _fail(f"Sin colocar: {len(sin)}")
    else:
        _ok("Empaquetado sintético: 12/12 colocadas")

    mixtas = 0
    for h in hojas:
        reales = [p for p in h["piezas"] if not str(p.get("nombre", "")).startswith("CU_CORTE")]
        cortas = sum(1 for p in reales if float(p.get("largo_cu_in") or 99) <= 15.01)
        largas = len(reales) - cortas
        if cortas and largas:
            mixtas += 1
    if mixtas:
        issues.append(f"Barras mixtas en test homogéneo: {mixtas}")
        _warn(f"Test homogéneo generó {mixtas} barra(s) mixta(s)")
    else:
        _ok("Agrupación homogénea en test sintético")

    return issues


def main() -> int:
    print("=" * 60)
    print("AUDITORÍA OPERATIVA — GIGA COBRE / W.O. 4 X1")
    print("=" * 60)

    all_issues: list[str] = []
    all_issues.extend(audit_nesting_logic())

    dxf_dir = os.path.join(WO_BASE, "DXF")
    step_dir = os.path.join(WO_BASE, "STEP")

    if os.path.isdir(dxf_dir):
        all_issues.extend(audit_step_coverage(dxf_dir, step_dir))
        all_issues.extend(audit_dxf_cut_cu(dxf_dir, sample=10))
        if os.path.isdir(step_dir):
            all_issues.extend(audit_macro_log(step_dir))
    else:
        _warn(f"Ruta WO no accesible: {dxf_dir}")
        all_issues.append("Ruta WO no accesible")

    print("\n" + "=" * 60)
    if all_issues:
        print(f"RESULTADO: {len(all_issues)} observación(es)")
        for i in all_issues:
            print(f"  - {i}")
        return 1
    print("RESULTADO: TODO OK — listo para corrida")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
