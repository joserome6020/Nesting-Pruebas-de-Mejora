"""Valida que una MRL SWO coincida exactamente con su plan canónico."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interface.largos_nesting_service import validar_mrl_swo_canonica_tras_export


parser = argparse.ArgumentParser()
parser.add_argument("--swo", required=True)
args = parser.parse_args()
ok, message = validar_mrl_swo_canonica_tras_export(args.swo)
print(message)
raise SystemExit(0 if ok else 1)
