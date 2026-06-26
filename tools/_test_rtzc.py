"""Pruebas RTZC: sobrante + compensación plasma → solo Robot Plasma."""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.nesting_engine.efficiency_metrics import (
    allocar_nombre_rtzc_sobrante_db,
    es_placa_madre_rtzc,
    es_placa_madre_sobrante_rtz,
    hoja_es_sobrante_plasma_compensado,
    hoja_export_solo_plasma,
    inicializar_contador_rtz_sobrante,
    inicializar_contador_rtzc_sobrante,
    nombre_rtzc_para_placa,
    sincronizar_hoja_sobrante_rtz,
    sincronizar_sobrantes_rtz_en_resultados,
)
from modules.nesting_engine.exporter import (
    _debe_generar_plasma,
    _resolver_display_name_hoja,
    exportar_resultados_a_dxf,
)
from interface.postgres_connector import _build_sheet_meta


def _hoja_base(**extra):
    h = {
        "placa_id": "PLC-TEST",
        "placa_w": 1219.2,
        "placa_h": 2438.4,
        "eficiencia": 12.0,
        "piezas": [
            {
                "nombre": "PIEZA_A",
                "poligonos": [
                    [(0, 0), (500, 0), (500, 500), (0, 500)],
                ],
                "marcas": [],
            }
        ],
    }
    h.update(extra)
    return h


def test_nomenclatura_rtzc():
    nombre = nombre_rtzc_para_placa(
        1, "0.5", "WO TEST", largo_mm=2438.4, ancho_mm=1219.2
    )
    assert nombre.startswith("RTZC1-0.5-"), nombre
    assert "96.0x48.0" in nombre, nombre
    assert nombre.endswith("-WO TEST"), nombre
    print("OK test_nomenclatura_rtzc")


def test_sync_rtzc_vs_rtz():
    h_rtzc = _hoja_base(
        ignorar_deduccion=True,
        plasma_compensado_manual=True,
    )
    h_rtz = _hoja_base(ignorar_deduccion=True)

    sincronizar_hoja_sobrante_rtz(
        h_rtzc,
        ignorar=True,
        contador_rtz={"n": 1},
        contador_rtzc={"n": 1},
        calibre="0.5",
        wo_name="1000 KVA",
    )
    sincronizar_hoja_sobrante_rtz(
        h_rtz,
        ignorar=True,
        contador_rtz={"n": 1},
        calibre="0.5",
        wo_name="1000 KVA",
    )

    assert h_rtzc["placa_id"].startswith("RTZC"), h_rtzc["placa_id"]
    assert h_rtzc.get("is_rtz_plasma_sobrante") is True
    assert h_rtzc.get("rtz_tipo") == "COMPENSADO"
    assert es_placa_madre_rtzc(h_rtzc)
    assert not es_placa_madre_sobrante_rtz(h_rtzc)

    assert h_rtz["placa_id"].startswith("RTZ") and not h_rtz["placa_id"].startswith("RTZC")
    assert not h_rtz.get("is_rtz_plasma_sobrante")
    assert es_placa_madre_sobrante_rtz(h_rtz)
    assert not es_placa_madre_rtzc(h_rtz)
    print("OK test_sync_rtzc_vs_rtz")


def test_debe_generar_plasma():
    clave = "0.5_ACERO"
    h_comp = _hoja_base(ignorar_deduccion=True, plasma_compensado_manual=True)
    h_sobrante = _hoja_base(ignorar_deduccion=True)
    assert _debe_generar_plasma(clave, h_comp) is True
    assert _debe_generar_plasma(clave, h_sobrante) is False
    print("OK test_debe_generar_plasma")


