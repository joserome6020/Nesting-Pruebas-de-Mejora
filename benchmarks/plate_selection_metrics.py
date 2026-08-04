"""Contrato de evidencia para selección de placas (acomodo + costo).

Objetivo dual (ninguno puede sacrificarse a ciegas):
  - mejor acomodo: piezas colocadas, # hojas, eficiencia, integridad
  - menor impacto de costo: Σ precio_placa (sin retazos)

Gates fail-closed: cualquier regresión dura ⇒ FAIL.
Mejora fehaciente: además de pasar hard gates, debe ganar costo y/o tiempo SIM
sin empeorar acomodo.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "arga_plate_selection_evidence_v1"

# Tolerancias duras (regresión no aceptada).
COST_EPS_RATIO = 0.001  # 0.1% float noise
EFI_REGRESSION_PP = 0.5  # puntos porcentuales
# Umbrales mínimos para declarar mejora (activación).
MIN_COST_IMPROVE_RATIO = 0.01  # ≥1% más barato
MIN_SIM_TIME_IMPROVE_RATIO = 0.20  # ≥20% más rápido en SIM
MIN_SIM_TIME_IMPROVE_ABS_MS = 500.0  # y al menos 0.5s absolutos (anti-ruido)


def empty_metrics(*, case_id: str, engine_id: str, label: str = "") -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "case_id": str(case_id),
        "engine_id": str(engine_id),
        "label": str(label or ""),
        "elapsed_ms_total": 0.0,
        "sim_elapsed_ms_total": 0.0,
        "sim_candidates_started": 0,
        "sim_candidates_finished": 0,
        "sim_candidates_skipped": 0,
        "expected_pieces": 0,
        "placed_pieces": 0,
        "sheet_count": 0,
        "remnant_sheet_count": 0,
        "cost_total": 0.0,
        "cost_empresa": 0.0,
        "cost_proveedor": 0.0,
        "mean_efi_direct": 0.0,
        "mean_efi_real": 0.0,
        "integrity_ok": False,
        "integrity_failures": [],
        "selected_plates": [],
        "winner_plate_ids": [],
        "oracle": {},
        "oracle_ok": None,
        "error": "",
        "pass_ok": False,
    }


def extract_from_nest_result(
    result: dict[str, Any],
    *,
    case_id: str,
    engine_id: str,
    expected_pieces: int,
    elapsed_ms_total: float,
    probe_summary: dict[str, Any] | None = None,
    label: str = "",
    oracle: dict[str, Any] | None = None,
    integrity_failures: list | None = None,
) -> dict[str, Any]:
    row = empty_metrics(case_id=case_id, engine_id=engine_id, label=label)
    row["elapsed_ms_total"] = float(elapsed_ms_total or 0.0)
    row["expected_pieces"] = int(expected_pieces or 0)
    row["oracle"] = dict(oracle or {})

    err = str((result or {}).get("error") or "")
    if err:
        row["error"] = err
        row["integrity_ok"] = False
        return row

    failures = list(integrity_failures or [])
    row["integrity_failures"] = failures
    row["integrity_ok"] = len(failures) == 0

    sheets: list[dict[str, Any]] = []
    placed = 0
    cost = 0.0
    cost_emp = 0.0
    cost_prov = 0.0
    efi_d: list[float] = []
    efi_r: list[float] = []
    remnant = 0

    for key, group in (result or {}).items():
        if not isinstance(group, dict):
            continue
        if "costo_total" in group:
            # Prefer group totals when present, but still walk sheets for detail.
            pass
        for sheet in group.get("hojas") or []:
            if not isinstance(sheet, dict):
                continue
            n_pz = len(sheet.get("piezas") or [])
            precio = float(sheet.get("precio_placa") or 0.0)
            is_rtz = bool(sheet.get("es_retazo", False))
            origen = str(sheet.get("origen_placa") or "EMPRESA").upper()
            efi = float(sheet.get("eficiencia") or 0.0)
            sheets.append(
                {
                    "group": str(key),
                    "plate_id": str(sheet.get("placa_id") or ""),
                    "w_mm": float(sheet.get("placa_w") or 0.0),
                    "h_mm": float(sheet.get("placa_h") or 0.0),
                    "price": precio,
                    "source": origen,
                    "is_remnant": is_rtz,
                    "piece_count": n_pz,
                    "efficiency": efi,
                }
            )
            placed += n_pz
            if is_rtz:
                remnant += 1
                continue
            cost += precio
            if "EMPRESA" in origen or origen.strip() == "":
                cost_emp += precio
            else:
                cost_prov += precio
            if efi > 0:
                efi_d.append(efi)

        ed = float(group.get("eficiencia_tanque_directa") or 0.0)
        er = float(group.get("eficiencia_tanque_real") or 0.0)
        if ed > 0:
            efi_d.append(ed)
        if er > 0:
            efi_r.append(er)

        # Prefer explicit group cost when available (matches UI).
        if group.get("costo_total") is not None:
            # Recalculate from non-remnant sheets for this group only when walking;
            # final cost uses sheet sum below for consistency with selected_plates.
            pass

    row["selected_plates"] = sheets
    row["sheet_count"] = sum(1 for s in sheets if not s["is_remnant"])
    row["remnant_sheet_count"] = remnant
    row["placed_pieces"] = int(placed)
    row["cost_total"] = float(cost)
    row["cost_empresa"] = float(cost_emp)
    row["cost_proveedor"] = float(cost_prov)
    row["mean_efi_direct"] = float(sum(efi_d) / len(efi_d)) if efi_d else 0.0
    row["mean_efi_real"] = float(sum(efi_r) / len(efi_r)) if efi_r else 0.0

    if probe_summary:
        row["sim_elapsed_ms_total"] = float(
            probe_summary.get("sim_elapsed_ms_total") or 0.0
        )
        row["sim_candidates_started"] = int(
            probe_summary.get("sim_candidates_started") or 0
        )
        row["sim_candidates_finished"] = int(
            probe_summary.get("sim_candidates_finished") or 0
        )
        row["sim_candidates_skipped"] = int(
            probe_summary.get("sim_candidates_skipped") or 0
        )
        row["winner_plate_ids"] = list(probe_summary.get("winner_plate_ids") or [])
        row["probe"] = probe_summary

    # Oracle: formatos ganadores esperados (si el caso lo declara).
    row["oracle_ok"] = _eval_oracle(row, row["oracle"])

    row["pass_ok"] = bool(
        row["integrity_ok"]
        and not row["error"]
        and int(row["placed_pieces"]) >= int(row["expected_pieces"])
        and (row["oracle_ok"] is not False)
    )
    return row


def _eval_oracle(row: dict[str, Any], oracle: dict[str, Any]) -> bool | None:
    if not oracle:
        return None
    expected_ids = {
        str(x).strip().upper()
        for x in (oracle.get("winner_plate_ids") or [])
        if str(x).strip()
    }
    expected_formats = {
        str(x).strip()
        for x in (oracle.get("winner_formats_in") or [])
        if str(x).strip()
    }
    max_cost = oracle.get("max_cost_total")
    max_sheets = oracle.get("max_sheets")

    ok = True
    if expected_ids:
        got = {str(x).strip().upper() for x in (row.get("winner_plate_ids") or [])}
        # También aceptar placa_id de hojas finales.
        got |= {
            str(s.get("plate_id") or "").strip().upper()
            for s in (row.get("selected_plates") or [])
            if not s.get("is_remnant")
        }
        if not expected_ids.issubset(got) and not (got & expected_ids):
            ok = False
    if expected_formats:
        got_fmts = set()
        for s in row.get("selected_plates") or []:
            if s.get("is_remnant"):
                continue
            w_in = round(float(s.get("w_mm") or 0.0) / 25.4, 3)
            h_in = round(float(s.get("h_mm") or 0.0) / 25.4, 3)
            a, b = sorted((w_in, h_in))
            got_fmts.add(f"{a:.3f}x{b:.3f}")
        if not (got_fmts & expected_formats):
            ok = False
    if max_cost is not None and float(row.get("cost_total") or 0.0) > float(max_cost) + 1e-6:
        ok = False
    if max_sheets is not None and int(row.get("sheet_count") or 0) > int(max_sheets):
        ok = False
    return ok


def compare_gate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    require_improvement: bool = True,
) -> dict[str, Any]:
    """Gate dual: hard regression fail-closed + mejora fehaciente opcional."""
    hard: list[str] = []
    soft_wins: list[str] = []

    if candidate.get("error"):
        hard.append(f"candidate_error:{candidate.get('error')}")
    if not candidate.get("integrity_ok", False):
        hard.append("integrity_failed")
    if int(candidate.get("placed_pieces") or 0) < int(baseline.get("placed_pieces") or 0):
        hard.append("placed_regression")
    if int(candidate.get("expected_pieces") or 0) and int(
        candidate.get("placed_pieces") or 0
    ) < int(candidate.get("expected_pieces") or 0):
        hard.append("incomplete_place")
    if int(candidate.get("sheet_count") or 0) > int(baseline.get("sheet_count") or 0):
        hard.append("more_sheets")

    base_efi = float(baseline.get("mean_efi_direct") or 0.0)
    cand_efi = float(candidate.get("mean_efi_direct") or 0.0)
    if base_efi > 0 and (cand_efi + EFI_REGRESSION_PP) < base_efi:
        hard.append(
            f"efi_regression:{cand_efi:.2f}<{base_efi:.2f}-{EFI_REGRESSION_PP}"
        )

    base_cost = float(baseline.get("cost_total") or 0.0)
    cand_cost = float(candidate.get("cost_total") or 0.0)
    if base_cost > 0 and cand_cost > base_cost * (1.0 + COST_EPS_RATIO):
        hard.append(f"cost_regression:{cand_cost:.2f}>{base_cost:.2f}")

    if candidate.get("oracle_ok") is False:
        hard.append("oracle_miss")

    # Wins
    if base_cost > 0 and cand_cost <= base_cost * (1.0 - MIN_COST_IMPROVE_RATIO):
        soft_wins.append(
            f"cost_improved:{(1.0 - cand_cost / base_cost) * 100.0:.2f}%"
        )
    base_sim = float(baseline.get("sim_elapsed_ms_total") or 0.0)
    cand_sim = float(candidate.get("sim_elapsed_ms_total") or 0.0)
    if (
        base_sim > 0
        and cand_sim <= base_sim * (1.0 - MIN_SIM_TIME_IMPROVE_RATIO)
        and (base_sim - cand_sim) >= MIN_SIM_TIME_IMPROVE_ABS_MS
        and cand_cost <= base_cost * (1.0 + COST_EPS_RATIO)
        and int(candidate.get("sheet_count") or 0)
        <= int(baseline.get("sheet_count") or 0)
    ):
        soft_wins.append(
            f"sim_faster:{(1.0 - cand_sim / base_sim) * 100.0:.1f}%"
        )
    if int(candidate.get("sheet_count") or 0) < int(baseline.get("sheet_count") or 0):
        if cand_cost <= base_cost * (1.0 + COST_EPS_RATIO):
            soft_wins.append("fewer_sheets")
    if cand_efi > base_efi + 0.5:
        soft_wins.append(f"efi_up:+{cand_efi - base_efi:.2f}pp")

    hard_ok = not hard
    improved = bool(soft_wins)
    activate_ok = hard_ok and (improved if require_improvement else True)

    return {
        "schema": "arga_plate_selection_gate_v1",
        "hard_ok": hard_ok,
        "improved": improved,
        "activate_ok": activate_ok,
        "hard_failures": hard,
        "improvements": soft_wins,
        "deltas": {
            "cost_total": cand_cost - base_cost,
            "cost_ratio": (cand_cost / base_cost) if base_cost > 0 else None,
            "sheet_count": int(candidate.get("sheet_count") or 0)
            - int(baseline.get("sheet_count") or 0),
            "placed_pieces": int(candidate.get("placed_pieces") or 0)
            - int(baseline.get("placed_pieces") or 0),
            "mean_efi_direct_pp": cand_efi - base_efi,
            "sim_elapsed_ms_total": cand_sim - base_sim,
            "elapsed_ms_total": float(candidate.get("elapsed_ms_total") or 0.0)
            - float(baseline.get("elapsed_ms_total") or 0.0),
        },
        "policy": {
            "dual_objective": ["mejor_acomodo", "menor_impacto_costo"],
            "cost_eps_ratio": COST_EPS_RATIO,
            "efi_regression_pp": EFI_REGRESSION_PP,
            "min_cost_improve_ratio": MIN_COST_IMPROVE_RATIO,
            "min_sim_time_improve_ratio": MIN_SIM_TIME_IMPROVE_RATIO,
            "min_sim_time_improve_abs_ms": MIN_SIM_TIME_IMPROVE_ABS_MS,
            "require_improvement": require_improvement,
        },
    }
