"""Candado 2026-10-02: la SWO siempre parte del CSV de AutoDXF vigente de cada job.

Caso real: cambiaban largos en el CSV de un job ya exportado y la SWO seguía
pidiendo material con el snapshot viejo de ``lista_largos_job``. Ni el nesting,
ni el plan canónico del export, ni el botón «Recalcular desde CSV» (que buscaba
una carpeta llamada «SWO-xxx») refrescaban esa tabla.
"""
from __future__ import annotations

import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from interface import largos_nesting_service as lns  # noqa: E402
from modules import lista_largos_importer as imp  # noqa: E402

PARES_SWO = [("251007", "W.O. 10 X2"), ("251008", "W.O. 11 X3")]


def _crear_job_con_csv(base: Path, filas: list[tuple[str, str, float, int]]) -> Path:
    autodxf = base / "MODEL CORE FILES" / "AutoDXF"
    autodxf.mkdir(parents=True)
    lineas = ["Nombre,Clasificacion,Largo (in),Cantidad"]
    lineas += [f"{n},{c},{l},{q}" for n, c, l, q in filas]
    (autodxf / "Lista_Largos.csv").write_text("\n".join(lineas), encoding="utf-8")
    return base


def test_sync_reimporta_solo_si_el_csv_cambio():
    filas = [("ITEM 1", "ANG037", 38.0, 4), ("ITEM 2", "CH010", 24.5, 2)]
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = _crear_job_con_csv(Path(tmp) / "251007", filas)
        rows_csv = imp._leer_csv_lista_largos(
            carpeta / "MODEL CORE FILES" / "AutoDXF" / "Lista_Largos.csv"
        )
        hashes_iguales = Counter(imp._row_hash("251007", r) for r in rows_csv)

        with patch.object(imp, "_row_hashes_bd", return_value=hashes_iguales), patch.object(
            imp, "importar_lista_largos_job"
        ) as importar:
            res = imp.sincronizar_lista_largos_job_si_cambio(
                "251007", {}, rutas_candidatas=[str(carpeta)]
            )
        assert res["status"] == "sin_cambios", res
        importar.assert_not_called()

        viejo = Counter(hashes_iguales)
        viejo.pop(next(iter(viejo)))
        with patch.object(imp, "_row_hashes_bd", return_value=viejo), patch.object(
            imp, "importar_lista_largos_job", return_value={"ok": True, "status": "importado"}
        ) as importar:
            res = imp.sincronizar_lista_largos_job_si_cambio(
                "251007", {}, rutas_candidatas=[str(carpeta)]
            )
        assert res["status"] == "actualizado", res
        importar.assert_called_once()
        assert importar.call_args.kwargs.get("propagar_material") is False


def test_sync_sin_csv_respeta_snapshot():
    with patch.object(imp, "_resolver_carpeta_job_con_csv", return_value=None), patch.object(
        imp, "importar_lista_largos_job"
    ) as importar:
        res = imp.sincronizar_lista_largos_job_si_cambio("251007", {})
    assert res["status"] == "csv_no_encontrado"
    importar.assert_not_called()


def test_demanda_swo_sincroniza_csv_antes_de_leer_bd():
    orden: list[str] = []
    with patch.object(lns, "resolver_wos_fuente_swo", return_value=PARES_SWO), patch.object(
        lns,
        "sincronizar_jobs_desde_csv",
        side_effect=lambda app, jobs: orden.append(f"sync:{','.join(jobs)}") or {},
    ), patch.object(
        lns,
        "_filas_desde_bd_para_wo",
        side_effect=lambda cur, job, wo: orden.append(f"bd:{job}") or [{"job": job}],
    ):
        filas, origen = lns._filas_demanda_swo(MagicMock(), MagicMock(), "SWO-099")
    assert origen == "swo_bd"
    assert orden[0] == "sync:251007,251008", orden
    assert orden[1:] == ["bd:251007", "bd:251008"], orden


def test_plan_canonico_swo_sincroniza_antes_de_generar():
    orden: list[str] = []
    conn = MagicMock()
    with patch.object(lns, "_conexion_bd", return_value=(conn, dict)), patch.object(
        lns, "resolver_wos_fuente_swo", return_value=PARES_SWO
    ), patch.object(
        lns,
        "sincronizar_jobs_desde_csv",
        side_effect=lambda app, jobs: orden.append("sync") or {},
    ), patch(
        "api_server._ll_obtener_o_generar_plan",
        side_effect=lambda *a, **k: orden.append("plan") or ({"data": {}}, {}),
    ):
        lns.cargar_plan_largos("SWO-099", "SWO")
    assert orden == ["sync", "plan"], orden


def test_boton_recalcular_swo_sincroniza_cada_job():
    with patch.object(lns, "resolver_wos_fuente_swo", return_value=PARES_SWO), patch.object(
        lns,
        "sincronizar_jobs_desde_csv",
        return_value={"251007": "actualizado", "251008": "sin_cambios"},
    ) as sync:
        ok, msg = lns._sincronizar_lista_largos_job_desde_csv(MagicMock(), None, "SWO-099")
    assert ok is True, msg
    assert sync.call_args.args[1] == ["251007", "251008"]


if __name__ == "__main__":
    test_sync_reimporta_solo_si_el_csv_cambio()
    test_sync_sin_csv_respeta_snapshot()
    test_demanda_swo_sincroniza_csv_antes_de_leer_bd()
    test_plan_canonico_swo_sincroniza_antes_de_generar()
    test_boton_recalcular_swo_sincroniza_cada_job()
    print("OK")
