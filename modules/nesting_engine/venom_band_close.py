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
    try:
        test_clear = test_poly.buffer(kerf_half, resolution=2, join_style=2)
    except Exception:
        test_clear = test_poly
    tc = test_clear.bounds
    for op in others:
        if op is None or getattr(op, "is_empty", True):
            continue
        ob = op.bounds
        if tc[2] <= ob[0] or tc[0] >= ob[2] or tc[3] <= ob[1] or tc[1] >= ob[3]:
            continue
        try:
            op_clear = op.buffer(kerf_half, resolution=2, join_style=2)
        except Exception:
            op_clear = op
        if test_clear.intersects(op_clear) and not test_clear.touches(op_clear):
            return True
    return False


def _group_can_move(
    members: list[dict],
    all_entries: list[dict],
    dx: float,
    dy: float,
    kerf_half: float,
    placa_w: float,
    placa_h: float,
) -> bool:
    member_idx = {m["idx"] for m in members}
    others = [e["poly"] for e in all_entries if e["idx"] not in member_idx]
    for m in members:
        test = affinity.translate(m["poly"], dx, dy)
        tb = test.bounds
        if tb[0] < kerf_half - 1e-6 or tb[1] < kerf_half - 1e-6:
            return False
        if placa_w > 0 and tb[2] > placa_w - kerf_half + 1e-6:
            return False
        if placa_h > 0 and tb[3] > placa_h - kerf_half + 1e-6:
            return False
        # Entre miembros del mismo grupo: se mueven juntos → no chequear.
        if _collides(test, others, kerf_half):
            return False
    return True


def _apply_group_move(members: list[dict], all_entries: list[dict], dx: float, dy: float) -> None:
    by_idx = {e["idx"]: e for e in all_entries}
    for m in members:
        _translate_piece(m["p"], dx, dy)
        new_poly = affinity.translate(m["poly"], dx, dy)
        m["poly"] = new_poly
        if m["idx"] in by_idx:
            by_idx[m["idx"]]["poly"] = new_poly


def _slide_bands_axis(
    entries: list[dict],
    *,
    axis: str,
    direction: float,
    kerf_half: float,
    placa_w: float,
    placa_h: float,
    merge_gap: float,
    step: float,
    max_mm: float,
) -> tuple[int, float]:
    """
    direction < 0 → hacia lo-/miny; > 0 → hacia hi/max.
    Desliza bandas desde el extremo opuesto a la gravedad hacia el ancla.
    """
    bands = _cluster_bands(entries, axis=axis, merge_gap=merge_gap)
    if len(bands) < 2:
        return 0, 0.0

    # Anclar la banda más cercana al borde de gravedad; mover el resto hacia ella.
    if direction < 0:
        # hacia lo: procesar de hi → lo (bandas altas primero)
        order = sorted(range(len(bands)), key=lambda i: bands[i]["lo"], reverse=True)
        dx_unit, dy_unit = (-step, 0.0) if axis == "x" else (0.0, -step)
    else:
        order = sorted(range(len(bands)), key=lambda i: bands[i]["hi"])
        dx_unit, dy_unit = (step, 0.0) if axis == "x" else (0.0, step)

    closed = 0
    mm_tot = 0.0
    for bi in order:
        # La banda ancla (extremo gravedad) no se mueve como grupo prioritario:
        # si direction<0, ancla = banda con menor lo.
        if direction < 0 and bi == min(range(len(bands)), key=lambda i: bands[i]["lo"]):
            continue
        if direction > 0 and bi == max(range(len(bands)), key=lambda i: bands[i]["hi"]):
            continue

        members = bands[bi]["members"]
        moved = 0.0
        while moved + step <= max_mm + 1e-9:
            if not _group_can_move(
                members, entries, dx_unit, dy_unit, kerf_half, placa_w, placa_h
            ):
                break
            _apply_group_move(members, entries, dx_unit, dy_unit)
            moved += step
        if moved > 0.5:
            closed += 1
            mm_tot += moved
            # refrescar bounds de banda
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
) -> dict[str, Any]:
    """
    Cierra aire entre hileras (Y) y columnas (X).
    Llamar DESPUÉS de gravedad individual y ANTES de sheet pockets.
    force=True: ignora flags (compact-lite / tests).
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
    entries = _piece_entries(hoja)
    if len(entries) < 3:
        return stats

    placa_w = float(hoja.get("placa_w", 0) or 0)
    placa_h = float(hoja.get("placa_h", 0) or 0)
    kerf_in = float(hoja.get("kerf_usado", 0.0) or 0.0)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)

    # merge_gap: piezas de la misma fila suelen solaparse en Y o quedar cerca.
    areas = [float(e["poly"].area) for e in entries]
    areas.sort()
    med_area = areas[len(areas) // 2] if areas else 1.0
    typical_h = max(25.0, (med_area ** 0.5) * 0.35)
    merge_gap = max(kerf_half * 4.0, typical_h * 0.45)
    step = 2.0
    max_mm = min(900.0, max(placa_w, placa_h) * 0.45)

    # Preferir compactar hacia origen (esquina kerf) — misma convención que Venom.
    cy, my = _slide_bands_axis(
        entries,
        axis="y",
        direction=-1.0,
        kerf_half=kerf_half,
        placa_w=placa_w,
        placa_h=placa_h,
        merge_gap=merge_gap,
        step=step,
        max_mm=max_mm,
    )
    # Refrescar polys tras Y (entries ya mutados).
    cx, mx = _slide_bands_axis(
        entries,
        axis="x",
        direction=-1.0,
        kerf_half=kerf_half,
        placa_w=placa_w,
        placa_h=placa_h,
        merge_gap=merge_gap,
        step=step,
        max_mm=max_mm,
    )

    stats["bands_y"] = int(cy)
    stats["bands_x"] = int(cx)
    stats["mm_y"] = float(my)
    stats["mm_x"] = float(mx)
    stats["t"] = time.perf_counter() - t0

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
