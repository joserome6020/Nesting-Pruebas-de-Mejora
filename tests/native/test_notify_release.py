"""Candado 2026-09-30d: aviso por correo de cada release (tools/notify_release.py).

El correo debe llevar el link del zip publicado, las instrucciones de
instalación (descomprimir + acceso directo al .exe sin separarlo de la
carpeta), respetar notas manuales y negarse a salir sin `url`.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "tools"))

import notify_release as nr  # noqa: E402

LATEST = {
    "version": "2026.09.30",
    "commit": "524e3258bfb12349cc65669ad516eaa270318b08",
    "commit_short": "524e3258",
    "filename": "ArgaNestingSuite-2026.09.30-524e3258.zip",
    "url": "https://github.com/joserome6020/Nesting-Pruebas-de-Mejora/releases/download/"
           "v2026.09.30-524e3258/ArgaNestingSuite-2026.09.30-524e3258.zip",
    "sha256": "341aab0c",
    "size_bytes": 452759129,
    "published_at_utc": "2026-09-30T15:08:34+00:00",
    "notes": "- Gaps 0.375 / 0.260\n- Piezas <= 456.954 in2 sin marcaje",
}


def main() -> int:
    fallos: list[str] = []
    cuerpo = nr.construir_html(LATEST, ["Gaps 0.375 / 0.260", "Piezas <= 456.954 in2"])

    for esperado in (
        LATEST["url"],
        "https://github.com/joserome6020/Nesting-Pruebas-de-Mejora/releases/tag/v2026.09.30-524e3258",
        "Descomprima",
        "Escritorio (crear acceso directo)",
        nr.EXE_NAME,
        "toda la carpeta descomprimida junta",
        "432 MB",
        "Piezas &lt;= 456.954 in2",
    ):
        if esperado not in cuerpo:
            fallos.append(f"falta en el cuerpo: {esperado!r}")

    if "2026.09.30" not in nr.construir_asunto(LATEST):
        fallos.append("asunto sin versión")

    if set(nr.DEFAULT_RECIPIENTS) != {"jose_rosales@grupoarga.com", "aaron_orrantia@grupoarga.com"}:
        fallos.append(f"destinatarios por defecto: {nr.DEFAULT_RECIPIENTS}")

    enviados: list[dict] = []
    original = nr.enviar_por_outlook
    nr.enviar_por_outlook = lambda **kw: enviados.append(kw)
    try:
        nr.notificar_release(LATEST, to=["jose_rosales@grupoarga.com"])
        try:
            nr.notificar_release({**LATEST, "url": ""})
            fallos.append("sin url no debe enviarse")
        except SystemExit:
            pass
    finally:
        nr.enviar_por_outlook = original

    if len(enviados) != 1 or enviados[0]["to"] != ["jose_rosales@grupoarga.com"]:
        fallos.append(f"envío de prueba incorrecto: {[e.get('to') for e in enviados]}")
    elif "Gaps 0.375 / 0.260" not in enviados[0]["html_body"]:
        fallos.append("notas manuales de latest.json no usadas")

    if fallos:
        print("FAIL test_notify_release")
        for f in fallos:
            print("  -", f)
        return 1
    print("PASS test_notify_release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
