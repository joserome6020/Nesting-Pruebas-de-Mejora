"""
Integridad de hojas de nesting: restos con piezas repetidas, reconciliación y deduplicación.
"""

from __future__ import annotations

import copy
from collections import Counter, deque
from typing import List, Tuple

DEFAULT_KERF_IN = 0.3


def _piece_name_base(nombre: str) -> str:
    s = str(nombre or "").strip()
    if "__" in s:
        head, tail = s.split("__", 1)
        head = head.strip()
        tail = tail.strip()
        if tail and (head.startswith("W.O.") or head.upper().startswith("SWO-")):
            s = tail
    return (
        s.replace("REF__", "")
        .replace("TATUAJE__", "")
        .replace("RETAZO_GUILLOTINA__", "")
        .replace("CU_CORTE__", "")
        .replace("REMANENTE__", "")
        .strip()
    )


def _es_pieza_real_nombre(nombre: str) -> bool:
    n = str(nombre or "")
    return not (
        n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
        or n.startswith("CU_CORTE__")
        or n.startswith("REMANENTE__")
    )


def _es_grupo_cu(clave: str = "", hoja=None) -> bool:
    if isinstance(hoja, dict) and hoja.get("modo_largos_cu"):
        return True
    clv = str(clave or "").strip().upper()
    return clv.endswith("_CU") or "| CU" in clv or clv.endswith("|CU")


def kerf_efectivo_hoja(hoja, clave: str = "", kerf_global: float = DEFAULT_KERF_IN) -> float:
    """Cobre/largos CU: kerf 0. Acero y demás: mínimo 0.3 in (o kerf_global si mayor)."""
    if _es_grupo_cu(clave, hoja):
        return 0.0
    try:
        k = float((hoja or {}).get("kerf_usado", kerf_global) or 0.0)
    except Exception:
        k = 0.0
    min_k = float(kerf_global or DEFAULT_KERF_IN)
    if min_k < DEFAULT_KERF_IN:
        min_k = DEFAULT_KERF_IN
    if k < min_k:
        k = min_k
    return k


def normalizar_kerf_hojas(hojas, clave: str = "", kerf_global: float = DEFAULT_KERF_IN) -> None:
    for h in hojas or []:
        if not isinstance(h, dict):
            continue
        h["kerf_usado"] = kerf_efectivo_hoja(h, clave=clave, kerf_global=kerf_global)


def piezas_colocadas_en_hoja(hoja) -> list:
    out = []
    for p in (hoja or {}).get("piezas") or []:
        nom = str((p or {}).get("nombre", "") or "")
        if nom.startswith("REMANENTE__"):
            continue
        out.append(p)
    return out


def piezas_reales_en_hoja(hoja) -> list:
    return [p for p in piezas_colocadas_en_hoja(hoja) if _es_pieza_real_nombre(p.get("nombre", ""))]


def calcular_restos_desde_colocados(pool, hoja) -> list:
    """
    Restos = piezas del pool que no quedaron en la hoja.
    Usa conteo por nombre para soportar cantidades > 1 con el mismo nombre.
    """
    colocados = piezas_colocadas_en_hoja(hoja)
    pendiente = Counter(str(p.get("nombre") or "") for p in colocados)
    restos = []
    for p in pool or []:
        nom = str(p.get("nombre") or "")
        if pendiente.get(nom, 0) > 0:
            pendiente[nom] -= 1
            continue
        restos.append(copy.deepcopy(p))
    return restos


def contar_piezas_reales_hoja(hoja) -> Counter:
    return Counter(
        str(p.get("nombre") or "")
        for p in piezas_reales_en_hoja(hoja)
        if str(p.get("nombre") or "").strip()
    )


def calcular_restos_por_delta(pool, delta: Counter) -> list:
    """
    Quita del pool solo las piezas recién colocadas (delta de conteos).
    No usar calcular_restos_desde_colocados contra una madre que ya tenía piezas:
    eso “come” instancias homónimas que aún faltan por nestear.
    """
    restante = Counter(delta or {})
    restos = []
    for p in pool or []:
        nom = str(p.get("nombre") or "")
        if restante.get(nom, 0) > 0:
            restante[nom] -= 1
            continue
        restos.append(copy.deepcopy(p))
    return restos


def remover_colocados_del_pool(pool, hoja) -> list:
    """Devuelve pool sin las piezas que ya están en la hoja (FIFO por nombre)."""
    return calcular_restos_desde_colocados(pool, hoja)


