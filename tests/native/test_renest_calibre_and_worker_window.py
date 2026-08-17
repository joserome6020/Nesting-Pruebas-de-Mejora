"""Candados para renesteo de calibre y worker sin ventana de consola."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from shapely.geometry import LineString, box

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin  # noqa: E402
from modules.nesting_engine import arga_nest_worker_client as worker_client  # noqa: E402


def test_renest_calibre_define_conteos_para_metadata():
    """El renesteo no puede fallar al registrar conteos job/nido."""

    class FakeNesting(NestingCalcMixin):
        def _conteo_piezas_job_grupo(self, _clave):
            return {"Pieza": 3}

        def _contar_piezas_reales_grupo(self, _clave):
            return {"Pieza": 2}

        def _conteo_para_renest_calibre(self, _clave):
            return {"Pieza": 2}

        def _construir_fuente_geometria_por_nombre(self, _clave, *, prefer_dxf):
            assert prefer_dxf is True
            return {
                "Pieza": {
                    "nombre": "Pieza",
                    "poly_base": box(0, 0, 10, 5),
                    "marks_base": LineString(),
                    "area_base": 50.0,
                    "calibre": "0.375",
                    "material": "A 36",
                    "ruta": r"C:\PARTS\Pieza.dxf",
                }
            }

        def _pieza_pack_desde_fuente(self, src):
            return {"nombre": src["nombre"], "poly": src["poly_base"]}

    ui = FakeNesting()
    piezas = ui._build_piezas_para_renest_calibre("0.375_A 36")

    assert len(piezas) == 2
    assert ui._renest_calibre_build_info == {
        "conteo_job": {"Pieza": 3},
        "conteo_nido": {"Pieza": 2},
        "faltantes_geom": [],
        "total_esperado": 2,
        "total_generado": 2,
        "fuente_conteo": "nido",
    }


def test_worker_windows_se_lanza_sin_consola():
    """El IPC no debe crear la ventana CUI que parpadea sobre el ANS."""
    original_proc = worker_client._PROC
    original_popen = worker_client.subprocess.Popen
    original_exe = worker_client.default_worker_exe
    captured = {}

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    try:
        worker_client._PROC = None
        worker_client.default_worker_exe = lambda: Path(__file__)
        worker_client.subprocess.Popen = fake_popen
        worker_client._ensure_proc()
    finally:
        worker_client._PROC = original_proc
        worker_client.subprocess.Popen = original_popen
        worker_client.default_worker_exe = original_exe

    if os.name == "nt":
        assert captured["kwargs"]["creationflags"] == getattr(
            worker_client.subprocess,
            "CREATE_NO_WINDOW",
            0,
        )
    else:
        assert "creationflags" not in captured["kwargs"]


if __name__ == "__main__":
    test_renest_calibre_define_conteos_para_metadata()
    test_worker_windows_se_lanza_sin_consola()
    print("SMOKE OK")
