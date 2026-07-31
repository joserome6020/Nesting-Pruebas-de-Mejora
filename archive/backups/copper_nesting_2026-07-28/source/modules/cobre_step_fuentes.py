"""Manifiesto y STEP de cobre desde DXF fuente (sin mover ni renombrar archivos)."""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

MANIFEST_FILENAME = "cobre_dxf_fuentes.json"
MANIFEST_VERSION = 1


def _norm_ruta(path: str) -> str:
    return os.path.normpath(str(path or "").strip())


def espesor_in_desde_clave(clave: str, default: float = 0.25) -> float:
    raw = str(clave or "").split("_", 1)[0].strip()
    if not raw:
        return default
    if "/" in raw:
        try:
            num, den = raw.split("/", 1)
            return float(num) / float(den)
        except Exception:
            return default
    try:
        return float(raw)
    except Exception:
        return default


def recolectar_fuentes_cobre_desde_resultados(resultados: dict) -> dict[str, Any]:
    """Únicas rutas DXF de piezas reales en grupos modo_largos_cu."""
    fuentes: dict[str, dict[str, str]] = {}
    claves_cu: list[str] = []

    for clave, data in (resultados or {}).items():
        if not isinstance(data, dict) or not data.get("modo_largos_cu"):
            continue
        claves_cu.append(str(clave))
        for hoja in data.get("hojas") or []:
            if not isinstance(hoja, dict):
                continue
            for pz in hoja.get("piezas") or []:
                if not isinstance(pz, dict):
                    continue
                nom = str(pz.get("nombre") or "").strip()
                if not nom or nom.startswith("CU_CORTE__"):
                    continue
                ruta = _norm_ruta(pz.get("ruta") or "")
                if not ruta or not os.path.isfile(ruta):
                    continue
                key = ruta.casefold()
                if key not in fuentes:
                    fuentes[key] = {"nombre": nom, "ruta_dxf": ruta}

    clave_ref = claves_cu[0] if claves_cu else ""
    return {
        "version": MANIFEST_VERSION,
        "clave": clave_ref,
        "espesor_in": espesor_in_desde_clave(clave_ref),
        "fuentes": sorted(fuentes.values(), key=lambda x: x.get("nombre", "")),
    }


def escribir_manifest_cobre(destino_dir: str, manifest: dict[str, Any]) -> str:
    destino_dir = _norm_ruta(destino_dir)
    os.makedirs(destino_dir, exist_ok=True)
    path = os.path.join(destino_dir, MANIFEST_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return path


def cargar_manifest_cobre(ruta_manifest: str) -> Optional[dict[str, Any]]:
    path = _norm_ruta(ruta_manifest)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def buscar_manifest_en_nesting(ruta_nesting: str) -> Optional[dict[str, Any]]:
    """Busca cobre_dxf_fuentes.json en NESTEOS DE COBRE (y legacy CAMA LASER)."""
    root = _norm_ruta(ruta_nesting)
    if not root or not os.path.isdir(root):
        return None
    candidatos_dir = []
    for nombre in sorted(os.listdir(root)):
        upper = str(nombre or "").upper()
        if "NESTEOS DE COBRE" in upper:
            candidatos_dir.append(os.path.join(root, nombre))
    for nombre in sorted(os.listdir(root)):
        if re.search(r"CAMA\s+LASER", str(nombre or ""), re.IGNORECASE):
            path = os.path.join(root, nombre)
            if path not in candidatos_dir:
                candidatos_dir.append(path)
    for candidato_dir in candidatos_dir:
        candidato = os.path.join(candidato_dir, MANIFEST_FILENAME)
        data = cargar_manifest_cobre(candidato)
        if data and data.get("fuentes"):
            data["_manifest_path"] = candidato
            return data
    return None


def procesar_steps_cobre_en_ubicacion_fuentes(
    manifest: dict[str, Any],
    *,
    thk_mm: float | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> list[tuple[str, bool, str]]:
    """
    Genera {mismo_nombre}.step junto a cada DXF fuente (sin mover ni renombrar el DXF).
    Agrupa por carpeta y delega a FreeCAD batch.
    """
    from freecad_runner import ejecutar_macro_freecad

    _log = log_fn or (lambda _msg: None)
    fuentes = manifest.get("fuentes") or []
    if not fuentes:
        return []

    if thk_mm is None:
        esp_in = float(manifest.get("espesor_in") or 0.25)
        thk_mm = esp_in * 25.4

    por_carpeta: dict[str, list[str]] = defaultdict(list)
    for item in fuentes:
        if not isinstance(item, dict):
            continue
        ruta = _norm_ruta(item.get("ruta_dxf") or "")
        if not ruta or not os.path.isfile(ruta):
            _log(f"[COBRE-FUENTE][SKIP] DXF no accesible: {ruta or item.get('nombre', '?')}")
            continue
        por_carpeta[os.path.dirname(ruta)].append(ruta)

    resultados: list[tuple[str, bool, str]] = []
    os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"

    for carpeta, rutas in sorted(por_carpeta.items()):
        _log(
            f"[COBRE-FUENTE] STEP in-place: {len(rutas)} DXF en {carpeta} "
            f"(thk={thk_mm:.3f} mm)"
        )
        ok = ejecutar_macro_freecad(
            carpeta,
            carpeta,
            float(thk_mm),
            "TR",
            0.0,
            0.0,
            0.0,
            prefer_verde=False,
            max_intentos=2,
            material="CU",
        )
        resultados.append((carpeta, bool(ok), carpeta))

    return resultados
