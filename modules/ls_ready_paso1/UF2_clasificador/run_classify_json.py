# -*- coding: utf-8 -*-
"""
CLI: clasificar JSON crudo del lector DXF -> plan por piezas.

Uso:
  cd Desktop\\CARLOS RPA
  python run_classify_json.py
  python run_classify_json.py ruta\\al\\archivo.json
  python run_classify_json.py ruta\\al\\archivo.json salida.json
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CONFIG_DIR = os.path.join(ROOT, "config")
if CONFIG_DIR not in sys.path:
    sys.path.insert(0, CONFIG_DIR)

import classification_config as cfg
from classification.classifier import classify_json_file


def resolve_input_json(argv):
    if len(argv) >= 2 and argv[1].strip():
        return os.path.normpath(argv[1])
    explicit = getattr(cfg, "JSON_FILE_EXPLICIT", None)
    if not explicit:
        try:
            import carousel_config as carousel_cfg
            explicit = getattr(carousel_cfg, "JSON_FILE_EXPLICIT", None)
        except ImportError:
            explicit = None
    if explicit and os.path.isfile(explicit):
        return os.path.normpath(explicit)
    source_dir = getattr(cfg, "JSON_SOURCE_DIR", "")
    if source_dir and os.path.isdir(source_dir):
        candidates = []
        for name in os.listdir(source_dir):
            if name.lower().endswith(".json"):
                path = os.path.join(source_dir, name)
                candidates.append((os.path.getmtime(path), path))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]
    return None


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    json_path = resolve_input_json(argv)
    if not json_path or not os.path.isfile(json_path):
        print("No se encontro JSON de entrada.")
        print("Uso: python run_classify_json.py <entrada.json> [salida.json]")
        return 1

    output_path = None
    if len(argv) >= 3 and argv[2].strip():
        output_path = os.path.normpath(argv[2])

    logs = []
    try:
        result = classify_json_file(json_path, output_path=output_path, logs=logs)
    except Exception as exc:
        print("ERROR:", exc)
        traceback.print_exc()
        return 1

    out_path = result.get("source", {}).get("classified_json_path", output_path)
    summary = result.get("summary") or {}
    track = result.get("track_flow") or {}
    profile = result.get("plate_profile") or {}

    print("=" * 60)
    print("CLASIFICACION OK")
    print("Entrada:", json_path)
    print("Salida:", out_path)
    print(
        "Perfil:",
        profile.get("key"),
        "(solicitado:",
        profile.get("requested_key"),
        ")",
    )
    if profile.get("warnings"):
        for w in profile["warnings"]:
            print("  AVISO:", w)
    print("Piezas:", summary.get("piece_count"))
    print("Orden MARK (der->izq):", " -> ".join(track.get("piece_order_mark") or []))
    print("Orden CUT  (izq->der):", " -> ".join(track.get("piece_order_cut") or []))
    print("Pasos mark:", summary.get("mark_step_count"))
    print("Pasos cut:", summary.get("cut_step_count"))
    print("=" * 60)
    for line in logs:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
