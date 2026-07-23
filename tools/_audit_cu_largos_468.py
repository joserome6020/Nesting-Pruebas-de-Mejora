"""Audita que las 468 piezas CU existan físicamente en hojas tras largos + dedup."""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from interface.autodxf_metadata import combinar_metadata_dxf
from modules.nesting_engine.cu_inventory import validar_inventario_cu_resultado
from modules.nesting_engine.cu_largos_nesting import procesar_grupo_largos_cu
from modules.nesting_engine.efficiency_metrics import contar_piezas_grupo, contar_piezas_hoja
from modules.nesting_engine.manager import recuperar_geometria_robusta
from modules.nesting_engine.sheet_integrity import deduplicar_resultados_nesting

AUTODXF = Path(
    r"//192.168.2.80/Users/Administrator/Desktop/Grupo Arga Metals/"
    r"ARGA METALS CORPORATE SYSTEM/PRODUCTO_TEST/CLIENTE_TEST/"
    r"1000 kva de prueba/MODEL CORE FILES/AutoDXF"
)
EXCL = {"processed files", "procesados", "nesting", "__pycache__"}


def _es_virtual(nom: str) -> bool:
    n = str(nom or "")
    return (
        n.startswith("REMANENTE__")
        or n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
        or n.startswith("CU_CORTE__")
    )


def cargar_piezas_job():
    piezas = []
    bom = Counter()
    for rootd, dirs, files in os.walk(str(AUTODXF)):
        dirs[:] = [d for d in dirs if d.strip().lower() not in EXCL]
        for f in files:
            if not f.lower().endswith(".dxf"):
                continue
            ruta = os.path.join(rootd, f)
            pieza, mat, qty, cal, _ = combinar_metadata_dxf(ruta)
            m = str(mat).strip().upper()
            if m not in ("CU", "COPPER") and "COBRE" not in m:
                continue
            poly, marks = recuperar_geometria_robusta(ruta)
            if poly is None:
                raise RuntimeError(f"Sin geometría: {pieza}")
            from shapely import affinity

            minx, miny, _, _ = poly.bounds
            poly = affinity.translate(poly, -minx, -miny)
            if marks is not None and not marks.is_empty:
                marks = affinity.translate(marks, -minx, -miny)
            q = int(qty or 1)
            bom[pieza] += q
            for _ in range(q):
                piezas.append(
                    {
                        "nombre": pieza,
                        "poly": poly,
                        "marks": marks,
                        "area": float(poly.area),
                        "calibre": cal,
                        "material": mat,
                        "ruta": ruta,
                    }
                )
    return piezas, bom


def cargar_barras_inventario():
    from modules.sheets_manager import PlatesManager

    emp, _ = PlatesManager().obtener_datos_placas_divididos()
    barras = []
    for row in emp:
        if not row or len(row) < 5:
            continue
        cal, mat, pid, w, h = row[0], row[1], row[2], row[3], row[4]
        if str(mat).strip().upper() != "CU":
            continue
        try:
            w_in = float(str(w).replace('"', "").strip())
            h_in = float(str(h).replace('"', "").strip())
        except Exception:
            continue
        largo = max(w_in, h_in)
        ancho = min(w_in, h_in)
        if largo < 140 or ancho < 1.5 or ancho > 6.5:
            continue
        barras.append(
            {
                "id": str(pid),
                "w": largo * 25.4,
                "h": ancho * 25.4,
                "precio": float(row[6] or 0) if len(row) > 6 else 0.0,
                "precio_lb": float(row[7] or 0) if len(row) > 7 else 0.0,
                "origen": str(row[9] or "EMPRESA") if len(row) > 9 else "EMPRESA",
            }
        )
    return barras