def batch_reempaque_desde_hoja(hoja, piezas_origen) -> list:
    """Reconstruye el lote a reempaquetar respetando cantidades por nombre."""
    colocados = piezas_colocadas_en_hoja(hoja)
    if not colocados:
        return []

    necesario = Counter(str(p.get("nombre") or "") for p in colocados)
    origen_por_nombre: dict[str, list] = {}
    for p in piezas_origen or []:
        nom = str(p.get("nombre") or "")
        origen_por_nombre.setdefault(nom, []).append(p)

    batch = []
    for nom, cantidad in necesario.items():
        fuentes = origen_por_nombre.get(nom, [])
        for i in range(cantidad):
            if i < len(fuentes):
                batch.append(copy.deepcopy(fuentes[i]))
    return batch


def fingerprint_layout_hoja(hoja, tol_mm: float = 2.0) -> tuple:
    """Huella espacial de piezas reales (nombre + bbox cuantizado)."""
    entradas = []
    for p in (hoja or {}).get("piezas") or []:
        nom = str(p.get("nombre") or "")
        if not _es_pieza_real_nombre(nom):
            continue
        pols = p.get("poligonos") or []
        if not pols or not pols[0]:
            continue
        xs = [float(pt[0]) for pt in pols[0]]
        ys = [float(pt[1]) for pt in pols[0]]
        if not xs or not ys:
            continue
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        entradas.append(
            (
                nom,
                round(minx / tol_mm),
                round(miny / tol_mm),
                round((maxx - minx) / tol_mm),
                round((maxy - miny) / tol_mm),
            )
        )
    return tuple(sorted(entradas))


def _bloques_madre_rtz(hojas):
    bloques = []
    i = 0
    while i < len(hojas or []):
        h = hojas[i]
        if not isinstance(h, dict):
            i += 1
            continue
        if h.get("es_retazo"):
            i += 1
            continue
        madre = h
        rtzs = []
        j = i + 1
        while j < len(hojas) and isinstance(hojas[j], dict) and hojas[j].get("es_retazo"):
            rtzs.append(hojas[j])
            j += 1
        bloques.append((madre, rtzs))
        i = j
    return bloques


def _build_buckets(piezas_origen):
    buckets: dict[str, deque] = {}
    for p in piezas_origen or []:
        nom = str(p.get("nombre") or "")
        if not nom:
            continue
        buckets.setdefault(nom, deque()).append(p)
    return buckets


def _clone_buckets(buckets: dict[str, deque]) -> dict[str, deque]:
    return {k: deque(v) for k, v in buckets.items()}


def _consumir_fifo(buckets: dict[str, deque], nombre: str) -> bool:
    nom = str(nombre or "")
    if not nom:
        return False
    if buckets.get(nom) and buckets[nom]:
        buckets[nom].popleft()
        return True
    base = _piece_name_base(nom)
    if buckets.get(base) and buckets[base]:
        buckets[base].popleft()
        return True
    for key, dq in buckets.items():
        if dq and _piece_name_base(key) == base:
            dq.popleft()
            return True
    return False


def _bloque_consumible(buckets: dict[str, deque], madre, rtzs) -> bool:
    trial = _clone_buckets(buckets)
    for h in [madre] + list(rtzs or []):
        for p in piezas_reales_en_hoja(h):
            if not _consumir_fifo(trial, str(p.get("nombre") or "")):
                return False
    return True


def _commit_bloque(buckets: dict[str, deque], madre, rtzs) -> None:
    for h in [madre] + list(rtzs or []):
        for p in piezas_reales_en_hoja(h):
            _consumir_fifo(buckets, str(p.get("nombre") or ""))


def reconciliar_hojas_grupo(piezas_origen, hojas):
    """
    Elimina bloques madre+RTZ que colocan piezas ya consumidas (placas duplicadas por error de restos).
    """
    if not hojas:
        return list(hojas or [])
    buckets = _build_buckets(piezas_origen)
    if not buckets:
        return list(hojas)

    bloques = _bloques_madre_rtz(hojas)
    out: list = []
    for madre, rtzs in bloques:
        if _bloque_consumible(buckets, madre, rtzs):
            _commit_bloque(buckets, madre, rtzs)
            out.append(madre)
            out.extend(rtzs)
    return out