def test_build_sheet_meta():
    hoja = _hoja_base(ignorar_deduccion=True, plasma_compensado_manual=True)
    sincronizar_hoja_sobrante_rtz(
        hoja,
        ignorar=True,
        contador_rtz={"n": 1},
        contador_rtzc={"n": 1},
        calibre="0.5",
        wo_name="WO1",
    )
    meta = _build_sheet_meta(
        hoja=hoja,
        grupo_calibre="0.5_ACERO",
        sheet_seq_global=1,
        contador_placas={},
        contador_rtz={"n": 99},
        contador_rtzc={"n": 99},
        nombre_job="JOB",
        nombre_wo="WO1",
        es_swo=False,
        ruta_exportacion="",
    )
    assert meta["is_rtz"] is True
    assert meta["is_rtz_plasma_sobrante"] is True
    assert meta["rtz_tipo"] == "COMPENSADO"
    assert meta["sheet_display_name"].startswith("RTZC")
    print("OK test_build_sheet_meta")


def test_export_solo_plasma(tmp_dir):
    hoja = _hoja_base(
        ignorar_deduccion=True,
        plasma_compensado_manual=True,
        sheet_seq=1,
    )
    sincronizar_hoja_sobrante_rtz(
        hoja,
        ignorar=True,
        contador_rtz={"n": 1},
        contador_rtzc={"n": 1},
        calibre="0.5",
        wo_name="WO-RTZC",
    )
    resultados = {"0.5_ACERO": {"hojas": [hoja]}}
    exportados = exportar_resultados_a_dxf(
        resultados,
        tmp_dir,
        base_name="TEST_RTZC",
        wo_label="WO-RTZC",
    )
    nest_root = os.path.join(tmp_dir, "NESTING")
    laser_dirs = []
    for sub in (
        "CAMA LASER SIN MINI NEST",
        "CAMA LASER 12 KW SIN MINI NEST",
        "ROBOT LASER + MINI NEST",
    ):
        dxf_dir = os.path.join(nest_root, sub, "DXF")
        if os.path.isdir(dxf_dir):
            laser_dirs.extend(
                [os.path.join(dxf_dir, f) for f in os.listdir(dxf_dir) if f.endswith(".dxf")]
            )
    plasma_dir = os.path.join(nest_root, "ROBOT PLASMA", "DXF")
    plasma_files = (
        [os.path.join(plasma_dir, f) for f in os.listdir(plasma_dir) if f.endswith(".dxf")]
        if os.path.isdir(plasma_dir)
        else []
    )

    assert hoja_export_solo_plasma(hoja)
    assert len(laser_dirs) == 0, f"DXF láser inesperados: {laser_dirs}"
    assert len(plasma_files) >= 1, "Falta DXF en Robot Plasma"
    assert len(exportados) >= 1
    pqart = hoja.get("pqart_exports") or []
    tipos = {e.get("tipo_corte") for e in pqart}
    assert "Plasma" in tipos
    assert "CamaLaser" not in tipos and "RobotLaser" not in tipos
    print("OK test_export_solo_plasma")


def test_regression_sobrante_laser(tmp_dir):
    hoja = _hoja_base(ignorar_deduccion=True, sheet_seq=1)
    sincronizar_hoja_sobrante_rtz(
        hoja,
        ignorar=True,
        contador_rtz={"n": 1},
        calibre="0.25",
        wo_name="WO-RTZ",
    )
    resultados = {"0.25_ACERO": {"hojas": [hoja]}}
    exportar_resultados_a_dxf(resultados, tmp_dir, base_name="TEST_RTZ", wo_label="WO-RTZ")
    plasma_dir = os.path.join(tmp_dir, "NESTING", "ROBOT PLASMA", "DXF")
    plasma_files = (
        os.listdir(plasma_dir) if os.path.isdir(plasma_dir) else []
    )
    assert hoja["placa_id"].startswith("RTZ") and not hoja["placa_id"].startswith("RTZC")
    assert len(plasma_files) == 0, f"Sobrante láser no debe exportar plasma: {plasma_files}"
    print("OK test_regression_sobrante_laser")


def main():
    tmp = tempfile.mkdtemp(prefix="rtzc_test_")
    try:
        test_nomenclatura_rtzc()
        test_sync_rtzc_vs_rtz()
        test_debe_generar_plasma()
        test_build_sheet_meta()
        test_export_solo_plasma(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        tmp = tempfile.mkdtemp(prefix="rtz_test_")
        test_regression_sobrante_laser(tmp)
        print("\n=== TODAS LAS PRUEBAS RTZC OK ===")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
