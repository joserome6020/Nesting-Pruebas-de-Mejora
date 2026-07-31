"""Valida colocación y slide bottom-left del motor arga_base (Fase 1)."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shapely.affinity import translate as affinity_translate
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from modules.nesting_engine.algorithm_bridge import empaquetar_una_hoja_arga_base
from modules.nesting_engine.sheet_integrity import validar_colocacion_completa


def _rect_piece(name: str, w: float, h: float, area: float | None = None) -> dict:
  poly = box(0, 0, w, h)
  return {
    "nombre": name,
    "poly": poly,
    "marks": None,
    "area": float(area if area is not None else poly.area),
    "calibre": "0.25",
    "material": "ACERO",
  }


def _rings_to_poly(rings) -> Polygon | None:
  if not rings:
    return None
  exterior = [(float(x), float(y)) for x, y in rings[0]]
  holes = [[(float(x), float(y)) for x, y in ring] for ring in rings[1:]]
  if len(exterior) < 3:
    return None
  try:
    return Polygon(exterior, holes)
  except Exception:
    return None


def _piece_polys(hoja) -> list[tuple[str, Polygon]]:
  out = []
  for p in hoja.get("piezas") or []:
    nom = str(p.get("nombre") or "")
    poly = _rings_to_poly(p.get("poligonos") or [])
    if poly is not None and not poly.is_empty:
      out.append((nom, poly))
  return out


def _validar_geometria(hoja, w_placa: float, h_placa: float, kerf_in: float = 0.3) -> tuple[bool, str]:
  sheet = box(0, 0, w_placa, h_placa)
  kerf_mm = kerf_in * 25.4
  polys = _piece_polys(hoja)
  if not polys:
    return False, "Sin piezas colocadas"

  for nom, poly in polys:
    if not sheet.buffer(0.5).contains(poly):
      return False, f"{nom} fuera de placa"
    if poly.area <= 0:
      return False, f"{nom} área inválida"

  for i in range(len(polys)):
    nom_a, poly_a = polys[i]
    buff_a = poly_a.buffer(kerf_mm * 0.5)
    for j in range(i + 1, len(polys)):
      nom_b, poly_b = polys[j]
      buff_b = poly_b.buffer(kerf_mm * 0.5)
      inter = buff_a.intersection(buff_b)
      if inter.area > 0.5:
        return False, f"Solape kerf entre {nom_a} y {nom_b} ({inter.area:.1f} mm²)"
  return True, ""


def _validar_slide_bottom_left(
  hoja, w_placa: float, h_placa: float, kerf_in: float = 0.3, step: float = 0.49
) -> tuple[bool, str]:
  """Verifica compactación BLF usando geometría con kerf (como el motor C++)."""
  polys = _piece_polys(hoja)
  if not polys:
    return True, ""

  sheet = box(0, 0, w_placa, h_placa)
  kerf_radio = kerf_in * 25.4 / 2.0

  for i, (nom, poly) in enumerate(polys):
    test_poly = poly.buffer(kerf_radio, join_style=2)
    others_buff = [
      p.buffer(kerf_radio, join_style=2)
      for j, (_, p) in enumerate(polys)
      if j != i
    ]
    union_others = unary_union(others_buff) if others_buff else None

    for dx, dy in ((-step, 0.0), (0.0, -step)):
      shifted = affinity_translate(test_poly, dx, dy)
      minx, miny, maxx, maxy = shifted.bounds
      if minx < -0.05 or miny < -0.05 or maxx > w_placa + 0.05 or maxy > h_placa + 0.05:
        continue
      if not sheet.contains(shifted):
        continue
      if union_others is not None and shifted.intersects(union_others):
        continue
      return False, f"{nom} puede deslizar ({dx:+.2f},{dy:+.2f}) mm"
  return True, ""


def _run_case(name: str, piezas, w: float, h: float) -> tuple[bool, str]:
  hoja, restos = empaquetar_una_hoja_arga_base(
    piezas, w, h, kerf_override=0.3, margin_override=0.0
  )
  ok_inv, msg_inv = validar_colocacion_completa(piezas, [hoja], piezas_pendientes=restos)
  if not ok_inv:
    return False, f"{name}: inventario — {msg_inv}"

  ok_geo, msg_geo = _validar_geometria(hoja, w, h)
  if not ok_geo:
    return False, f"{name}: geometría — {msg_geo}"

  ok_slide, msg_slide = _validar_slide_bottom_left(hoja, w, h)
  if not ok_slide:
    return False, f"{name}: movimiento — {msg_slide}"

  placed = len(hoja.get("piezas") or [])
  return True, f"{name}: OK ({placed} colocadas, {len(restos)} restos)"


def main() -> int:
  w, h = 2500.0, 5000.0
  cases = [
    (
      "rectangulos_iguales",
      [_rect_piece("A", 400, 300)] * 4 + [_rect_piece("B", 600, 200)] * 2,
    ),
    (
      "mixto_grande_pequeno",
      [
        _rect_piece("GRANDE", 1200, 800),
        _rect_piece("MED", 500, 400),
        _rect_piece("MED", 500, 400),
        _rect_piece("PEQ", 120, 80),
        _rect_piece("PEQ", 120, 80),
        _rect_piece("PEQ", 120, 80),
      ],
    ),
    (
      "apilado_vertical",
      [_rect_piece("TIRA", 200, 900)] * 3 + [_rect_piece("CUAD", 350, 350)] * 2,
    ),
  ]

  print("== Validación arga_base (colocación + movimiento BLF) ==")
  failed = 0
  for case_name, piezas in cases:
    ok, msg = _run_case(case_name, piezas, w, h)
    print(f"{'PASS' if ok else 'FAIL'}: {msg}")
    if not ok:
      failed += 1

  return 1 if failed else 0


if __name__ == "__main__":
  sys.exit(main())
