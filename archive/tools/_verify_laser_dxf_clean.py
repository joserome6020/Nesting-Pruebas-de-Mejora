"""Verifica que DXF láser no tengan capas cobre."""
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf

COBRE = frozenset({"CUT_CU", "BAR_START", "ARGA_META"})
DIRS = [
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals\ARGA METALS CORPORATE SYSTEM\TANKS\VANTRAN\06-30-2275TANK25325\MODEL CORE FILES\W.O. 8 X8\ARGA MODEL CORE\NESTING\ROBOT LASER + MINI NEST\DXF",
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals\ARGA METALS CORPORATE SYSTEM\TANKS\VANTRAN\06-30-2275TANK25325\MODEL CORE FILES\W.O. 9 X9\ARGA MODEL CORE\NESTING\ROBOT LASER + MINI NEST\DXF",
]

bad = []
for d in DIRS:
    for p in sorted(glob.glob(os.path.join(d, "*.dxf"))):
        doc = ezdxf.readfile(p)
        layers = {l.dxf.name for l in doc.layers}
        used = {str(e.dxf.layer) for e in doc.modelspace()}
        leak = (layers | used) & COBRE
        if leak:
            bad.append((os.path.basename(p), sorted(leak)))

if bad:
    print("FALLO:", bad)
    raise SystemExit(1)
print("OK: sin capas cobre en", sum(len(glob.glob(os.path.join(d, '*.dxf'))) for d in DIRS), "DXF")
