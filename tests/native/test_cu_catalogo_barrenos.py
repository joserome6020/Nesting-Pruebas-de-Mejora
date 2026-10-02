"""Candado: doble protección del herramental de cobre (catálogo de planos + simulación).

Motivo: W.O. 91 X1 (2026-10-01) salió con el herramental mal elegido. Además del
analizador de barrenos, el CSV de punzonado ahora pasa por:

- Catálogo de barrenos (``_config/cu_catalogo_barrenos.json``, desde los STEP/PDF
  de GIGA): tipo, medida, dirección y cantidad por pieza; si no coincide, bloquea.
- Simulación del punzonado: cada golpe con la herramienta de su ``TOOLn`` debe
  reproducir el barreno del DXF (posición, medida y orientación), uno por barreno.
- Un STEP vacío/ilegible nunca cuenta como "pieza sin barrenos" al generar el catálogo.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shapely.geometry import LineString, box  # noqa: E402


def _slot(cx, cy, w, largo, *, eje_y):
    d = (largo - w) / 2.0
    seg = LineString([(cx, cy - d), (cx, cy + d)]) if eje_y else LineString([(cx - d, cy), (cx + d, cy)])
    return seg.buffer(w / 2.0, resolution=32)


def _hoja_fcu108():
    """Barra 1.75" con GENE-FCU-5-108 (2 ovalados 10.31×15.08, lo largo a lo largo)."""
    from modules.nesting_engine.cu_largos_nesting import empaquetar_largos_cu

    poly = box(0, 0, 127.1, 44.45)
    for cx in (44.73 + 4.766 / 2, 84.44 + 4.766 / 2):
        poly = poly.difference(_slot(cx, 22.23, 10.31, 15.08, eje_y=False))
    pieza = {
        "nombre": "GENE-FCU-5-108", "poly": poly, "marks": None, "area": poly.area,
        "calibre": "0.25", "material": "CU", "ruta": "GENE-FCU-5-108.dxf",
    }
    placa = {
        "w": 3657.6, "h": 44.45, "precio": 100.0, "precio_lb": 1.0,
        "origen_placa": "EMPRESA", "material": "CU", "calibre": "0.25",
    }
    hojas, sin = empaquetar_largos_cu([pieza], [placa], separacion_in=0.0)
    assert not sin and len(hojas) == 1
    return hojas[0]


