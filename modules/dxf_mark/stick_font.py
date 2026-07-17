"""Tipografía monolinea (palitos) para marcaje láser. Coordenadas normalizadas [0..1]."""

from __future__ import annotations

import math

# Cada glifo = lista de trazos abiertos (cada trazo = lista de (x, y)).
STICK_GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    "0": [
        # Rectángulo cerrado sin diagonal (más eficiente en láser/STEP).
        [(0.15, 0.08), (0.85, 0.08), (0.85, 0.92), (0.15, 0.92), (0.15, 0.08)],
    ],
    "1": [
        [(0.35, 0.75), (0.50, 0.92), (0.50, 0.08)],
        [(0.28, 0.08), (0.72, 0.08)],
    ],
    "2": [
        [
            (0.15, 0.78),
            (0.25, 0.92),
            (0.75, 0.92),
            (0.85, 0.78),
            (0.85, 0.62),
            (0.15, 0.08),
            (0.85, 0.08),
        ]
    ],
    "3": [
        [
            (0.18, 0.92),
            (0.78, 0.92),
            (0.85, 0.78),
            (0.85, 0.62),
            (0.55, 0.50),
            (0.85, 0.38),
            (0.85, 0.22),
            (0.78, 0.08),
            (0.18, 0.08),
        ]
    ],
    "4": [
        [(0.72, 0.08), (0.72, 0.92)],
        [(0.72, 0.92), (0.18, 0.38), (0.88, 0.38)],
    ],
    "5": [
        [
            (0.82, 0.92),
            (0.20, 0.92),
            (0.20, 0.55),
            (0.72, 0.55),
            (0.85, 0.42),
            (0.85, 0.22),
            (0.72, 0.08),
            (0.20, 0.08),
        ]
    ],
    "6": [
        [
            (0.78, 0.85),
            (0.65, 0.92),
            (0.28, 0.92),
            (0.15, 0.78),
            (0.15, 0.22),
            (0.28, 0.08),
            (0.72, 0.08),
            (0.85, 0.22),
            (0.85, 0.42),
            (0.72, 0.55),
            (0.28, 0.55),
            (0.15, 0.42),
        ]
    ],
    "7": [[(0.15, 0.92), (0.85, 0.92), (0.35, 0.08)]],
    "8": [
        [
            (0.50, 0.50),
            (0.75, 0.62),
            (0.75, 0.82),
            (0.50, 0.92),
            (0.25, 0.82),
            (0.25, 0.62),
            (0.50, 0.50),
            (0.75, 0.38),
            (0.75, 0.18),
            (0.50, 0.08),
            (0.25, 0.18),
            (0.25, 0.38),
            (0.50, 0.50),
        ]
    ],
    "9": [
        [
            (0.22, 0.15),
            (0.35, 0.08),
            (0.72, 0.08),
            (0.85, 0.22),
            (0.85, 0.78),
            (0.72, 0.92),
            (0.28, 0.92),
            (0.15, 0.78),
            (0.15, 0.58),
            (0.28, 0.45),
            (0.72, 0.45),
            (0.85, 0.58),
        ]
    ],
    "A": [
        [(0.08, 0.08), (0.50, 0.92), (0.92, 0.08)],
        [(0.28, 0.38), (0.72, 0.38)],
    ],
    "B": [
        [(0.18, 0.08), (0.18, 0.92)],
        [
            (0.18, 0.92),
            (0.68, 0.92),
            (0.82, 0.80),
            (0.82, 0.62),
            (0.68, 0.50),
            (0.18, 0.50),
        ],
        [
            (0.18, 0.50),
            (0.70, 0.50),
            (0.85, 0.36),
            (0.85, 0.20),
            (0.70, 0.08),
            (0.18, 0.08),
        ],
    ],
    "C": [
        [
            (0.85, 0.78),
            (0.70, 0.92),
            (0.30, 0.92),
            (0.15, 0.75),
            (0.15, 0.25),
            (0.30, 0.08),
            (0.70, 0.08),
            (0.85, 0.22),
        ]
    ],
    "D": [
        [(0.18, 0.08), (0.18, 0.92)],
        [
            (0.18, 0.92),
            (0.62, 0.92),
            (0.85, 0.72),
            (0.85, 0.28),
            (0.62, 0.08),
            (0.18, 0.08),
        ],
    ],
    "E": [
        [(0.22, 0.08), (0.22, 0.92), (0.82, 0.92)],
        [(0.22, 0.50), (0.70, 0.50)],
        [(0.22, 0.08), (0.82, 0.08)],
    ],
    "F": [
        [(0.22, 0.08), (0.22, 0.92), (0.82, 0.92)],
        [(0.22, 0.50), (0.68, 0.50)],
    ],
    "G": [
        [
            (0.85, 0.78),
            (0.70, 0.92),
            (0.30, 0.92),
            (0.15, 0.75),
            (0.15, 0.25),
            (0.30, 0.08),
            (0.70, 0.08),
            (0.85, 0.22),
            (0.85, 0.48),
            (0.55, 0.48),
        ]
    ],
    "H": [
        [(0.18, 0.08), (0.18, 0.92)],
        [(0.82, 0.08), (0.82, 0.92)],
        [(0.18, 0.50), (0.82, 0.50)],
    ],
    "I": [
        [(0.50, 0.08), (0.50, 0.92)],
        [(0.28, 0.92), (0.72, 0.92)],
        [(0.28, 0.08), (0.72, 0.08)],
    ],
    "J": [
        [
            (0.72, 0.92),
            (0.72, 0.28),
            (0.60, 0.08),
            (0.35, 0.08),
            (0.18, 0.22),
        ]
    ],
    "K": [
        [(0.20, 0.08), (0.20, 0.92)],
        [(0.82, 0.92), (0.20, 0.50), (0.82, 0.08)],
    ],
    "L": [[(0.22, 0.92), (0.22, 0.08), (0.82, 0.08)]],
    "M": [
        [
            (0.10, 0.08),
            (0.10, 0.92),
            (0.50, 0.42),
            (0.90, 0.92),
            (0.90, 0.08),
        ]
    ],
    "N": [[(0.18, 0.08), (0.18, 0.92), (0.82, 0.08), (0.82, 0.92)]],
    "O": [
        [
            (0.30, 0.08),
            (0.70, 0.08),
            (0.85, 0.25),
            (0.85, 0.75),
            (0.70, 0.92),
            (0.30, 0.92),
            (0.15, 0.75),
            (0.15, 0.25),
            (0.30, 0.08),
        ]
    ],
    "P": [
        [(0.20, 0.08), (0.20, 0.92)],
        [
            (0.20, 0.92),
            (0.68, 0.92),
            (0.82, 0.78),
            (0.82, 0.58),
            (0.68, 0.45),
            (0.20, 0.45),
        ],
    ],
    "Q": [
        [
            (0.30, 0.08),
            (0.70, 0.08),
            (0.85, 0.25),
            (0.85, 0.75),
            (0.70, 0.92),
            (0.30, 0.92),
            (0.15, 0.75),
            (0.15, 0.25),
            (0.30, 0.08),
        ],
        [(0.55, 0.35), (0.88, 0.05)],
    ],
    "R": [
        [(0.20, 0.08), (0.20, 0.92)],
        [
            (0.20, 0.92),
            (0.68, 0.92),
            (0.82, 0.78),
            (0.82, 0.58),
            (0.68, 0.45),
            (0.20, 0.45),
        ],
        [(0.50, 0.45), (0.85, 0.08)],
    ],
    "S": [
        [
            (0.82, 0.78),
            (0.68, 0.92),
            (0.30, 0.92),
            (0.18, 0.78),
            (0.18, 0.62),
            (0.30, 0.50),
            (0.70, 0.50),
            (0.82, 0.38),
            (0.82, 0.22),
            (0.70, 0.08),
            (0.32, 0.08),
            (0.18, 0.22),
        ]
    ],
    "T": [
        [(0.12, 0.92), (0.88, 0.92)],
        [(0.50, 0.92), (0.50, 0.08)],
    ],
    "U": [
        [
            (0.18, 0.92),
            (0.18, 0.28),
            (0.32, 0.08),
            (0.68, 0.08),
            (0.82, 0.28),
            (0.82, 0.92),
        ]
    ],
    "V": [[(0.08, 0.92), (0.50, 0.08), (0.92, 0.92)]],
    "W": [
        [
            (0.05, 0.92),
            (0.28, 0.08),
            (0.50, 0.55),
            (0.72, 0.08),
            (0.95, 0.92),
        ]
    ],
    "X": [
        [(0.15, 0.92), (0.85, 0.08)],
        [(0.85, 0.92), (0.15, 0.08)],
    ],
    "Y": [
        [(0.12, 0.92), (0.50, 0.50), (0.88, 0.92)],
        [(0.50, 0.50), (0.50, 0.08)],
    ],
    "Z": [[(0.15, 0.92), (0.85, 0.92), (0.15, 0.08), (0.85, 0.08)]],
    "-": [[(0.18, 0.50), (0.82, 0.50)]],
    "_": [[(0.10, 0.08), (0.90, 0.08)]],
    ".": [[(0.42, 0.08), (0.58, 0.08), (0.58, 0.20), (0.42, 0.20), (0.42, 0.08)]],
    "/": [[(0.22, 0.08), (0.78, 0.92)]],
}


