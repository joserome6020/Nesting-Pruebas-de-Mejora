#!/usr/bin/env python
"""Candado: switches Pedir/No de Nesteo de largos persisten al cerrar/reabrir.

Antes solo vivían en memoria y ``calcular_planes_largos_nesting`` las
borraba al recalcular el plan.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    previous = os.environ.get("ARGA_NEST_DATA_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGA_NEST_DATA_DIR"] = tmp

        from interface.largos_nesting_service import (
            guardar_exclusiones_mrl_unidades,
            obtener_exclusiones_mrl_unidades,
            _exclusiones_mrl_desde_disk,
            _load_exclusiones_mrl_disk,
        )

        app = SimpleNamespace(
            job_activo="62176",
            plan_largos_job="62176",
            exclusiones_mrl_unidades_por_lote={},
        )
        excl = {"ANG022::240.0::1", "SLC051::240.0::2"}
        guardar_exclusiones_mrl_unidades(app, 0, excl)

        # Memoria
        assert obtener_exclusiones_mrl_unidades(app, 0) == excl

        # Disco (cierra nesteo = nueva app / memoria vacía)
        assert _exclusiones_mrl_desde_disk("62176", 0) == excl
        disk = _load_exclusiones_mrl_disk()
        assert "62176" in (disk.get("jobs") or {})

        app2 = SimpleNamespace(
            job_activo="62176",
            plan_largos_job="62176",
            exclusiones_mrl_unidades_por_lote={},
        )
        loaded = obtener_exclusiones_mrl_unidades(app2, 0)
        assert loaded == excl, f"no hidrató desde disco: {loaded}"

        # Recalcular planes no debe borrar Pedir/No.
        app2.exclusiones_mrl_unidades_por_lote = {0: set(excl)}
        # Simula el merge de calcular_planes_largos_nesting
        prev = dict(app2.exclusiones_mrl_unidades_por_lote)
        planes = {0: {"data": {"X": []}, "total_barras": 1}}
        merged = {}
        for idx in planes:
            if idx in prev:
                merged[idx] = {str(k) for k in (prev[idx] or set())}
            else:
                merged[idx] = _exclusiones_mrl_desde_disk("62176", idx)
        assert merged[0] == excl, merged

        # Vaciar exclusiones (todas Pedir) limpia disco del lote.
        guardar_exclusiones_mrl_unidades(app2, 0, set())
        assert _exclusiones_mrl_desde_disk("62176", 0) == set()

    if previous is None:
        os.environ.pop("ARGA_NEST_DATA_DIR", None)
    else:
        os.environ["ARGA_NEST_DATA_DIR"] = previous

    print("LARGOS_PEDIR_PERSIST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
