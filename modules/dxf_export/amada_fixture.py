"""
Fixtura Amada real para export AMADA/FIXTURA.

Hay dos fixturas físicas (canal 5\"). Siempre se elige la más justa
(canal más corto) que todavía acepte el largo de la pieza: así los topes
presionan el cobre hacia la esquina BL (Punto 0).

Método de alineación:
  1) En el DXF de fixtura, tomar pared vertical (tope izq) y riel horizontal
     (base) del canal — caras que empaman con el cobre.
  2) Prolongar ambas; su intersección = PUNTO 0.
  3) Colocar la esquina BL de la pieza exactamente en ese punto 0.
  4) La fixtura se copia ÍNTEGRA (no se borra geometría interna).

En el export nest: Punto 0 → (0,0) mm (= BL de la pieza).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.math import Matrix44

AMADA_FIXTURE_LAYER = "FIXTURE"
_IN_TO_MM = 25.4

# Tolerancia de largo al decidir si cabe (misma familia que PARTS).
AMADA_FIT_TOL_IN = 0.02


@dataclass(frozen=True)
class AmadaSeat:
    """
    Referencias del canal + Punto 0 = intersección de sus prolongaciones.

    left/bottom = coordenadas del Punto 0 (pulgadas nativas).
    """

    left: float
    bottom: float
    right: float
    top: float
    rail_x0: float
    rail_x1: float
    wall_y0: float = 0.0
    wall_y1: float = 0.0
    rail_x_start: float = 0.0
    rail_x_end: float = 0.0

    @property
    def origin(self) -> tuple[float, float]:
        return (self.left, self.bottom)

    @property
    def width(self) -> float:
        """Largo útil del canal (entre topes), en pulgadas."""
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom


@dataclass(frozen=True)
class AmadaFixtureSpec:
    id: str
    label: str
    filename: str
    fallback_largo_in: float


# Orden del catálogo: no decide prioridad; la elección es por canal más corto.
AMADA_FIXTURE_CATALOG: tuple[AmadaFixtureSpec, ...] = (
    AmadaFixtureSpec(
        id="fixtura_2",
        label="Fixtura 2",
        filename="Fixtura 2.DXF",
        fallback_largo_in=28.95,
    ),
    AmadaFixtureSpec(
        id="original",
        label="Fixtura original",
        filename="FICSTURA MEJORADA CORTE BUENO .25IN.DXF",
        fallback_largo_in=35.33,
    ),
)


# Constantes públicas (se sincronizan al dibujar).
SEAT_LEFT_IN = -17.6650
SEAT_BOTTOM_IN = -5.0394
SEAT_RIGHT_IN = 17.6650
SEAT_TOP_IN = 0.0394


def _fixture_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "FIXTURA AMADA"


def _path_for_spec(spec: AmadaFixtureSpec) -> Path | None:
    path = _fixture_dir() / spec.filename
    return path if path.is_file() else None


def resolve_amada_fixture_dxf(fixture_id: str | None = None) -> str | None:
    """Ruta del DXF de fixtura. Sin id → env o fixtura original (compat)."""
    env = (os.environ.get("AMADA_FIXTURE_DXF") or "").strip()
    if env and os.path.isfile(env) and not fixture_id:
        return env

    if fixture_id:
        for spec in AMADA_FIXTURE_CATALOG:
            if spec.id == fixture_id:
                p = _path_for_spec(spec)
                return str(p) if p else None

    # Compat: env genérico o original.
    if env and os.path.isfile(env):
        return env
    for spec in AMADA_FIXTURE_CATALOG:
        if spec.id == "original":
            p = _path_for_spec(spec)
            if p:
                return str(p)
    for spec in AMADA_FIXTURE_CATALOG:
        p = _path_for_spec(spec)
        if p:
            return str(p)
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

    Si el DXF tiene varios canales ~5\" (Fixtura 2: exterior 35\" + interior
    28.95\"), se prefiere el canal MÁS CORTO: es el que aprieta el cobre.
    """
    path = path or resolve_amada_fixture_dxf()
    fallback = AmadaSeat(
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
    if not path:
        return fallback

    horiz = []
    vert = []
    for x1, y1, x2, y2 in _iter_lines(ezdxf.readfile(path)):
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if dy < 1e-3 and dx > 20.0:
            horiz.append((0.5 * (y1 + y2), min(x1, x2), max(x1, x2)))
        if dx < 1e-3 and dy > 1.0:
            vert.append((0.5 * (x1 + x2), min(y1, y2), max(y1, y2)))

    channels = []
    for i, a in enumerate(horiz):
        for b in horiz[i + 1 :]:
            gap = abs(b[0] - a[0])
            if gap < 4.90 or gap > 5.20:
                continue
            y0, y1 = min(a[0], b[0]), max(a[0], b[0])
            x0 = max(a[1], b[1])
            x1 = min(a[2], b[2])
            # Fixtura 2 tiene canal útil ~28.95"; no exigir 30"+.
            if x1 - x0 < 25.0:
                continue
            channels.append((y0, y1, x0, x1, gap))

    def _best_wall(y0, y1, x_rail0, side: str):
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
        canal = float(right_x) - float(wall_x)
        # Preferir gap ~5.079" y, a igualdad, el canal MÁS CORTO (más justo).
        score = (abs(gap - 5.079), canal)
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
    return fallback


@lru_cache(maxsize=8)
def _load_fixture_bundle(path: str):
    if not path or not os.path.isfile(path):
        return None
    doc = ezdxf.readfile(path)
    seat = detect_amada_seat(path)
    ents = [e.copy() for e in doc.modelspace()]
    return {"path": path, "seat": seat, "ents": ents, "n_src": len(list(doc.modelspace()))}


def _seat_for_path(path: str | None, fallback_largo: float) -> AmadaSeat:
    if path:
        try:
            return detect_amada_seat(path)
        except Exception:
            pass
    half = float(fallback_largo) / 2.0
    return AmadaSeat(
        left=-half,
        bottom=SEAT_BOTTOM_IN,
        right=half,
        top=SEAT_TOP_IN,
        rail_x0=-half + 0.125,
        rail_x1=half - 0.125,
    )


def listar_fixturas_amada() -> list[dict[str, Any]]:
    """Catálogo con canal real (o fallback) de cada DXF presente."""
    out: list[dict[str, Any]] = []
    for spec in AMADA_FIXTURE_CATALOG:
        path = _path_for_spec(spec)
        seat = _seat_for_path(str(path) if path else None, spec.fallback_largo_in)
        out.append(
            {
                "id": spec.id,
                "label": spec.label,
                "filename": spec.filename,
                "path": str(path) if path else None,
                "disponible": path is not None,
                "canal_in": float(seat.width),
                "alto_in": float(seat.height),
                "seat": seat,
            }
        )
    return out


def amada_fixtura_largo_max_in() -> float:
    """Mayor canal disponible entre todas las fixturas (límite absoluto ESP.)."""
    largos = [float(f["canal_in"]) for f in listar_fixturas_amada() if f.get("disponible")]
    if largos:
        return max(largos)
    return max(float(s.fallback_largo_in) for s in AMADA_FIXTURE_CATALOG)


def elegir_fixtura_amada(
    largo_pieza_in: float,
    *,
    tol_in: float = AMADA_FIT_TOL_IN,
) -> dict[str, Any] | None:
    """
    Elige la fixtura más justa (canal más corto) que aún acepte el largo.

    Así los topes ejercen presión hacia el Punto 0 / esquina BL.
    """
    largo = float(largo_pieza_in or 0)
    if largo <= 0:
        return None
    cands: list[tuple[float, dict[str, Any]]] = []
    for fx in listar_fixturas_amada():
        if not fx.get("disponible"):
            continue
        canal = float(fx["canal_in"])
        if largo <= canal + float(tol_in):
            cands.append((canal, fx))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]["id"]))
    chosen = dict(cands[0][1])
    chosen["holgura_in"] = float(chosen["canal_in"]) - largo
    return chosen


