"""Auto-update stub para ANS C++ (Fase D profundidad).

No descarga binarios automáticamente sin confirmación.
Lee `native/update_manifest.example.json` o URL configurada.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_MANIFEST = _ROOT / "native" / "update_manifest.json"
_EXAMPLE = _ROOT / "native" / "update_manifest.example.json"


@dataclass
class UpdateInfo:
    current: str
    latest: str
    url: str | None
    notes: str
    update_available: bool


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def current_core_version() -> str:
    try:
        from modules.nesting_engine import arga_nest_core_bridge as bridge

        return str(bridge.core_status().get("version") or "unknown")
    except Exception:
        return "unknown"


def check_for_update(*, manifest_url: str | None = None) -> UpdateInfo:
    """Consulta manifiesto local o remoto. Nunca instala solo."""
    data: dict
    if manifest_url:
        with urllib.request.urlopen(manifest_url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    elif _LOCAL_MANIFEST.is_file():
        data = _read_json(_LOCAL_MANIFEST)
    elif _EXAMPLE.is_file():
        data = _read_json(_EXAMPLE)
    else:
        return UpdateInfo(current_core_version(), "?", None, "sin manifiesto", False)

    current = current_core_version()
    latest = str(data.get("latest_version") or "")
    url = data.get("download_url")
    notes = str(data.get("notes") or "")
    available = bool(latest) and latest not in current and latest != current
    return UpdateInfo(current, latest, url, notes, available)


def apply_update_instructions() -> str:
    return (
        "Auto-update no aplica binarios sin aprobación IT.\n"
        "1) Revisar update_manifest.json\n"
        "2) Descargar paquete firmado (Authenticode)\n"
        "3) Verificar hash SHA256 del manifiesto\n"
        "4) Sustituir native/bin + modules/nesting_engine/arga_nest_core*.pyd\n"
        "5) Ejecutar tests/native/test_suite_ans_cpp.py"
    )


if __name__ == "__main__":
    info = check_for_update()
    print(json.dumps(info.__dict__, indent=2, ensure_ascii=False))
    print(apply_update_instructions())