def asegurar_identidad_hojas(hojas, clave: str = "") -> None:
    """Asigna índice de lista y sheet_uid estable para placas con el mismo placa_id."""
    clv = str(clave or "").strip()
    for i, h in enumerate(hojas or []):
        if not isinstance(h, dict):
            continue
        h["_nest_list_idx"] = i
        if h.get("es_retazo"):
            continue
        if str(h.get("sheet_uid") or "").strip():
            continue
        pid = str(h.get("placa_id") or "STOCK").strip() or "STOCK"
        h["sheet_uid"] = f"{clv}::{pid}::{i}" if clv else f"{pid}::{i}"


def deduplicar_hojas_grupo(hojas):
    """
    Reensambla bloques madre+RTZ sin colapsar hojas repetidas legítimas (qty>10, mismo layout).

    La deduplicación agresiva por huella idéntica eliminaba instancias válidas en lotes X10+.
    reconciliar_hojas_grupo ya descarta placas fantasma que reutilizan piezas consumidas.
    """
    hojas = list(hojas or [])
    madres = [h for h in hojas if isinstance(h, dict) and not h.get("es_retazo")]
    if madres and all(h.get("modo_largos_cu") for h in madres):
        return hojas

    bloques = _bloques_madre_rtz(hojas)
    if not bloques:
        return list(hojas or [])

    out: list = []
    for madre, rtzs in bloques:
        out.append(madre)
        out.extend(rtzs)
    return out


def validar_colocacion_completa(
    piezas_origen: list,
    hojas: list,
    piezas_pendientes: list | None = None,
) -> Tuple[bool, str]:
    """True si todas las piezas del pack quedaron colocadas en las hojas."""
    esperado = Counter(
        str(p.get("nombre") or "").strip()
        for p in (piezas_origen or [])
        if str(p.get("nombre") or "").strip()
    )
    colocado = Counter()
    for hoja in hojas or []:
        for p in piezas_reales_en_hoja(hoja):
            nom = str(p.get("nombre") or "").strip()
            if nom:
                colocado[nom] += 1

    if esperado == colocado:
        return True, ""

    faltan = sum(max(0, esperado[n] - colocado.get(n, 0)) for n in esperado)
    sobran = sum(max(0, colocado[n] - esperado.get(n, 0)) for n in colocado)
    pend = list(piezas_pendientes or [])
    det = ""
    if pend:
        det = " Piezas sin colocar: " + ", ".join(str(x) for x in pend[:10])
        if len(pend) > 10:
            det += f" (+{len(pend) - 10} más)"
    return (
        False,
        f"Inventario incompleto: faltan {faltan} colocación(es), sobran {sobran}.{det}",
    )


def sanitizar_hojas_grupo(piezas_origen, hojas, clave: str = "", kerf_global: float = DEFAULT_KERF_IN):
    hojas = reconciliar_hojas_grupo(piezas_origen, hojas)
    hojas = deduplicar_hojas_grupo(hojas)
    normalizar_kerf_hojas(hojas, clave=clave, kerf_global=kerf_global)
    return hojas


def deduplicar_resultados_nesting(resultados, kerf_global: float = DEFAULT_KERF_IN) -> None:
    if not isinstance(resultados, dict):
        return
    for clave, grupo in resultados.items():
        if not isinstance(grupo, dict) or "error" in grupo:
            continue
        hojas = grupo.get("hojas")
        if not isinstance(hojas, list):
            continue
        pool = grupo.get("piezas_pool")
        if isinstance(pool, list) and pool and grupo.get("piezas_pool_engine"):
            grupo["hojas"] = sanitizar_hojas_grupo(
                pool, hojas, clave=str(clave), kerf_global=kerf_global
            )
            ok_inv, msg_inv = validar_colocacion_completa(
                pool,
                grupo["hojas"],
                piezas_pendientes=grupo.get("piezas_pendientes"),
            )
            if not ok_inv:
                grupo["advertencia"] = msg_inv
                grupo.pop("error", None)
            else:
                grupo.pop("advertencia", None)
        else:
            hojas_largos = [
                h for h in hojas if isinstance(h, dict) and h.get("modo_largos_cu")
            ]
            hojas_otras = [
                h for h in hojas if isinstance(h, dict) and not h.get("modo_largos_cu")
            ]
            if hojas_largos and hojas_otras:
                # Mixto placa+largos: dedup solo en 2D; barras 1D se conservan todas.
                grupo["hojas"] = deduplicar_hojas_grupo(hojas_otras) + hojas_largos
            else:
                grupo["hojas"] = deduplicar_hojas_grupo(hojas)
            normalizar_kerf_hojas(grupo["hojas"], clave=str(clave), kerf_global=kerf_global)
        asegurar_identidad_hojas(grupo["hojas"], clave=str(clave))
