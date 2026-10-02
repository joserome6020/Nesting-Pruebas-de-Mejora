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

        with patch.object(imp, "_snapshot_bd", return_value=(hashes_iguales, [])), patch.object(
            imp, "importar_lista_largos_job"
        ) as importar:
            res = imp.sincronizar_lista_largos_job_si_cambio(
                "251007", {}, rutas_candidatas=[str(carpeta)]
            )
        assert res["status"] == "sin_cambios", res
        importar.assert_not_called()

        viejo = Counter(hashes_iguales)
        viejo.pop(next(iter(viejo)))
        with patch.object(imp, "_snapshot_bd", return_value=(viejo, [])), patch.object(
            imp, "importar_lista_largos_job", return_value={"ok": True, "status": "importado"}
        ) as importar:
            res = imp.sincronizar_lista_largos_job_si_cambio(
                "251007", {}, rutas_candidatas=[str(carpeta)]
            )
        assert res["status"] == "actualizado", res
        importar.assert_called_once()
        assert importar.call_args.kwargs.get("propagar_material") is False
        cfg_import = importar.call_args.args[2]
        assert "lock_timeout" in str(cfg_import.get("options")), cfg_import


def test_sync_no_importa_csv_hi_con_piezas_de_otros_jobs():
    """Caso real 25430-HI: su CSV traía la lista consolidada de 261102/261307/…"""
    filas = [("261102-ITEM 1", "CAN009", 4.0, 16), ("261307-ITEM 3", "ANG037", 35.0, 4)]
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = _crear_job_con_csv(Path(tmp) / "25430-HI", filas)
        with patch.object(imp, "_snapshot_bd", return_value=(Counter({"viejo": 1}), [])), patch.object(
            imp, "importar_lista_largos_job"
        ) as importar:
            res = imp.sincronizar_lista_largos_job_si_cambio(
                "25430-HI", {}, rutas_candidatas=[str(carpeta)]
            )
    assert res["status"] == "csv_no_confiable", res
    assert "261102" in res["motivo"] and "261307" in res["motivo"], res
    importar.assert_not_called()


def test_sync_acepta_prefijo_del_propio_job():
    """261116-HI con piezas «261116 - ITEM 3» sí es su propio CSV."""
    filas = [("261116 - ITEM 3", "ANG037", 32.0, 4)]
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = _crear_job_con_csv(Path(tmp) / "261116-HI", filas)
        with patch.object(imp, "_snapshot_bd", return_value=(Counter({"viejo": 1}), [])), patch.object(
            imp, "importar_lista_largos_job", return_value={"ok": True, "status": "importado"}
        ) as importar:
            res = imp.sincronizar_lista_largos_job_si_cambio(
                "261116-HI", {}, rutas_candidatas=[str(carpeta)]
            )
    assert res["status"] == "actualizado", res
    importar.assert_called_once()


def test_sync_no_importa_si_la_carpeta_tiene_otro_csv_ademas_del_importado():
    filas = [("ITEM 1", "ANG037", 38.0, 4)]
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = _crear_job_con_csv(Path(tmp) / "251007", filas)
        autodxf = carpeta / "MODEL CORE FILES" / "AutoDXF"
        original = autodxf / "Lista_Perfiles_Clasificados TANK.csv"
        original.write_text("Nombre,Clasificacion,Largo (in),Cantidad\nITEM 1,ANG037,30,4", encoding="utf-8")
        imp._CARPETA_CSV_POR_JOB.pop(imp._norm_job("251007"), None)
        with patch.object(
            imp, "_snapshot_bd", return_value=(Counter({"viejo": 1}), [str(original)])
        ), patch.object(imp, "importar_lista_largos_job") as importar:
            res = imp.sincronizar_lista_largos_job_si_cambio(
                "251007", {}, rutas_candidatas=[str(carpeta)]
            )
    assert res["status"] == "csv_no_confiable", res
    assert "dos CSV" in res["motivo"], res
    importar.assert_not_called()


def test_sync_sin_csv_respeta_snapshot():
    with patch.object(imp, "_snapshot_bd", return_value=(Counter(), [])), patch.object(
        imp, "_resolver_carpeta_job_con_csv", return_value=None
    ), patch.object(imp, "importar_lista_largos_job") as importar:
        res = imp.sincronizar_lista_largos_job_si_cambio("251007", {})
    assert res["status"] == "csv_no_encontrado"
    importar.assert_not_called()