def normalize_mark_text(text: str) -> str:
    """Mayúsculas; conserva A-Z 0-9 y -_./ ; el resto se omite."""
    allowed = set(STICK_GLYPHS) | {" "}
    out: list[str] = []
    for ch in str(text or "").upper():
        if ch in allowed:
            out.append(ch)
    return "".join(out).strip()


def build_stick_strokes(
    text: str,
    origin: tuple[float, float],
    height: float,
    *,
    width_factor: float = 0.72,
    spacing_factor: float = 0.18,
) -> list[list[tuple[float, float]]]:
    """
    Genera polilíneas abiertas (palitos) para el texto.
    origin = esquina inferior-izquierda del texto.
    height = altura de mayúsculas en unidades del DXF.
    """
    x0, y0 = origin
    h = float(height)
    if h <= 0:
        return []
    glyph_w = h * float(width_factor)
    gap = h * float(spacing_factor)
    strokes: list[list[tuple[float, float]]] = []
    cursor = x0
    for ch in normalize_mark_text(text):
        if ch == " ":
            cursor += glyph_w * 0.55 + gap
            continue
        glyph = STICK_GLYPHS.get(ch)
        if not glyph:
            cursor += glyph_w + gap
            continue
        for stroke in glyph:
            if len(stroke) < 2:
                continue
            strokes.append([(cursor + gx * glyph_w, y0 + gy * h) for gx, gy in stroke])
        cursor += glyph_w + gap
    return strokes


