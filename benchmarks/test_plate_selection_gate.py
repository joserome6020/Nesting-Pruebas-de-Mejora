"""Tests unitarios del gate dual (sin nestear)."""
from __future__ import annotations

from benchmarks.plate_selection_metrics import compare_gate, empty_metrics


def _base(**kwargs):
    row = empty_metrics(case_id="t", engine_id="arga_force", label="base")
    row.update(
        {
            "integrity_ok": True,
            "placed_pieces": 20,
            "expected_pieces": 20,
            "sheet_count": 3,
            "cost_total": 1000.0,
            "mean_efi_direct": 55.0,
            "sim_elapsed_ms_total": 10000.0,
            "elapsed_ms_total": 12000.0,
            "pass_ok": True,
        }
    )
    row.update(kwargs)
    return row


def test_rejects_cost_regression():
    gate = compare_gate(_base(), _base(cost_total=1100.0))
    assert gate["hard_ok"] is False
    assert any("cost_regression" in x for x in gate["hard_failures"])
    assert gate["activate_ok"] is False


def test_rejects_more_sheets():
    gate = compare_gate(_base(), _base(sheet_count=4, cost_total=900.0))
    assert gate["hard_ok"] is False
    assert "more_sheets" in gate["hard_failures"]


def test_rejects_efi_regression():
    gate = compare_gate(_base(), _base(mean_efi_direct=50.0, cost_total=900.0))
    assert gate["hard_ok"] is False
    assert any("efi_regression" in x for x in gate["hard_failures"])


def test_accepts_cost_improvement():
    gate = compare_gate(_base(), _base(cost_total=950.0, mean_efi_direct=55.5))
    assert gate["hard_ok"] is True
    assert gate["improved"] is True
    assert gate["activate_ok"] is True
    assert any("cost_improved" in x for x in gate["improvements"])


def test_accepts_sim_speedup_without_cost_regression():
    cand = _base(sim_elapsed_ms_total=7000.0, cost_total=1000.0)
    gate = compare_gate(_base(), cand)
    assert gate["hard_ok"] is True
    assert gate["improved"] is True
    assert any("sim_faster" in x for x in gate["improvements"])


def test_ignores_tiny_sim_noise():
    # 25% relativo pero solo 200ms absolutos → no cuenta como mejora.
    gate = compare_gate(
        _base(sim_elapsed_ms_total=800.0),
        _base(sim_elapsed_ms_total=600.0, cost_total=1000.0),
    )
    assert gate["hard_ok"] is True
    assert not any("sim_faster" in x for x in gate["improvements"])
    assert gate["activate_ok"] is False


def test_equal_without_require_improvement():
    gate = compare_gate(_base(), _base(), require_improvement=False)
    assert gate["hard_ok"] is True
    assert gate["activate_ok"] is True
    assert gate["improved"] is False


def test_equal_with_require_improvement_blocks_activation():
    gate = compare_gate(_base(), _base(), require_improvement=True)
    assert gate["hard_ok"] is True
    assert gate["activate_ok"] is False
