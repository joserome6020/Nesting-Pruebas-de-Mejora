"""
Venom band-close — cierra bandas/filas con aire entre clusters.

Objetivo: densificar la placa (aprovechar área), no solo compactar bbox al borde.
Detecta hileras (Y) y columnas (X) y las desliza como grupo rígido hasta kerf.
"""
from __future__ import annotations

import os
import time
from typing import Any

from shapely import affinity


def band_close_enabled() -> bool:
    """ON si ARGA_NEST_COMPACT=1 (default) o ARGA_NEST_BAND_CLOSE=1."""
    compact = (os.environ.get("ARGA_NEST_COMPACT") or "1").strip().lower()
    if compact not in ("0", "false", "off", "no"):
        return True
    v = (os.environ.get("ARGA_NEST_BAND_CLOSE") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _translate_piece(p: dict, sx: float, sy: float) -> None:
    from .venom_ai import _translate_piece_data

    _translate_piece_data(p, sx, sy)


def _piece_entries(hoja: dict) -> list[dict]:
    from .venom_hole_fill import _is_virtual, _piece_poly

    out = []
    for idx, p in enumerate(hoja.get("piezas") or []):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        out.append({"idx": idx, "p": p, "poly": poly})
    return out


def _cluster_bands(entries: list[dict], *, axis: str, merge_gap: float) -> list[dict]:
    """
    axis='y' → hileras horizontales; axis='x' → columnas verticales.
    Une piezas cuyo intervalo en el eje se solapa o queda a ≤ merge_gap.
    """
    if not entries:
        return []

    def _lohi(e: dict) -> tuple[float, float]:
        b = e["poly"].bounds
        if axis == "y":
            return float(b[1]), float(b[3])
        return float(b[0]), float(b[2])

    ordered = sorted(entries, key=lambda e: _lohi(e)[0])
    bands: list[dict] = []
    for e in ordered:
        lo, hi = _lohi(e)
        placed = False
        for band in bands:
            if lo <= band["hi"] + merge_gap and hi >= band["lo"] - merge_gap:
                band["members"].append(e)
                band["lo"] = min(band["lo"], lo)
                band["hi"] = max(band["hi"], hi)
                placed = True
                break
        if not placed:
            bands.append({"members": [e], "lo": lo, "hi": hi})

    # Fusionar bandas que quedaron solapadas tras expansiones.
    bands.sort(key=lambda b: b["lo"])
    merged: list[dict] = []
    for band in bands:
        if merged and band["lo"] <= merged[-1]["hi"] + merge_gap:
            m = merged[-1]
            m["members"].extend(band["members"])
            m["lo"] = min(m["lo"], band["lo"])
            m["hi"] = max(m["hi"], band["hi"])
        else:
            merged.append(band)
    return merged


def _collides(
    test_poly,
    others: list,
    kerf_half: float,
) -> bool:
    """Pieza↔pieza: distancia exacta ≥ kerf completo (2×kerf_half). Sin buffer flojo."""
    kerf_full = max(float(kerf_half) * 2.0, 1.0)
    try:
        from .nest_poka_yoke import distancia_menor_que_kerf_mm, metal_solapa
    except Exception:
        distancia_menor_que_kerf_mm = None  # type: ignore
        metal_solapa = None  # type: ignore

    tb = test_poly.bounds
    pad = kerf_full + 1.0
    for op in others:
        if op is None or getattr(op, "is_empty", True):
            continue
        ob = op.bounds
        if (
            tb[2] + pad < ob[0]
            or tb[0] - pad > ob[2]
            or tb[3] + pad < ob[1]
            or tb[1] - pad > ob[3]
        ):
            continue
        if metal_solapa is not None and metal_solapa(test_poly, op):
            return True
        if distancia_menor_que_kerf_mm is not None:
            if distancia_menor_que_kerf_mm(test_poly, op, kerf_full):
                return True
            continue
        # Fallback legacy
        try:
            if float(test_poly.distance(op)) + 1e-6 < kerf_full:
                return True
        except Exception:
            return True
    return False


def _expand_members(members: list[dict], all_by_idx: dict[int, dict], rigid_children: dict[int, list[int]]) -> list[dict]:
    """Incluye hijos rígidos (guests en orificio anclados al host)."""
    seen = {m["idx"] for m in members}
    out = list(members)
    for m in members:
        for cid in rigid_children.get(int(m["idx"]), []) or []:
            if cid in seen:
                continue
            child = all_by_idx.get(int(cid))
            if child is None:
                continue
            out.append(child)
            seen.add(int(cid))
    return out


def _group_can_move(
    members: list[dict],
    all_entries: list[dict],
    dx: float,
    dy: float,
    kerf_half: float,
    placa_w: float,
    placa_h: float,
    *,
    rigid_children: dict[int, list[int]] | None = None,
    plate_inset_mm: float | None = None,
) -> bool:
    all_by_idx = {e["idx"]: e for e in all_entries}
    expanded = _expand_members(members, all_by_idx, rigid_children or {})
    member_idx = {m["idx"] for m in expanded}
    others = [e["poly"] for e in all_entries if e["idx"] not in member_idx]
    # Placa→pieza: margen de tabla (0.250"), NUNCA kerf/2.
    if plate_inset_mm is not None:
        inset = float(plate_inset_mm)
    else:
        try:
            from .cut_gaps_table import PLATE_TO_PIECE_DEFAULT_IN

            inset = float(PLATE_TO_PIECE_DEFAULT_IN) * 25.4
        except Exception:
            inset = 0.250 * 25.4
    inset = max(inset, 0.0)
    for m in expanded:
        test = affinity.translate(m["poly"], dx, dy)
        tb = test.bounds
        if tb[0] < inset - 1e-6 or tb[1] < inset - 1e-6:
            return False
        if placa_w > 0 and tb[2] > placa_w - inset + 1e-6:
            return False
        if placa_h > 0 and tb[3] > placa_h - inset + 1e-6:
            return False
        if _collides(test, others, kerf_half):
            return False
    return True


def _apply_group_move(
    members: list[dict],
    all_entries: list[dict],
    dx: float,
    dy: float,
    *,
    rigid_children: dict[int, list[int]] | None = None,
) -> None:
    by_idx = {e["idx"]: e for e in all_entries}
    expanded = _expand_members(members, by_idx, rigid_children or {})
    for m in expanded:
        _translate_piece(m["p"], dx, dy)
        new_poly = affinity.translate(m["poly"], dx, dy)
        m["poly"] = new_poly
        if m["idx"] in by_idx:
            by_idx[m["idx"]]["poly"] = new_poly


def _slide_bands_axis_full(
    band_entries: list[dict],
    full_entries: list[dict],
    *,
    axis: str,
    direction: float,
    kerf_half: float,
    placa_w: float,
    placa_h: float,
    merge_gap: float,
    step: float,
    max_mm: float,
    rigid_children: dict[int, list[int]] | None = None,
    plate_inset_mm: float | None = None,
) -> tuple[int, float]:
    """Como _slide_bands_axis pero colisiona contra full_entries (incl. frozen)."""
    bands = _cluster_bands(band_entries, axis=axis, merge_gap=merge_gap)
    if len(bands) < 2:
        return 0, 0.0

    if direction < 0:
        order = sorted(range(len(bands)), key=lambda i: bands[i]["lo"], reverse=True)
        dx_unit, dy_unit = (-step, 0.0) if axis == "x" else (0.0, -step)
    else:
        order = sorted(range(len(bands)), key=lambda i: bands[i]["hi"])
        dx_unit, dy_unit = (step, 0.0) if axis == "x" else (0.0, step)

    closed = 0
    mm_tot = 0.0
    for bi in order:
        if direction < 0 and bi == min(range(len(bands)), key=lambda i: bands[i]["lo"]):
            continue
        if direction > 0 and bi == max(range(len(bands)), key=lambda i: bands[i]["hi"]):
            continue

        members = bands[bi]["members"]
        moved = 0.0
        while moved + step <= max_mm + 1e-9:
            if not _group_can_move(
                members,
                full_entries,
                dx_unit,
                dy_unit,
                kerf_half,
                placa_w,
                placa_h,
                rigid_children=rigid_children,
                plate_inset_mm=plate_inset_mm,
            ):
                break
            _apply_group_move(
                members, full_entries, dx_unit, dy_unit, rigid_children=rigid_children
            )
            moved += step
        if moved > 0.5:
            closed += 1
            mm_tot += moved
            if axis == "y":
                bands[bi]["lo"] = min(m["poly"].bounds[1] for m in members)
                bands[bi]["hi"] = max(m["poly"].bounds[3] for m in members)
            else:
                bands[bi]["lo"] = min(m["poly"].bounds[0] for m in members)
                bands[bi]["hi"] = max(m["poly"].bounds[2] for m in members)

    return closed, mm_tot


def close_inter_band_gaps(
    hoja: dict,
    engine_id: str = "default",
    *,
    force: bool = False,
    skip_idxs: set[int] | None = None,
    rigid_children: dict[int, list[int]] | None = None,
    all_entries: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Cierra aire entre hileras (Y) y columnas (X).
    Llamar DESPUÉS de gravedad individual y ANTES de sheet pockets.
    force=True: ignora flags (compact-lite / tests).

    skip_idxs: no forman banda propia (p.ej. guests ya dentro de orificio).
    rigid_children: host_idx → [guest_idx...] se mueven pegados al host.
    all_entries: mapa completo de piezas (incl. skip) para colisiones/hijos.
    """
    enabled = bool(force) or band_close_enabled()
    stats: dict[str, Any] = {
        "bands_y": 0,
        "bands_x": 0,
        "mm_y": 0.0,
        "mm_x": 0.0,
        "enabled": enabled,
        "forced": bool(force),
    }
    if not enabled:
        return stats

    t0 = time.perf_counter()
    full_entries = all_entries if all_entries is not None else _piece_entries(hoja)
    skip = {int(i) for i in (skip_idxs or set())}
    band_entries = [e for e in full_entries if e["idx"] not in skip]
    if len(band_entries) < 2:
        return stats

    placa_w = float(hoja.get("placa_w", 0) or 0)
    placa_h = float(hoja.get("placa_h", 0) or 0)
    try:
        from .cut_gaps_table import gaps_efectivos_para_hoja

        kerf_in, margin_in = gaps_efectivos_para_hoja(
            hoja, clave=str(hoja.get("clave") or "")
        )
    except Exception:
        kerf_in = float(hoja.get("kerf_usado", 0.0) or 0.0)
        margin_in = float(hoja.get("margin_usado", 0.25) or 0.25)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)
    plate_inset = max(float(margin_in) * 25.4, 0.0)

    areas = [float(e["poly"].area) for e in band_entries]
    areas.sort()
    med_area = areas[len(areas) // 2] if areas else 1.0
    typical_h = max(25.0, (med_area ** 0.5) * 0.35)
    # Paso fino: 2 mm dejaba gaps ~0.17" bajo kerf 0.250" al compactar.
    step = min(0.5, max(kerf_half * 0.15, 0.25))
    merge_gap = max(kerf_half * 4.0, typical_h * 0.45)
    max_mm = min(900.0, max(placa_w, placa_h) * 0.45)
    kids = {int(k): [int(x) for x in (v or [])] for k, v in (rigid_children or {}).items()}

    cy, my = _slide_bands_axis_full(
        band_entries,
        full_entries,
        axis="y",
        direction=-1.0,
        kerf_half=kerf_half,
        placa_w=placa_w,
        placa_h=placa_h,
        merge_gap=merge_gap,
        step=step,
        max_mm=max_mm,
        rigid_children=kids,
        plate_inset_mm=plate_inset,
    )
    cx, mx = _slide_bands_axis_full(
        band_entries,
        full_entries,
        axis="x",
        direction=-1.0,
        kerf_half=kerf_half,
        placa_w=placa_w,
        placa_h=placa_h,
        merge_gap=merge_gap,
        step=step,
        max_mm=max_mm,
        rigid_children=kids,
        plate_inset_mm=plate_inset,
    )

    stats["bands_y"] = int(cy)
    stats["bands_x"] = int(cx)
    stats["mm_y"] = float(my)
    stats["mm_x"] = float(mx)
    stats["t"] = time.perf_counter() - t0
    stats["skip"] = len(skip)

    hoja["venom_band_close"] = stats
    if cy or cx:
        log_msg = (
            f"[VENOM-BAND] Motor: {engine_id} | "
            f"bands_y={cy} mm_y={my:.1f} | bands_x={cx} mm_x={mx:.1f} | "
            f"t={stats['t']:.2f}s"
        )
        print(log_msg)
        try:
            from .venom_hole_fill import _append_ai_log

            _append_ai_log(log_msg)
        except Exception:
            pass
    return stats
