"""Probe opcional de selección de placas (SIM multi-formato).

Activo solo dentro de ``plate_selection_probe()`` o con
``ARGA_PLATE_SEL_PROBE_FILE`` (JSONL, seguro entre procesos del pool).
No altera el ganador; solo registra timings/metadatos.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class PlateSimEvent:
    event: str
    clave: str = ""
    placa_id: str = ""
    w_mm: float = 0.0
    h_mm: float = 0.0
    precio: float = 0.0
    piezas_colocadas: int = 0
    area_usada: float = 0.0
    restos: int = 0
    score: float | None = None
    elapsed_ms: float | None = None
    detail: str = ""


@dataclass
class PlateSelectionProbe:
    events: list[PlateSimEvent] = field(default_factory=list)
    label: str = ""

    def record(self, event: str, **kwargs: Any) -> None:
        self.events.append(PlateSimEvent(event=str(event), **kwargs))

    def summary(self) -> dict[str, Any]:
        return summarize_events(self.events, label=self.label)


def summarize_events(
    events: list[PlateSimEvent] | list[dict[str, Any]],
    *,
    label: str = "",
) -> dict[str, Any]:
    normalized: list[PlateSimEvent] = []
    for item in events or []:
        if isinstance(item, PlateSimEvent):
            normalized.append(item)
        elif isinstance(item, dict):
            fields = {
                k: item.get(k)
                for k in PlateSimEvent.__dataclass_fields__
                if k == "event" or k in item
            }
            if "event" not in fields or fields["event"] is None:
                continue
            normalized.append(PlateSimEvent(**fields))
    starts = [e for e in normalized if e.event == "sim_start"]
    results = [e for e in normalized if e.event == "sim_result"]
    skips = [e for e in normalized if e.event == "sim_skip"]
    winners = [e for e in normalized if e.event == "sim_winner"]
    timed = [float(e.elapsed_ms) for e in results if e.elapsed_ms is not None]
    return {
        "label": label,
        "sim_candidates_started": len(starts),
        "sim_candidates_finished": len(results),
        "sim_candidates_skipped": len(skips),
        "sim_winners": len(winners),
        "sim_elapsed_ms_total": float(sum(timed)) if timed else 0.0,
        "sim_elapsed_ms_max": float(max(timed)) if timed else 0.0,
        "sim_elapsed_ms_mean": (
            float(sum(timed) / len(timed)) if timed else 0.0
        ),
        "winner_plate_ids": [e.placa_id for e in winners],
        "events": [e.__dict__ for e in normalized],
    }


def load_probe_file(path: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return summarize_events(events, label="probe_file")


_PROBE: ContextVar[PlateSelectionProbe | None] = ContextVar(
    "arga_plate_selection_probe", default=None
)


def get_probe() -> PlateSelectionProbe | None:
    return _PROBE.get()


def record_probe(event: str, **kwargs: Any) -> None:
    payload = {"event": str(event), **kwargs}
    probe = _PROBE.get()
    if probe is not None:
        probe.record(event, **kwargs)
    path = str(os.environ.get("ARGA_PLATE_SEL_PROBE_FILE") or "").strip()
    if path:
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass


@contextmanager
def plate_selection_probe(label: str = "") -> Iterator[PlateSelectionProbe]:
    probe = PlateSelectionProbe(label=str(label or ""))
    token = _PROBE.set(probe)
    try:
        yield probe
    finally:
        _PROBE.reset(token)
