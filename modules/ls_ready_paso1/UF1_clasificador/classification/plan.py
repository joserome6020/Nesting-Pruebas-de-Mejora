# -*- coding: utf-8 -*-
"""Stitch: concatena local_plan de piezas en plan global MARK y CUT."""


def stitch_global_plan(pieces_by_id, piece_order_mark, piece_order_cut):
    mark_steps = []
    cut_steps = []
    mark_step_no = 0
    cut_step_no = 0

    for piece_id in piece_order_mark:
        piece = pieces_by_id[piece_id]
        local_mark = (piece.get("local_plan") or {}).get("mark") or []
        for step in local_mark:
            mark_step_no += 1
            global_step = dict(step)
            global_step["step"] = mark_step_no
            global_step["phase"] = "mark"
            global_step["piece_id"] = piece_id
            mark_steps.append(global_step)

    for piece_id in piece_order_cut:
        piece = pieces_by_id[piece_id]
        local_cut = (piece.get("local_plan") or {}).get("cut") or []
        for step in local_cut:
            cut_step_no += 1
            global_step = dict(step)
            global_step["step"] = cut_step_no
            global_step["phase"] = "cut"
            global_step["piece_id"] = piece_id
            cut_steps.append(global_step)

    return {
        "mark": mark_steps,
        "cut": cut_steps,
        "all": mark_steps + cut_steps,
    }


# Alias retrocompatible
build_execution_plan = stitch_global_plan