def test_sync_usa_ruta_csv_guardada_en_bd_sin_escanear_tanks():
    """Sin la ruta del ANS (export) se usa source_csv_path de BD, no el escaneo SMB."""
    filas = [("ITEM 1", "ANG037", 38.0, 4)]
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = _crear_job_con_csv(Path(tmp) / "TNK3PH-0017", filas)
        csv_bd = carpeta / "MODEL CORE FILES" / "AutoDXF" / "Lista_Largos.csv"
        hashes = Counter(
            imp._row_hash("TNK3PH-0017", r) for r in imp._leer_csv_lista_largos(csv_bd)
        )
        imp._CARPETA_CSV_POR_JOB.pop(imp._norm_job("TNK3PH-0017"), None)
        with patch.object(imp, "_snapshot_bd", return_value=(hashes, [str(csv_bd)])), patch.object(
            imp, "_buscar_carpeta_job_corporate"
        ) as escaneo:
            res = imp.sincronizar_lista_largos_job_si_cambio("TNK3PH-0017", {})
        assert res["status"] == "sin_cambios", res
        escaneo.assert_not_called()


def test_job_sin_csv_no_escanea_tanks_en_cada_nesteo():
    """Jobs sin CSV (ATC) no deben congelar el nesteo con el escaneo SMB de TANKS."""
    imp._CARPETA_CSV_POR_JOB.pop("JOB-INEXISTENTE", None)
    with patch.object(imp, "_buscar_carpeta_job_corporate", return_value=None) as escaneo:
        assert imp._resolver_carpeta_job_con_csv("JOB-INEXISTENTE") is None
    escaneo.assert_not_called()


def test_tanks_encuentra_job_a_dos_niveles():
    """Producción: TANKS/SOUTHWEST/TNK3PH-0017 (cliente/job), no solo producto/cliente/job."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "TANKS"
        carpeta = _crear_job_con_csv(root / "SOUTHWEST" / "TNK3PH-0017", [("A", "ANG037", 10.0, 1)])
        hit = imp._buscar_carpeta_job_corporate("TNK3PH-0017", roots=[root])
        assert hit == carpeta, hit


def test_tanks_dos_niveles_no_confunde_atc_con_tanque():
    """HV-ATC-261431 no debe tomar el CSV del tanque TANKS/VANTRAN/261431."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "TANKS"
        _crear_job_con_csv(root / "VANTRAN" / "261431", [("A", "ANG037", 10.0, 1)])
        assert imp._buscar_carpeta_job_corporate("HV-ATC-261431", roots=[root]) is None


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


def test_demanda_swo_mixta_completa_job_sin_bd_con_csv():
    """Un job con lista en BD y otro sin ella: no se debe omitir el segundo."""
    with patch.object(lns, "resolver_wos_fuente_swo", return_value=PARES_SWO), patch.object(
        lns, "sincronizar_jobs_desde_csv", return_value={}
    ), patch.object(
        lns,
        "_filas_desde_bd_para_wo",
        side_effect=lambda cur, job, wo: [{"job": job}] if job == "251007" else [],
    ), patch.object(
        lns,
        "_filas_desde_csv_para_pares",
        side_effect=lambda app, pares: [{"job": j, "origen": "csv"} for j, _w in pares],
    ):
        filas, origen = lns._filas_demanda_swo(MagicMock(), MagicMock(), "SWO-099")
    assert origen == "swo_bd"
    assert [f["job"] for f in filas] == ["251007", "251008"], filas


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
    test_sync_no_importa_csv_hi_con_piezas_de_otros_jobs()
    test_sync_acepta_prefijo_del_propio_job()
    test_sync_no_importa_si_la_carpeta_tiene_otro_csv_ademas_del_importado()
    test_sync_sin_csv_respeta_snapshot()
    test_sync_usa_ruta_csv_guardada_en_bd_sin_escanear_tanks()
    test_job_sin_csv_no_escanea_tanks_en_cada_nesteo()
    test_tanks_encuentra_job_a_dos_niveles()
    test_tanks_dos_niveles_no_confunde_atc_con_tanque()
    test_demanda_swo_sincroniza_csv_antes_de_leer_bd()
    test_demanda_swo_mixta_completa_job_sin_bd_con_csv()
    test_plan_canonico_swo_sincroniza_antes_de_generar()
    test_boton_recalcular_swo_sincroniza_cada_job()
    print("OK")