def main() -> None:
    previous = os.environ.get("ARGA_NEST_DATA_DIR")
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["ARGA_NEST_DATA_DIR"] = temp_dir
        os.environ.pop("ARGA_CU_FORCE_DXF_STEP", None)
        from modules.dxf_export import cu_punch_csv as pc
        from modules.nesting_engine import cu_catalogo_barrenos as cb

        # --- Catálogo real empaquetado (STEP + PDF manual) ---
        cat = cb.cargar_catalogo()
        assert len(cat) >= 170, f"todas las piezas de cobre de los 16 boards ({len(cat)})"
        for nombre in (
            "GENE-FCU-2-105", "GENE-FCU-2.25-101", "GENE-FCU-4-306", "GENE-FCU-5-110",
            "GENE-FCU-5-120", "GENE-FCU-5-124", "GENE-FCU-6-113",
        ):
            assert nombre in cat, f"{nombre}: STEP vacío de GIGA, debe venir del plano PDF"
        ok = [("E", 15.07, 10.31)] * 2
        assert cb.verificar_pieza("GENE-FCU-5-108", ok)[0] == "ok"
        assert cb.verificar_pieza("GENE-FCU-5-108 (2)", ok)[0] == "ok", "nombre con sufijo"
        est, det = cb.verificar_pieza("GENE-FCU-5-108", [("E", 10.31, 15.07)] * 2)
        assert est == "no_coincide" and "2× ovalado 10.31×15.07 GIRADO" in det, det
        assert "lo pide a lo largo" in det and "trae a lo ancho" in det, det
        # Un solo slot girado entre dos: se señala ese, no "medida distinta".
        est, det = cb.verificar_pieza("GENE-FCU-5-108", [("E", 15.07, 10.31), ("E", 10.31, 15.07)])
        assert est == "no_coincide" and det.count("GIRADO") == 1 and "1× ovalado" in det, det
        # Pieza acomodada a 180° en la barra: mismos ejes, sigue OK.
        assert cb.verificar_pieza("GENE-FCU-5-108", [("E", 15.07, 10.31), ("E", 15.08, 10.30)])[0] == "ok"
        est, det = cb.verificar_pieza("GENE-FCU-5-108", ok[:1])
        assert est == "no_coincide" and "faltan 1×" in det, det
        est, _ = cb.verificar_pieza("GENE-FCU-5-108", ok + [("C", 11.11, 11.11)])
        assert est == "no_coincide", "barreno de más"
        est, _ = cb.verificar_pieza("GENE-FCU-5-108", [("E", 20.64, 11.11)] * 2)
        assert est == "no_coincide", "medida distinta"
        assert cb.verificar_pieza("PIEZA-X", ok)[0] == "sin_catalogo"
        # No todo el cobre se llama GENE-*CU-*: ABB/RLG por número de parte completo.
        cat_n = {"RLG-J-1-4KA-S": {}, "RLG-J-10-4KA-S": {}, "ABB-62-10-BCK-33": {}}
        assert cb.codigo_pieza("RLG-J-10-4KA-S (2)", cat_n) == "RLG-J-10-4KA-S"
        assert cb.codigo_pieza("RLG-J-1-4KA-S", cat_n) == "RLG-J-1-4KA-S"
        assert cb.codigo_pieza("ABB-62-10-BCK-33_rev", cat_n) == "ABB-62-10-BCK-33"
        assert cb.codigo_pieza("ABB-62-10-BCK-330", cat_n) is None
        for pn in ("ABB-42-BCK-705", "ABB-62-10-BCK-33", "RLG-J-A1-4KA-S", "GENE-NC-0808-5"):
            assert pn in cat, f"{pn}: pieza de cobre de los planos (no se llama GENE-*CU-*)"
        for pn in ("GENE-BKT-287", "GENE-SIHC-40-40-119"):
            assert pn not in cat, f"{pn}: soporte de acero 'for copper', no es cobre"
        # FCU-5-120 desde el plano: 10× Ø11.00 + 20 ovalados 10.31×15.07 a lo ancho.
        b120 = [("C", 11.0, 11.0)] * 10 + [("E", 10.31, 15.07)] * 20
        assert cb.verificar_pieza("GENE-FCU-5-120", b120)[0] == "ok"
        # FCU-5-118: 6× Ø11 + 24 ovalados a lo ancho (STEP).
        b118 = [("C", 11.0, 11.0)] * 6 + [("E", 10.31, 15.07)] * 24
        assert cb.verificar_pieza("GENE-FCU-5-118", b118)[0] == "ok"
        # El catálogo sale de los planos: ninguna pieza de cobre sin barrenos.
        vacias = [pn for pn, e in cat.items() if not e.get("variantes")]
        assert not vacias, f"piezas sin barrenos del plano: {vacias}"
        # ABB-22-U-BCK-735: el plano (6× Ø11.11 + 2× Ø10.31) manda sobre el STEP con Ø10.41.
        b735 = [("C", 11.11, 11.11)] * 6 + [("C", 10.31, 10.31)] * 2
        assert cb.verificar_pieza("ABB-22-U-BCK-735", b735)[0] == "ok"
        b735_step = [("C", 11.11, 11.11)] * 2 + [("C", 10.41, 10.41)] * 4 + [("C", 10.31, 10.31)] * 2
        assert cb.verificar_pieza("ABB-22-U-BCK-735", b735_step)[0] == "no_coincide"
        # RLG-J-1 (STEP vacío, leído del plano): 4× Ø11.11 + 2 ovalados 20.64×11.11 a lo largo.
        bj1 = [("C", 11.11, 11.11)] * 4 + [("E", 20.64, 11.11)] * 2
        assert cb.verificar_pieza("RLG-J-1-4KA-S", bj1)[0] == "ok"
        assert cb.verificar_pieza("RLG-J-1-4KA-S", [("C", 11.11, 11.11)] * 4 + [("E", 11.11, 20.64)] * 2)[0] == "no_coincide"
        assert cb.verificar_pieza("ABB-62-10-BCK-57", [("C", 11.11, 11.11)] * 9)[0] == "ok"

        # --- El export bloquea si el DXF no coincide con el plano ---
        hoja = _hoja_fcu108()
        filas = pc.construir_filas_barra(hoja, thickness_mm=6.35)
        assert filas[0]["M2"] == "M3" and filas[0]["TOOL3"] == "E15.1X10.3", filas[0]
        cat_otro = {
            "GENE-FCU-5-108": {"variantes": [{"barrenos": [
                {"tipo": "E", "ancho": 10.31, "largo": 15.07, "eje": "ancho", "cantidad": 2}
            ]}]}
        }
        try:
            pc.construir_filas_barra(_hoja_fcu108(), thickness_mm=6.35, catalogo=cat_otro)
        except pc.PunchCsvError as exc:
            assert "no coinciden con el plano" in str(exc) and "GIRADO" in str(exc), exc
        else:
            raise AssertionError("DXF distinto al plano debió bloquear el CSV")
        # GIGA nuevo (pieza fuera del catálogo): sale el CSV solo con el analizador, sin ruido.
        h_sin = _hoja_fcu108()
        filas_sin = pc.construir_filas_barra(h_sin, thickness_mm=6.35, catalogo={})
        assert filas_sin and filas_sin[0]["TOOL3"] == "E15.1X10.3", filas_sin
        assert "cu_punch_avisos" not in h_sin
        assert cb.verificar_pieza("PIEZA-X", ok, catalogo={}) == ("sin_catalogo", "")

        # --- Simulación del punzonado detecta CSV alterado ---
        hoja = _hoja_fcu108()
        filas = pc.construir_filas_barra(hoja, thickness_mm=6.35)
        assert pc.verificar_filas_barra(hoja, filas) == []
        girada = [dict(filas[0], TOOL3="E10.3X15.1")]
        errs = pc.verificar_filas_barra(hoja, girada)
        assert errs and "no reproduce el barreno" in errs[0], errs
        otra = [dict(filas[0], M2="M8", M3="M8")]
        errs = pc.verificar_filas_barra(hoja, otra)
        assert any("no reproduce el barreno" in e for e in errs), errs
        falta = [dict(filas[0], M3="", X3="0", Y3="0")]
        errs = pc.verificar_filas_barra(hoja, falta)
        assert any("0 golpes" in e for e in errs), errs
        movida = [dict(filas[0], X2=str(float(filas[0]["X2"]) + 1.0))]
        errs = pc.verificar_filas_barra(hoja, movida)
        assert any("no cae en ningún barreno" in e for e in errs), errs

        # --- Generador del catálogo ---
        sys.path.insert(0, str(ROOT / "tools"))
        import cu_catalogo_barrenos as tool

        manual = tool.mezclar_manual({})
        assert manual["GENE-FCU-5-124"]["variantes"][0]["fuente"].startswith("PDF:")
        try:
            import OCP  # noqa: F401
        except ImportError:
            print("[SKIP] OCP no disponible: STEP vacío")
        else:
            vacio = Path(temp_dir) / "GENE-FCU-9-999.step"
            vacio.write_text(
                "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\n"
                "FILE_NAME('','',(''),(''),'','','');\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
                "ENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n",
                encoding="ascii",
            )
            try:
                tool.analizar_step(str(vacio))
            except RuntimeError:
                pass
            else:
                raise AssertionError("STEP vacío no puede pasar como pieza sin barrenos")
            logs: list[str] = []
            cat_v = tool.construir_catalogo(temp_dir, log=logs.append)
            assert "GENE-FCU-9-999" not in cat_v and logs and "[ERROR]" in logs[0], (cat_v, logs)

    if previous is None:
        os.environ.pop("ARGA_NEST_DATA_DIR", None)
    else:
        os.environ["ARGA_NEST_DATA_DIR"] = previous
    print("SMOKE OK")


if __name__ == "__main__":
    main()