def auditar_hojas(resultado: dict) -> dict:
    hojas = resultado.get("hojas") or []
    con_poligono = 0
    sin_poligono = 0
    virtuales = 0
    por_nombre = Counter()
    por_hoja = []

    for i, hoja in enumerate(hojas):
        n_real = 0
        n_virt = 0
        n_geom = 0
        for p in hoja.get("piezas") or []:
            nom = str(p.get("nombre") or "")
            if _es_virtual(nom):
                virtuales += 1
                n_virt += 1
                continue
            pols = p.get("poligonos") or []
            if pols and pols[0] and len(pols[0]) >= 3:
                con_poligono += 1
                n_geom += 1
            else:
                sin_poligono += 1
            por_nombre[nom] += 1
            n_real += 1
        por_hoja.append(
            {
                "idx": i,
                "placa_id": hoja.get("placa_id"),
                "reales": n_real,
                "con_geom": n_geom,
                "virtuales": n_virt,
                "ui_count": contar_piezas_hoja(hoja),
            }
        )

    return {
        "hojas": len(hojas),
        "con_poligono": con_poligono,
        "sin_poligono": sin_poligono,
        "virtuales": virtuales,
        "ui_total": contar_piezas_grupo(resultado),
        "por_nombre": por_nombre,
        "por_hoja": por_hoja,
    }


def main():
    piezas, bom = cargar_piezas_job()
    barras = cargar_barras_inventario()
    print("=== ENTRADA ===")
    print(f"piezas_pack: {len(piezas)}")
    print(f"tipos BOM: {len(bom)} | suma BOM: {sum(bom.values())}")
    print(f"barras CU inventario: {len(barras)}")

    _, resultado = procesar_grupo_largos_cu(
        "0.25_CU",
        piezas,
        barras,
        exigir_colocacion_total=True,
    )
    if resultado.get("error"):
        print("ERROR motor:", resultado["error"])
        return 1

    pre = auditar_hojas(resultado)
    print("\n=== TRAS MOTOR (antes dedup UI) ===")
    print(f"hojas: {pre['hojas']} | piezas reales con polígono: {pre['con_poligono']}")
    print(f"sin polígono: {pre['sin_poligono']} | virtuales (cortes): {pre['virtuales']}")
    print(f"contador UI (efficiency_metrics): {pre['ui_total']}")

    ok_pre, msg_pre = validar_inventario_cu_resultado(piezas, resultado)
    print(f"validar_inventario_cu: {'OK' if ok_pre else 'FALLO'} {msg_pre}")

    res_wrap = {"0.25_CU": resultado}
    deduplicar_resultados_nesting(res_wrap)
    post = auditar_hojas(res_wrap["0.25_CU"])

    print("\n=== TRAS deduplicar_resultados_nesting (como la UI) ===")
    print(f"hojas: {post['hojas']} | piezas reales con polígono: {post['con_poligono']}")
    print(f"sin polígono: {post['sin_poligono']} | virtuales (cortes): {post['virtuales']}")
    print(f"contador UI (efficiency_metrics): {post['ui_total']}")

    ok_post, msg_post = validar_inventario_cu_resultado(piezas, res_wrap["0.25_CU"])
    print(f"validar_inventario_cu: {'OK' if ok_post else 'FALLO'} {msg_post}")

    if post["por_nombre"] != bom:
        faltan = {k: bom[k] - post["por_nombre"].get(k, 0) for k in bom if post["por_nombre"].get(k, 0) != bom[k]}
        sobran = {k: post["por_nombre"][k] - bom.get(k, 0) for k in post["por_nombre"] if post["por_nombre"][k] != bom.get(k, 0)}
        print("\n=== DIFF BOM vs hojas ===")
        if faltan:
            print("faltan:", dict(list(faltan.items())[:10]))
        if sobran:
            print("sobran:", dict(list(sobran.items())[:10]))
    else:
        print("\nBOM por nombre: COINCIDE 100% con hojas")

    suma_hojas = sum(r["ui_count"] for r in post["por_hoja"])
    print(f"\nsuma contadores por barra: {suma_hojas}")
    ok_final = (
        len(piezas) == 468
        and post["con_poligono"] == 468
        and post["ui_total"] == 468
        and post["sin_poligono"] == 0
        and ok_post
        and post["por_nombre"] == bom
    )
    print("\n=== VEREDICTO ===")
    print("TODAS LAS 468 EXISTEN EN NESTEO" if ok_final else "HAY DISCREPANCIA — revisar arriba")
    return 0 if ok_final else 2


if __name__ == "__main__":
    raise SystemExit(main())
