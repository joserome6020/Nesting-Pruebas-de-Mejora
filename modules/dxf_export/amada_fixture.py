"""
Fixtura Amada real para export AMADA/FIXTURA.

Método de alineación (macheote del usuario):
  1) En el DXF de fixtura, tomar la arista vertical (tope izq) y la
     horizontal (riel inf) del canal — caras que empaman con el cobre.
  2) Prolongar ambas con líneas rectas; su intersección = PUNTO 0.
  3) Colocar la esquina BL de la pieza exactamente en ese punto 0.
  4) La fixtura se copia ÍNTEGRA (no se borra geometría interna).

En el export nest: Punto 0 → (0,0) mm (= BL de la pieza).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import ezdxf
from ezdxf.math import Matrix44

AMADA_FIXTURE_LAYER = "FIXTURE"
_IN_TO_MM = 25.4


@dataclass(frozen=True)
class AmadaSeat:
    """
    Referencias del canal + Punto 0 = intersección de sus prolongaciones.

    left/bottom = coordenadas del Punto 0 (pulgadas nativas).
    """

    left: float  # X de la pared vertical de referencia
    bottom: float  # Y del riel horizontal de referencia
    right: float
    top: float
    rail_x0: float
    rail_x1: float
    # Segmentos de referencia (para auditoría / construcción)
    wall_y0: float = 0.0
    wall_y1: float = 0.0
    rail_x_start: float = 0.0
    rail_x_end: float = 0.0

    @property
    def origin(self) -> tuple[float, float]:
        """Punto 0: intersección pared × riel."""
        return (self.left, self.bottom)

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom


# Constantes públicas (se sincronizan al detectar).
SEAT_LEFT_IN = -17.6650
SEAT_BOTTOM_IN = -5.0394
SEAT_RIGHT_IN = 17.6650
SEAT_TOP_IN = 0.0394


def _default_fixture_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "FIXTURA AMADA" / "FICSTURA MEJORADA CORTE BUENO .25IN.DXF"


def resolve_amada_fixture_dxf() -> str | None:
    env = (os.environ.get("AMADA_FIXTURE_DXF") or "").strip()
    if env and os.path.isfile(env):
        return env
    path = _default_fixture_path()
    if path.is_file():
        return str(path)
    return None


def _iter_lines(doc):
    for e in doc.modelspace():
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        yield float(a.x), float(a.y), float(b.x), float(b.y)


def detect_amada_seat(path: str | None = None) -> AmadaSeat:
    """
    Identifica pared izq + riel inf del canal Amada y calcula Punto 0.

    Punto 0 = intersección de:
      - prolongación vertical de la pared (x = wall_x)
      - prolongación horizontal del riel (y = rail_y)
    """
    path = path or resolve_amada_fixture_dxf()
    if not path:
        return AmadaSeat(
            SEAT_LEFT_IN,
            SEAT_BOTTOM_IN,
            SEAT_RIGHT_IN,
            SEAT_TOP_IN,
            -17.54,
            17.54,
            wall_y0=-4.9144,
            wall_y1=-0.0856,
            rail_x_start=-17.54,
            rail_x_end=17.54,
        )

    horiz = []
    vert = []
    for x1, y1, x2, y2 in _iter_lines(ezdxf.readfile(path)):
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if dy < 1e-3 and dx > 20.0:
            horiz.append(
                (
                    0.5 * (y1 + y2),
                    min(x1, x2),
                    max(x1, x2),
                )
            )
        if dx < 1e-3 and dy > 1.0:
            vert.append(
                (
                    0.5 * (x1 + x2),
                    min(y1, y2),
                    max(y1, y2),
                )
            )

    # Candidatos de canal: par de rieles horizontales ~5"
    channels = []
    for i, a in enumerate(horiz):
        for b in horiz[i + 1 :]:
            gap = abs(b[0] - a[0])
            if gap < 4.90 or gap > 5.20:
                continue
            y0, y1 = min(a[0], b[0]), max(a[0], b[0])
            x0 = max(a[1], b[1])
            x1 = min(a[2], b[2])
            if x1 - x0 < 30.0:
                continue
            channels.append((y0, y1, x0, x1, gap))

    def _best_wall(y0, y1, x_rail0, side: str):
        """Tope vertical que solapa el canal y queda junto al extremo del riel."""
        best = None
        for vx, ymin, ymax in vert:
            ov = min(ymax, y1) - max(ymin, y0)
            if ov < 2.0:
                continue
            if side == "left":
                if vx > x_rail0 + 0.05:
                    continue
                if abs(vx - x_rail0) > 1.5:
                    continue
            else:
                if vx < x_rail0 - 0.05:
                    continue
                if abs(vx - x_rail0) > 1.5:
                    continue
            key = (abs(vx - x_rail0), -ov)
            if best is None or key < best[0]:
                best = (key, vx, ymin, ymax)
        return None if best is None else best[1:]

    scored = []
    for y0, y1, x0, x1, gap in channels:
        left = _best_wall(y0, y1, x0, "left")
        right = _best_wall(y0, y1, x1, "right")
        if left is None or right is None:
            continue
        wall_x, wall_y0, wall_y1 = left
        right_x, _, _ = right
        # Preferir canal ~5.079" (con topes), no el bolsillo exacto 5" del marco.
        score = (abs(gap - 5.079), -(x1 - x0))
        scored.append(
            (
                score,
                AmadaSeat(
                    left=float(wall_x),
                    bottom=float(y0),
                    right=float(right_x),
                    top=float(y1),
                    rail_x0=float(x0),
                    rail_x1=float(x1),
                    wall_y0=float(wall_y0),
                    wall_y1=float(wall_y1),
                    rail_x_start=float(x0),
                    rail_x_end=float(x1),
                ),
            )
        )

    if scored:
        return sorted(scored, key=lambda t: t[0])[0][1]

    return AmadaSeat(
        SEAT_LEFT_IN,
        SEAT_BOTTOM_IN,
        SEAT_RIGHT_IN,
        SEAT_TOP_IN,
        -17.54,
        17.54,
        wall_y0=-4.9144,
        wall_y1=-0.0856,
        rail_x_start=-17.54,
        rail_x_end=17.54,
    )


@lru_cache(maxsize=1)
def _load_fixture_bundle():
    path = resolve_amada_fixture_dxf()
    if not path:
        return None
    doc = ezdxf.readfile(path)
    seat = detect_amada_seat(path)
    ents = [e.copy() for e in doc.modelspace()]
    return {"path": path, "seat": seat, "ents": ents, "n_src": len(list(doc.modelspace()))}


def get_amada_seat() -> AmadaSeat:
    bundle = _load_fixture_bundle()
    if bundle:
        return bundle["seat"]
    return AmadaSeat(
        SEAT_LEFT_IN,
        SEAT_BOTTOM_IN,
        SEAT_RIGHT_IN,
        SEAT_TOP_IN,
        -17.54,
        17.54,
    )


def _sync_public_seat_constants(seat: AmadaSeat) -> None:
    global SEAT_LEFT_IN, SEAT_BOTTOM_IN, SEAT_RIGHT_IN, SEAT_TOP_IN
    SEAT_LEFT_IN = seat.left
    SEAT_BOTTOM_IN = seat.bottom
    SEAT_RIGHT_IN = seat.right
    SEAT_TOP_IN = seat.top


def draw_amada_fixture_real(msp, bar_l_mm: float, bar_w_mm: float) -> bool:
    """
    Copia ÍNTEGRA de la fixtura, trasladada para que Punto 0 → (0,0) mm.

    Punto 0 = intersección de prolongación pared izq × riel inf.
    La pieza del nest debe tener su BL en (0,0) mm.

    Si la pieza es más larga/alta que el canal, igual se dibuja la fixtura
    real (no se cae a contorno simulado). Solo se rechaza si no hay DXF.
    """
    if bar_l_mm <= 1.0 or bar_w_mm <= 1.0:
        return False
    bundle = _load_fixture_bundle()
    if not bundle:
        return False

    seat: AmadaSeat = bundle["seat"]
    _sync_public_seat_constants(seat)
    ents = bundle["ents"]

    # Punto 0 (left, bottom) → origen del export.
    ox, oy = seat.origin
    m = Matrix44.chain(
        Matrix44.scale(_IN_TO_MM),
        Matrix44.translate(-ox * _IN_TO_MM, -oy * _IN_TO_MM, 0.0),
    )
    if "FIXTURE" not in msp.doc.layers:
        msp.doc.layers.new("FIXTURE", dxfattribs={"color": 8})

    n_ok = 0
    for src in ents:
        try:
            ne = src.copy()
            if not ne.transform(m):
                continue
            ne.dxf.layer = AMADA_FIXTURE_LAYER
            msp.add_entity(ne)
            n_ok += 1
        except Exception:
            continue

    return n_ok > 0


def draw_amada_fixture_provisional(msp, bar_l_mm: float, bar_w_mm: float) -> None:
    """
    Compat: nombre histórico. Siempre intenta fixtura REAL.
    El fallback simulado solo si falta el DXF fuente.
    """
    if draw_amada_fixture_real(msp, bar_l_mm, bar_w_mm):
        return
    path = resolve_amada_fixture_dxf()
    print(
        "[AMADA-FIXTURE] DXF real no disponible — usando contorno simulado "
        f"(path={path!r}). Coloque "
        "'FIXTURA AMADA/FICSTURA MEJORADA CORTE BUENO .25IN.DXF' "
        "o defina AMADA_FIXTURE_DXF.",
        flush=True,
    )
    _draw_amada_fixture_fallback(msp, bar_l_mm, bar_w_mm)


def validate_piece_in_seat(bar_l_mm: float, bar_w_mm: float) -> dict:
    """La pieza debe caber entre topes (sin solapar metal)."""
    seat = get_amada_seat()
    seat_w = seat.width * _IN_TO_MM
    seat_h = seat.height * _IN_TO_MM
    return {
        "seat": seat,
        "point0_in": seat.origin,
        "ok": (
            bar_l_mm <= seat_w + 1e-6
            and bar_w_mm <= seat_h + 1e-6
            and bar_l_mm > 1.0
            and bar_w_mm > 1.0
        ),
        "clearance_x_mm": seat_w - bar_l_mm,
        "clearance_y_mm": seat_h - bar_w_mm,
        "overlap_x_mm": max(0.0, bar_l_mm - seat_w),
        "overlap_y_mm": max(0.0, bar_w_mm - seat_h),
    }


def _draw_amada_fixture_fallback(msp, bar_l_mm: float, bar_w_mm: float) -> None:
    L = float(bar_l_mm)
    W = float(bar_w_mm)
    mx = 12.7
    sx = min(40.0, max(8.0, L * 0.06))
    so = 10.0
    outer = [
        (-mx, 0.0),
        (-mx + sx * 0.4, -so),
        (L + mx - sx * 0.4, -so),
        (L + mx, 0.0),
        (L + mx, W),
        (L + mx - sx * 0.4, W + so),
        (-mx + sx * 0.4, W + so),
        (-mx, W),
    ]
    msp.add_lwpolyline(
        [(float(x), float(y)) for x, y in outer],
        dxfattribs={"layer": AMADA_FIXTURE_LAYER, "color": 8},
        close=True,
    )
    msp.add_lwpolyline(
        [(0.0, 0.0), (L, 0.0), (L, W), (0.0, W)],
        dxfattribs={"layer": AMADA_FIXTURE_LAYER, "color": 8},
        close=True,
    )
