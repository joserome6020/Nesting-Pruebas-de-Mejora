"""
Sincroniza overlays visuales (REF__, TATUAJE__, RETAZO_GUILLOTINA__) entre placa madre y RTZ.
Tras transferencias o re-nesteo, los overlays se regeneran desde las hojas RTZ asociadas.
"""

import copy

from shapely.geometry import Polygon, LineString
from shapely import affinity
from shapely.ops import unary_union

from .geometry_parser import generar_texto_vectorial


def _overlay_ligado_a_rtz(nombre: str, rtz_id: str) -> bool:
    n = str(nombre or "")
    rid = str(rtz_id or "")
    if not rid or rid not in n:
        return False
    return (
        n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
    )


def _piece_name_base(nombre: str) -> str:
    return (
        str(nombre or "")
        .replace("REF__", "")
        .replace("TATUAJE__", "")
        .replace("RETAZO_GUILLOTINA__", "")
        .replace("REMANENTE__", "")
        .strip()
    )


def _es_virtual(nombre: str) -> bool:
    n = str(nombre or "")
    return (
        n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
        or n.startswith("REMANENTE__")
    )


def _rtz_hojas_de_madre(hojas, idx_madre):
    out = []
    j = idx_madre + 1
    while j < len(hojas) and (hojas[j] or {}).get("es_retazo"):
        out.append(hojas[j])
        j += 1
    return out


def _inferir_global_rtz(madre, rtz_hoja):
    gx = rtz_hoja.get("global_x")
    gy = rtz_hoja.get("global_y")
    if gx is not None and gy is not None:
        return float(gx), float(gy)

    rtz_id = str(rtz_hoja.get("placa_id", "") or "")
    for p in (madre or {}).get("piezas") or []:
        nom = str(p.get("nombre", "") or "")
        if nom == f"RETAZO_GUILLOTINA__{rtz_id}":
            poly = (p.get("poligonos") or [[]])[0]
            if poly:
                return float(min(pt[0] for pt in poly)), float(min(pt[1] for pt in poly))

    for p_ref in (madre or {}).get("piezas") or []:
        nom = str(p_ref.get("nombre", "") or "")
        if not nom.startswith("REF__"):
            continue
        base = _piece_name_base(nom)
        for p_rtz in (rtz_hoja or {}).get("piezas") or []:
            if _es_virtual(p_rtz.get("nombre", "")):
                continue
            if _piece_name_base(p_rtz.get("nombre", "")) != base:
                continue
            poly_ref = (p_ref.get("poligonos") or [[]])[0]
            poly_rtz = (p_rtz.get("poligonos") or [[]])[0]
            if poly_ref and poly_rtz:
                return (
                    float(min(pt[0] for pt in poly_ref)) - float(min(pt[0] for pt in poly_rtz)),
                    float(min(pt[1] for pt in poly_ref)) - float(min(pt[1] for pt in poly_rtz)),
                )
    return 0.0, 0.0


def _quitar_overlays_rtz(madre, rtz_id):
    madre["piezas"] = [
        p
        for p in (madre.get("piezas") or [])
        if not _overlay_ligado_a_rtz(p.get("nombre", ""), rtz_id)
    ]


def _calibre_material(madre, rtz_hoja):
    cal = ""
    mat = ""
    for src in (rtz_hoja, madre):
        for p in (src or {}).get("piezas") or []:
            if not _es_virtual(p.get("nombre", "")):
                cal = cal or str(p.get("calibre", "") or "")
                mat = mat or str(p.get("material", "") or "")
    return cal, mat


def _poly_borde_local(rtz_hoja):
    coords = (rtz_hoja or {}).get("poly_borde_retazo")
    if not coords:
        return None
    try:
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            return None
        return poly
    except Exception:
        return None


