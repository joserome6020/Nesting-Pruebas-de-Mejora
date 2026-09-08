"""Candado: acero unificado en NESTEO DXF/{DXF,STEP}; tipo_corte PQART vacío."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.nesting_engine.exporter import (  # noqa: E402
    RUTA_NESTEO_DXF,
    RUTA_NESTEOS_COBRE,
    _nombre_dxf_plasma_unificado,
    _normalizar_tipo_corte_pqart,
    step_universal_sin_camas_activo,
)


def _build_rutas_like_export(job_root: str) -> dict:
    """Replica el dict de rutas de exportar_resultados_a_dxf (acero unificado)."""
    from modules.nesting_engine.exporter import step_universal_sin_camas_activo as uni

    rutas = {
        "nesteos_cobre_dxf": os.path.join(job_root, RUTA_NESTEOS_COBRE, "DXF"),
        "nesteos_cobre_step": os.path.join(job_root, RUTA_NESTEOS_COBRE, "STEP"),
        "nestee_dxf": os.path.join(job_root, RUTA_NESTEO_DXF, "DXF"),
    }
    for k in (
        "cama_laser_dxf",
        "cama_laser_12kw_dxf",
        "robot_laser_dxf",
        "robot_plasma_dxf",
    ):
        rutas[k] = rutas["nestee_dxf"]
    if uni():
        rutas["nestee_step"] = os.path.join(job_root, RUTA_NESTEO_DXF, "STEP")
        for k in (
            "cama_laser_step",
            "cama_laser_12kw_step",
            "robot_laser_step",
            "robot_plasma_step",
        ):
            rutas[k] = rutas["nestee_step"]
    return rutas


def test_rutas_acero_apuntan_a_nestee_dxf():
    assert RUTA_NESTEO_DXF == "NESTEO DXF"
    with tempfile.TemporaryDirectory() as tmp:
        rutas = _build_rutas_like_export(tmp)
        steel_dxf = os.path.normpath(os.path.join(tmp, "NESTEO DXF", "DXF"))
        steel_step = os.path.normpath(os.path.join(tmp, "NESTEO DXF", "STEP"))
        assert os.path.normpath(rutas["nestee_dxf"]) == steel_dxf
        assert os.path.normpath(rutas["cama_laser_dxf"]) == steel_dxf
        assert os.path.normpath(rutas["robot_laser_dxf"]) == steel_dxf
        assert os.path.normpath(rutas["robot_plasma_dxf"]) == steel_dxf
        if step_universal_sin_camas_activo():
            assert os.path.normpath(rutas["nestee_step"]) == steel_step
            assert os.path.normpath(rutas["robot_laser_step"]) == steel_step
        # Cobre sigue separado
        assert "NESTEOS DE COBRE" in rutas["nesteos_cobre_dxf"]
        assert "NESTEO DXF" not in rutas["nesteos_cobre_dxf"]


def test_tipo_corte_pqart_vacio():
    assert _normalizar_tipo_corte_pqart("ROBOT LASER + MINI NEST") == ""
    assert _normalizar_tipo_corte_pqart("CAMA LASER SIN MINI NEST") == ""
    assert _normalizar_tipo_corte_pqart("ROBOT PLASMA") == ""
    assert _normalizar_tipo_corte_pqart("NESTEOS DE COBRE") == ""


def test_nombre_plasma_no_colisiona():
    assert _nombre_dxf_plasma_unificado("SWO-1_0.25_H1.dxf") == "SWO-1_0.25_H1_PLASMA.dxf"
    assert (
        _nombre_dxf_plasma_unificado("SWO-1_0.25_H1_PLASMA.dxf")
        == "SWO-1_0.25_H1_PLASMA.dxf"
    )


if __name__ == "__main__":
    test_rutas_acero_apuntan_a_nestee_dxf()
    test_tipo_corte_pqart_vacio()
    test_nombre_plasma_no_colisiona()
    print("OK test_nestee_dxf_unificado")
