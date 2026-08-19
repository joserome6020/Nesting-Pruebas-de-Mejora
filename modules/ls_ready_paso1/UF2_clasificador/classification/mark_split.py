# -*- coding: utf-8 -*-
"""Separacion de trazos mark en texto vs figuras."""
from .geometry import bbox_height, bbox_width


def median_safe(values, default):
    vals = [float(v) for v in (values or []) if v is not None]
    if not vals:
        return float(default)
    vals.sort()
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def split_text_and_figures(strokes, cfg, logs=None):
    if not strokes:
        return [], []

    widths = [max(0.1, bbox_width(s["bbox_dxf"])) for s in strokes]
    heights = [max(0.1, bbox_height(s["bbox_dxf"])) for s in strokes]
    lengths = [max(0.1, float(s.get("length_mm") or 0.0)) for s in strokes]

    med_w = median_safe(widths, 10.0)
    med_h = median_safe(heights, 10.0)
    med_len = median_safe(lengths, 20.0)

    text_max_dim = max(cfg.TEXT_MAX_DIM_ABS, max(med_w, med_h) * cfg.TEXT_MAX_DIM_MULT)
    text_max_len = max(cfg.TEXT_MAX_LEN_ABS, med_len * cfg.TEXT_MAX_LEN_MULT)

    text_strokes = []
    figure_strokes = []
    for s in strokes:
        md = max(bbox_width(s["bbox_dxf"]), bbox_height(s["bbox_dxf"]))
        ln = float(s.get("length_mm") or 0.0)
        if md <= text_max_dim and ln <= text_max_len:
            text_strokes.append(s)
        else:
            figure_strokes.append(s)

    if logs is not None:
        logs.append(
            "mark_split text={0} figures={1} (max_dim={2:.2f} max_len={3:.2f})".format(
                len(text_strokes),
                len(figure_strokes),
                text_max_dim,
                text_max_len,
            )
        )
    return text_strokes, figure_strokes