def _reconstruir_overlays_rtz_en_madre(madre, rtz_hoja):
    if not madre or not rtz_hoja or madre.get("es_retazo"):
        return

    rtz_id = str(rtz_hoja.get("placa_id", "") or "")
    if not rtz_id:
        return

    _quitar_overlays_rtz(madre, rtz_id)

    gx, gy = _inferir_global_rtz(madre, rtz_hoja)
    rtz_hoja["global_x"] = gx
    rtz_hoja["global_y"] = gy

    rw = float(rtz_hoja.get("placa_w", 0) or 0)
    rh = float(rtz_hoja.get("placa_h", 0) or 0)
    if rw <= 0 or rh <= 0:
        return

    req_cal, req_mat = _calibre_material(madre, rtz_hoja)
    tipo = str(rtz_hoja.get("retazo_tipo", "") or "").upper()
    if not tipo:
        poly_t = _poly_borde_local(rtz_hoja)
        area_rect = rw * rh
        if poly_t is not None and area_rect > 0 and float(poly_t.area) < area_rect * 0.98:
            tipo = "HOLE"
        else:
            tipo = "SOBRANTE"

    # Piezas REF__ desde RTZ
    for p_acc in (rtz_hoja.get("piezas") or []):
        nom = str(p_acc.get("nombre", "") or "")
        if nom.startswith("REMANENTE__") or nom.startswith("TATUAJE__"):
            continue

        p_clon = copy.deepcopy(p_acc)
        p_clon["nombre"] = f"REF__{p_clon['nombre']}"

        if p_clon.get("poligonos"):
            nuevos_polys = []
            for pol_coords in p_clon["poligonos"]:
                try:
                    nuevos_polys.append(
                        list(affinity.translate(Polygon(pol_coords), xoff=gx, yoff=gy).exterior.coords)
                    )
                except Exception:
                    nuevos_polys.append(pol_coords)
            p_clon["poligonos"] = nuevos_polys

        if p_clon.get("marcas"):
            nuevas_marcas = []
            for line_coords in p_clon["marcas"]:
                try:
                    nuevas_marcas.append(
                        list(affinity.translate(LineString(line_coords), xoff=gx, yoff=gy).coords)
                    )
                except Exception:
                    nuevas_marcas.append(line_coords)
            p_clon["marcas"] = nuevas_marcas

        madre.setdefault("piezas", []).append(p_clon)

    espacio_libre = _poly_borde_local(rtz_hoja)
    if espacio_libre is None:
        try:
            espacio_libre = Polygon([(0, 0), (rw, 0), (rw, rh), (0, rh)])
        except Exception:
            return

    polys_restar = []
    for p_acc in (madre.get("piezas") or []):
        nom = str(p_acc.get("nombre", "") or "")
        if (
            nom.startswith("REMANENTE__")
            or nom.startswith("TATUAJE__")
            or nom.startswith("RETAZO_GUILLOTINA__")
            or nom.startswith("REF__")
        ):
            continue
        try:
            p_poly = Polygon((p_acc.get("poligonos") or [[]])[0]).buffer(10.0)
            p_poly_local = affinity.translate(p_poly, xoff=-gx, yoff=-gy)
            if espacio_libre.intersects(p_poly_local):
                polys_restar.append(p_poly_local)
        except Exception:
            pass

    if polys_restar:
        try:
            espacio_libre = espacio_libre.difference(unary_union(polys_restar))
        except Exception:
            pass

    cx_local, cy_local = rw / 2.0, rh / 2.0
    w_disp, h_disp = rw * 0.5, rh * 0.5

    if not espacio_libre.is_empty:
        if espacio_libre.geom_type == "MultiPolygon":
            best_poly = max(espacio_libre.geoms, key=lambda a: a.area)
        elif espacio_libre.geom_type == "Polygon":
            best_poly = espacio_libre
        else:
            best_poly = None

        if best_poly is not None:
            minx_e, miny_e, maxx_e, maxy_e = best_poly.bounds
            w_disp, h_disp = (maxx_e - minx_e), (maxy_e - miny_e)
            cx_local, cy_local = best_poly.centroid.x, best_poly.centroid.y
            if not best_poly.contains(best_poly.centroid):
                rep_point = best_poly.representative_point()
                cx_local, cy_local = rep_point.x, rep_point.y
                w_disp *= 0.6
                h_disp *= 0.6

    cx_t_global = gx + cx_local
    cy_t_global = gy + cy_local
    w_texto = max(50, min(w_disp * 0.85, 400))
    h_texto = max(15, min(h_disp * 0.85, 40))
    marks_t_global = generar_texto_vectorial(rtz_id, cx_t_global, cy_t_global, w_texto, h_texto)
    dummy_p_global = [
        [
            (cx_t_global - 1, cy_t_global - 1),
            (cx_t_global + 1, cy_t_global - 1),
            (cx_t_global + 1, cy_t_global + 1),
            (cx_t_global - 1, cy_t_global + 1),
            (cx_t_global - 1, cy_t_global - 1),
        ]
    ]

    if tipo == "HOLE":
        madre["piezas"].append(
            {
                "nombre": f"TATUAJE__{rtz_id}",
                "poligonos": dummy_p_global,
                "marcas": marks_t_global,
                "area": 0.0,
                "calibre": req_cal,
                "material": req_mat,
            }
        )
        return

    if tipo != "SOBRANTE":
        # Workspaces viejos sin retazo_tipo: si hay guillotina previa era SOBRANTE.
        tipo = "SOBRANTE"

    min_x, min_y = gx, gy
    max_x, max_y = gx + rw, gy + rh
    poly_g = [
        [
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
            (min_x, min_y),
        ]
    ]

    madre["piezas"].append(
        {
            "nombre": f"RETAZO_GUILLOTINA__{rtz_id}",
            "poligonos": poly_g,
            "marcas": [],
            "area": 0.0,
            "calibre": req_cal,
            "material": req_mat,
        }
    )
    madre["piezas"].append(
        {
            "nombre": f"TATUAJE__{rtz_id}",
            "poligonos": dummy_p_global,
            "marcas": marks_t_global,
            "area": 0.0,
            "calibre": req_cal,
            "material": req_mat,
        }
    )


def sincronizar_overlays_grupo(hojas):
    if not isinstance(hojas, list):
        return
    for i, hoja in enumerate(hojas):
        if not isinstance(hoja, dict) or hoja.get("es_retazo"):
            continue
        for rtz in _rtz_hojas_de_madre(hojas, i):
            _reconstruir_overlays_rtz_en_madre(hoja, rtz)


def sincronizar_overlays_resultados(resultados_nesting):
    if not isinstance(resultados_nesting, dict):
        return
    for _, grupo in resultados_nesting.items():
        if not isinstance(grupo, dict) or "error" in grupo:
            continue
        hojas = grupo.get("hojas")
        if isinstance(hojas, list):
            sincronizar_overlays_grupo(hojas)
