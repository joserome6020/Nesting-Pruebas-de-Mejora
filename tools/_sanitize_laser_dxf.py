"""Quita capas fantasma de cobre (y otras vacías) de DXF láser ya exportados."""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf

# Solo cobre / metadatos — no deben existir en nesteos láser normales.
COBRE_ONLY_LAYERS = frozenset({"CUT_CU", "BAR_START", "ARGA_META"})
KEEP_ALWAYS = frozenset({"0", "Defpoints"})


def _save_dxf_with_retry(doc, out_path: str, *, attempts: int = 12) -> None:
  import time
  import uuid

  out_path = os.path.abspath(str(out_path))
  out_dir = os.path.dirname(out_path) or "."
  os.makedirs(out_dir, exist_ok=True)
  tmp_path = os.path.join(
    out_dir,
    f".__arga_sanitize_{os.getpid()}_{uuid.uuid4().hex[:8]}.dxf",
  )
  doc.saveas(tmp_path)
  last_err: OSError | None = None
  for attempt in range(attempts):
    try:
      os.replace(tmp_path, out_path)
      return
    except OSError as exc:
      last_err = exc
      time.sleep(0.35 * (attempt + 1))
  try:
    if os.path.isfile(tmp_path):
      os.remove(tmp_path)
  except OSError:
    pass
  raise PermissionError(f"No se pudo escribir {out_path}: {last_err}") from last_err


def sanitize_laser_dxf(path: str, *, dry_run: bool = False, out_path: str | None = None) -> tuple[list[str], list[str]]:
  """Devuelve (capas_eliminadas, capas_finales)."""
  path = os.path.abspath(path)
  target = os.path.abspath(out_path or path)
  doc = ezdxf.readfile(path)
  msp = doc.modelspace()
  used = {str(e.dxf.layer) for e in msp if getattr(e.dxf, "layer", None)}

  removed: list[str] = []
  for layer in list(doc.layers):
    name = str(layer.dxf.name)
    if name in KEEP_ALWAYS or name in used:
      continue
    # Cobre siempre fuera si está vacía; el resto de vacías también.
    if name in COBRE_ONLY_LAYERS or name not in used:
      removed.append(name)
      if not dry_run:
        try:
          doc.layers.remove(name)
        except Exception:
          pass

  if not dry_run and removed:
    _save_dxf_with_retry(doc, target)

  final_layers = sorted(l.dxf.name for l in doc.layers)
  return removed, final_layers


def main() -> int:
  parser = argparse.ArgumentParser(description="Limpia capas cobre vacías en DXF láser.")
  parser.add_argument("dxf_dir", help="Carpeta con *.dxf")
  parser.add_argument("--dry-run", action="store_true")
  args = parser.parse_args()

  dxf_dir = os.path.normpath(args.dxf_dir)
  files = sorted(
    p
    for p in glob.glob(os.path.join(dxf_dir, "*.dxf"))
    if "_clean" not in p.replace("\\", "/")
    and ".tmp_" not in os.path.basename(p)
    and ".__arga_" not in os.path.basename(p)
  )
  if not files:
    print(f"[ERROR] Sin DXF en {dxf_dir}")
    return 1

  total_removed = 0
  clean_dir = os.path.join(dxf_dir, "_clean")
  used_clean = False
  for path in files:
    try:
      removed, final = sanitize_laser_dxf(path, dry_run=args.dry_run)
      out_note = "in-place"
    except PermissionError:
      if args.dry_run:
        raise
      os.makedirs(clean_dir, exist_ok=True)
      clean_path = os.path.join(clean_dir, os.path.basename(path))
      removed, final = sanitize_laser_dxf(path, out_path=clean_path)
      out_note = f"copia -> {clean_path}"
      used_clean = True
    total_removed += len(removed)
    tag = "DRY" if args.dry_run else "OK"
    print(f"[{tag}] {os.path.basename(path)} ({out_note}): -{len(removed)} capas -> {final}")

  print(f"\n[{ 'DRY-RUN' if args.dry_run else 'LISTO' }] {len(files)} DXF | capas eliminadas: {total_removed}")
  if used_clean and not args.dry_run:
    print(f"[INFO] Algunos DXF estaban bloqueados; copias limpias en: {clean_dir}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
