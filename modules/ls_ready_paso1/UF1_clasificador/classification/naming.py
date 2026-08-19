# -*- coding: utf-8 -*-
"""Nombres de pieza A..Z, AA.. y paths derivados."""


def index_to_piece_id(index):
    """0 -> A, 25 -> Z, 26 -> AA (estilo Excel)."""
    n = int(index) + 1
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def grab_text_name(piece_id, text_index=1):
    return "Grab{0}{1}".format(piece_id, int(text_index))


def grab_figure_name(piece_id, figure_step=1):
    if int(figure_step) <= 1:
        return "Grab{0}".format(piece_id)
    return "Grab{0}_{1}".format(piece_id, int(figure_step))


def inner_name(piece_id):
    return "Inner{0}".format(piece_id)


def hole_group_name(piece_id, group_index):
    if int(group_index) <= 1:
        return "Barr{0}".format(piece_id)
    return "Barr{0}{1}".format(piece_id, int(group_index))


def outer_name(piece_id):
    return str(piece_id)
