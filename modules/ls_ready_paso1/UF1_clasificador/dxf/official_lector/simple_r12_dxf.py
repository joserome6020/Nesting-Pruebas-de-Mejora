# -*- coding: utf-8 -*-
"""Lector mínimo ASCII DXF R11/R12 usado como respaldo cuando ezdxf no está disponible.

Implementa únicamente la interfaz que consume lector_dxf.py para entidades comunes:
LINE, ARC, CIRCLE y POLYLINE/VERTEX. No pretende sustituir ezdxf para DXF modernos.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Dict, Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class Vec2:
    x: float
    y: float
    z: float = 0.0

    def __getitem__(self, index: int) -> float:
        return (self.x, self.y, self.z)[index]


class _Entity:
    def __init__(self, etype: str, **attrs):
        self._etype = etype
        self.dxf = SimpleNamespace(**attrs)

    def dxftype(self) -> str:
        return self._etype


class _Polyline(_Entity):
    def __init__(self, layer: str, vertices: Sequence[Vec2], closed: bool):
        super().__init__("POLYLINE", layer=layer)
        self.vertices = [_Entity("VERTEX", location=v, layer=layer) for v in vertices]
        self.is_closed = bool(closed)

    def virtual_entities(self):
        points = [v.dxf.location for v in self.vertices]
        if len(points) < 2:
            return []
        out = []
        limit = len(points) if self.is_closed else len(points) - 1
        for idx in range(limit):
            a = points[idx]
            b = points[(idx + 1) % len(points)]
            out.append(_Entity("LINE", layer=self.dxf.layer, start=a, end=b))
        return out


class _Document:
    def __init__(self, entities: List[_Entity]):
        self._entities = entities

    def modelspace(self) -> List[_Entity]:
        return list(self._entities)


def _pairs(path: str) -> List[Tuple[int, str]]:
    with open(path, "r", encoding="latin-1", errors="ignore") as handle:
        lines = handle.read().splitlines()
    if len(lines) % 2:
        lines = lines[:-1]
    out: List[Tuple[int, str]] = []
    for idx in range(0, len(lines), 2):
        try:
            code = int(lines[idx].strip())
        except ValueError:
            continue
        out.append((code, lines[idx + 1].strip()))
    return out


def _values(data: Iterable[Tuple[int, str]]) -> Dict[int, List[str]]:
    out: Dict[int, List[str]] = {}
    for code, value in data:
        out.setdefault(code, []).append(value)
    return out


def _first(vals: Dict[int, List[str]], code: int, default: str = "0") -> str:
    values = vals.get(code)
    return values[0] if values else default


def _float(vals: Dict[int, List[str]], code: int, default: float = 0.0) -> float:
    try:
        return float(_first(vals, code, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _vec(vals: Dict[int, List[str]], x_code: int, y_code: int, z_code: int = 30) -> Vec2:
    return Vec2(_float(vals, x_code), _float(vals, y_code), _float(vals, z_code))


def readfile(path: str) -> _Document:
    pairs = _pairs(path)
    entities: List[_Entity] = []
    section = None
    idx = 0
    while idx < len(pairs):
        code, value = pairs[idx]
        if code == 0 and value == "SECTION" and idx + 1 < len(pairs) and pairs[idx + 1][0] == 2:
            section = pairs[idx + 1][1]
            idx += 2
            continue
        if code == 0 and value == "ENDSEC":
            section = None
            idx += 1
            continue
        if section != "ENTITIES" or code != 0:
            idx += 1
            continue

        etype = value
        idx += 1
        data: List[Tuple[int, str]] = []
        while idx < len(pairs) and pairs[idx][0] != 0:
            data.append(pairs[idx])
            idx += 1
        vals = _values(data)
        layer = _first(vals, 8, "0")

        if etype == "LINE":
            entities.append(_Entity("LINE", layer=layer, start=_vec(vals, 10, 20), end=_vec(vals, 11, 21, 31)))
        elif etype == "ARC":
            entities.append(_Entity(
                "ARC", layer=layer, center=_vec(vals, 10, 20), radius=_float(vals, 40),
                start_angle=_float(vals, 50), end_angle=_float(vals, 51),
            ))
        elif etype == "CIRCLE":
            entities.append(_Entity("CIRCLE", layer=layer, center=_vec(vals, 10, 20), radius=_float(vals, 40)))
        elif etype == "POLYLINE":
            flags = int(_float(vals, 70, 0.0))
            closed = bool(flags & 1)
            vertices: List[Vec2] = []
            while idx < len(pairs):
                next_code, next_type = pairs[idx]
                if next_code != 0:
                    idx += 1
                    continue
                if next_type == "SEQEND":
                    idx += 1
                    while idx < len(pairs) and pairs[idx][0] != 0:
                        idx += 1
                    break
                if next_type != "VERTEX":
                    break
                idx += 1
                vertex_data: List[Tuple[int, str]] = []
                while idx < len(pairs) and pairs[idx][0] != 0:
                    vertex_data.append(pairs[idx])
                    idx += 1
                vertices.append(_vec(_values(vertex_data), 10, 20))
            entities.append(_Polyline(layer, vertices, closed))
        # VERTEX/SEQEND are consumed as part of POLYLINE.

    return _Document(entities)