def get_amada_seat(fixture_id: str | None = None) -> AmadaSeat:
    if fixture_id:
        path = resolve_amada_fixture_dxf(fixture_id)
        if path:
            bundle = _load_fixture_bundle(path)
            if bundle:
                return bundle["seat"]
        for spec in AMADA_FIXTURE_CATALOG:
            if spec.id == fixture_id:
                return _seat_for_path(None, spec.fallback_largo_in)

    # Compat: la de mayor canal (misma API histórica ≈ original).
    disponibles = [f for f in listar_fixturas_amada() if f.get("disponible")]
    if disponibles:
        best = max(disponibles, key=lambda f: float(f["canal_in"]))
        return best["seat"]
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


def draw_amada_fixture_real(
    msp,
    bar_l_mm: float,
    bar_w_mm: float,
    *,
    fixture_id: str | None = None,
    largo_pieza_in: float | None = None,
) -> bool:
    """
    Copia ÍNTEGRA de la fixtura, trasladada para que Punto 0 → (0,0) mm.

    Si se pasa ``largo_pieza_in`` (o se deduce de ``bar_l_mm``) y no hay
    ``fixture_id``, se elige automáticamente la más justa.
    """
    if bar_l_mm <= 1.0 or bar_w_mm <= 1.0:
        return False

    fid = fixture_id
    if not fid:
        largo_in = (
            float(largo_pieza_in)
            if largo_pieza_in is not None and float(largo_pieza_in) > 0
            else float(bar_l_mm) / _IN_TO_MM
        )
        eleccion = elegir_fixtura_amada(largo_in)
        if eleccion:
            fid = str(eleccion["id"])

    path = resolve_amada_fixture_dxf(fid)
    if not path:
        return False
    bundle = _load_fixture_bundle(path)
    if not bundle:
        return False

    seat: AmadaSeat = bundle["seat"]
    _sync_public_seat_constants(seat)
    ents = bundle["ents"]

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


def draw_amada_fixture_provisional(
    msp,
    bar_l_mm: float,
    bar_w_mm: float,
    *,
    fixture_id: str | None = None,
    largo_pieza_in: float | None = None,
) -> None:
    """Compat: siempre intenta fixtura REAL; fallback simulado si falta DXF."""
    if draw_amada_fixture_real(
        msp,
        bar_l_mm,
        bar_w_mm,
        fixture_id=fixture_id,
        largo_pieza_in=largo_pieza_in,
    ):
        return
    path = resolve_amada_fixture_dxf(fixture_id)
    print(
        "[AMADA-FIXTURE] DXF real no disponible — usando contorno simulado "
        f"(path={path!r}). Coloque los DXF en 'FIXTURA AMADA/' "
        "o defina AMADA_FIXTURE_DXF.",
        flush=True,
    )
    _draw_amada_fixture_fallback(msp, bar_l_mm, bar_w_mm)


def validate_piece_in_seat(
    bar_l_mm: float,
    bar_w_mm: float,
    *,
    fixture_id: str | None = None,
) -> dict:
    """La pieza debe caber entre topes (sin solapar metal)."""
    if fixture_id:
        seat = get_amada_seat(fixture_id)
    else:
        eleccion = elegir_fixtura_amada(float(bar_l_mm) / _IN_TO_MM)
        seat = eleccion["seat"] if eleccion else get_amada_seat()
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
        "fixture_id": fixture_id
        or (elegir_fixtura_amada(float(bar_l_mm) / _IN_TO_MM) or {}).get("id"),
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