def text_bbox(strokes: list[list[tuple[float, float]]]) -> tuple[float, float, float, float] | None:
    pts = [p for s in strokes for p in s]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def measure_text_size(
    text: str,
    height: float,
    *,
    width_factor: float = 0.72,
    spacing_factor: float = 0.18,
) -> tuple[float, float]:
    strokes = build_stick_strokes(
        text, (0.0, 0.0), height, width_factor=width_factor, spacing_factor=spacing_factor
    )
    bb = text_bbox(strokes)
    if not bb:
        return 0.0, 0.0
    return bb[2] - bb[0], bb[3] - bb[1]


def translate_strokes(
    strokes: list[list[tuple[float, float]]], dx: float, dy: float
) -> list[list[tuple[float, float]]]:
    return [[(x + dx, y + dy) for x, y in s] for s in strokes]


def rotate_strokes(
    strokes: list[list[tuple[float, float]]],
    angle_deg: float,
    origin: tuple[float, float] = (0.0, 0.0),
) -> list[list[tuple[float, float]]]:
    """Rota trazos alrededor de origin (grados, sentido antihorario)."""
    ang = float(angle_deg or 0.0) % 360.0
    if abs(ang) < 1e-9 or abs(ang - 360.0) < 1e-9:
        return [list(s) for s in strokes]
    rad = math.radians(ang)
    c = math.cos(rad)
    s = math.sin(rad)
    ox, oy = origin
    out: list[list[tuple[float, float]]] = []
    for stroke in strokes:
        out.append(
            [
                (
                    ox + (x - ox) * c - (y - oy) * s,
                    oy + (x - ox) * s + (y - oy) * c,
                )
                for x, y in stroke
            ]
        )
    return out


def measure_text_aabb(
    text: str,
    height: float,
    *,
    angle_deg: float = 0.0,
    width_factor: float = 0.72,
    spacing_factor: float = 0.18,
) -> tuple[float, float]:
    """Ancho×alto del AABB del texto (tras rotación)."""
    strokes = build_stick_strokes(
        text, (0.0, 0.0), height, width_factor=width_factor, spacing_factor=spacing_factor
    )
    if abs(float(angle_deg or 0.0)) >= 1e-9:
        strokes = rotate_strokes(strokes, angle_deg, origin=(0.0, 0.0))
    bb = text_bbox(strokes)
    if not bb:
        return 0.0, 0.0
    return bb[2] - bb[0], bb[3] - bb[1]
