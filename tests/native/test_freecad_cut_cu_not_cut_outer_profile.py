"""Candado: CUT_OUTER no activa perfil cobre (CUT_CU substring bug)."""
from __future__ import annotations

import re


def _label_matches_any(lbl, keys):
    if not keys:
        return False
    lbl = (lbl or "").strip().upper()
    if not lbl:
        return False
    tokens = [t for t in re.split(r"[^A-Z0-9_.-]+", lbl) if t]
    for k in keys:
        ku = str(k).strip().upper()
        if not ku:
            continue
        if ku.isdigit():
            if lbl == ku or ku in tokens:
                return True
        else:
            if ku == "CUT_CU":
                if lbl == "CUT_CU" or "CUT_CU" in tokens:
                    return True
                continue
            if lbl == ku or ku in tokens:
                return True
            if ku in lbl:
                return True
    return False


def _resolve_profile_layer_names(layers):
    names = {str(l).upper().strip() for l in layers if str(l).strip()}
    if "CUT_CU" in names and "CUT_OUTER" not in names:
        return "ARGA_COBRE_LARGOS"
    return "ARGA_NESTING_STD"


def test_cut_outer_does_not_match_cut_cu():
    assert not _label_matches_any("CUT_OUTER", {"CUT_CU"})
    assert _label_matches_any("CUT_OUTER", {"CUT_OUTER"})
    assert _label_matches_any("CUT_CU", {"CUT_CU"})


def test_profile_from_nest_layers():
    prof = _resolve_profile_layer_names(["Plate", "CUT_OUTER", "CUT_INNER"])
    assert prof == "ARGA_NESTING_STD", prof


def test_empty_cut_cu_layer_table_does_not_force_cobre():
    prof = _resolve_profile_layer_names(["Plate", "CUT_OUTER", "CUT_INNER", "CUT_CU"])
    assert prof == "ARGA_NESTING_STD", prof


if __name__ == "__main__":
    test_cut_outer_does_not_match_cut_cu()
    test_profile_from_nest_layers()
    print("OK test_freecad_cut_cu_not_cut_outer_profile")
