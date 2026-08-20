"""Candado: Crear STEPs / despachador usa 1 STEP plano (sin Cama A/B ni offsets).

Bug 2026-08-20: Crear STEPs reutilizó el despachador legacy y regeneró
STEP/Cama A + STEP/Cama B con anclas robot. El ANS lleva tiempo en
STEP_UNIVERSAL_SIN_CAMAS (1 STEP por DXF, coords 1:1).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from despachador_nocturno import (  # noqa: E402
    clasificar_familia,
    resolver_destinos_step,
    resolver_destinos_step_cobre,
)


def test_universal_un_destino_plano_sin_offset():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch("despachador_nocturno._step_universal_sin_camas", return_value=True):
            destinos = resolver_destinos_step(tmp)
        assert len(destinos) == 1, destinos
        d = destinos[0]
        assert d["tag"] == "UNIVERSAL"
        assert Path(d["dir"]) == Path(tmp)
        assert d["origen"] == "NONE"
        assert float(d["off_x"]) == 0.0
        assert float(d["off_y"]) == 0.0
        assert float(d["off_z"]) == 0.0
        assert not (Path(tmp) / "Cama A").is_dir()
        assert not (Path(tmp) / "Cama B").is_dir()


def test_legacy_sigue_creando_cama_a_b_si_flag_off():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch("despachador_nocturno._step_universal_sin_camas", return_value=False):
            destinos = resolver_destinos_step(tmp)
        assert len(destinos) == 2, destinos
        tags = {d["tag"] for d in destinos}
        assert tags == {"A", "B"}
        assert (Path(tmp) / "Cama A").is_dir()
        assert (Path(tmp) / "Cama B").is_dir()
        for d in destinos:
            assert abs(float(d["off_x"])) > 0


def test_universal_incluye_cama_laser_y_cobre_sin_camas():
    with mock.patch("despachador_nocturno._step_universal_sin_camas", return_value=True):
        assert clasificar_familia("CAMA LASER SIN MINI NEST") == "CAMA_LASER"
        assert clasificar_familia("ROBOT LASER + MINI NEST") == "LASER"
        assert clasificar_familia("ROBOT PLASMA") == "PLASMA"
        assert clasificar_familia("NESTEOS DE COBRE") == "COBRE"
    with mock.patch("despachador_nocturno._step_universal_sin_camas", return_value=False):
        assert clasificar_familia("CAMA LASER SIN MINI NEST") is None

    with tempfile.TemporaryDirectory() as tmp:
        destinos = resolver_destinos_step_cobre(tmp)
        assert len(destinos) == 1
        assert destinos[0]["origen"] == "TR"
        assert float(destinos[0]["off_x"]) == 0.0


if __name__ == "__main__":
    test_universal_un_destino_plano_sin_offset()
    test_legacy_sigue_creando_cama_a_b_si_flag_off()
    test_universal_incluye_cama_laser_y_cobre_sin_camas()
    print("OK test_step_universal_sin_camas_despachador")
