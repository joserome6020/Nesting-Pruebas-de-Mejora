"""Aviso por correo de cada release publicado del ANS.

Arma un correo formal (HTML, español) con el link de descarga del zip y las
instrucciones de instalación, y lo envía por Outlook de escritorio con la
cuenta de la PC que publica (sin credenciales SMTP en el repo).

Uso típico (lo llama `publish_release.py --notify`, o suelto):

  # Enviar a los supervisores por defecto:
  python tools/notify_release.py

  # Prueba a un solo destinatario:
  python tools/notify_release.py --to jose_rosales@grupoarga.com

  # Solo generar el HTML para revisarlo, sin enviar:
  python tools/notify_release.py --preview _tmp/aviso_release.html

Requiere que `latest.json` ya tenga `url` (es decir, que el release esté
publicado); si no, no hay link que mandar y se aborta.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LATEST_JSON = ROOT / "dist" / "releases" / "latest.json"

DEFAULT_RECIPIENTS: tuple[str, ...] = (
    "jose_rosales@grupoarga.com",
    "aaron_orrantia@grupoarga.com",
    "quotes@grupoarga.com",
)

EXE_NAME = "ARGA NESTING SUITE.exe"
CARPETA_SUGERIDA = r"C:\ARGA NESTING SUITE"
MAX_NOVEDADES = 15

_GITHUB_ASSET_RE = re.compile(
    r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/releases/download/(?P<tag>[^/]+)/"
)


def _run(cmd: list[str], timeout: int = 60) -> str:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"código {proc.returncode}")
    return proc.stdout


def _formato_tamano(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "—"
    return f"{size_bytes / (1024 * 1024):,.0f} MB"


def _fecha_local(iso_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_utc)
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d/%m/%Y %H:%M")


def release_page_url(latest: dict) -> str:
    m = _GITHUB_ASSET_RE.match(str(latest.get("url") or ""))
    if not m:
        return ""
    return f"https://github.com/{m.group('repo')}/releases/tag/{m.group('tag')}"


def novedades_desde_release_anterior(latest: dict) -> list[str]:
    """Asuntos de commits entre el release anterior publicado y este.

    Best-effort: sin `gh`, sin red o sin tag previo devuelve lista vacía y el
    correo sale sin la sección de novedades.
    """
    m = _GITHUB_ASSET_RE.match(str(latest.get("url") or ""))
    commit = str(latest.get("commit") or "").strip()
    if not m or not commit:
        return []
    repo, tag = m.group("repo"), m.group("tag")
    try:
        releases = json.loads(
            _run(["gh", "release", "list", "--repo", repo, "--limit", "30",
                  "--json", "tagName,publishedAt"])
        )
        previos = sorted(
            (r for r in releases if r.get("tagName") != tag and r.get("publishedAt")),
            key=lambda r: r["publishedAt"],
            reverse=True,
        )
        if not previos:
            return []
        prev_tag = previos[0]["tagName"]
        _run(["git", "fetch", "--quiet", "origin", f"refs/tags/{prev_tag}:refs/tags/{prev_tag}"])
        log = _run(["git", "log", "--no-merges", "--pretty=%s", f"{prev_tag}..{commit}"])
    except Exception as exc:
        print(f"[WARN] No se pudieron obtener novedades: {exc}")
        return []
    asuntos = [ln.strip() for ln in log.splitlines() if ln.strip()]
    if len(asuntos) > MAX_NOVEDADES:
        resto = len(asuntos) - MAX_NOVEDADES
        asuntos = asuntos[:MAX_NOVEDADES] + [f"… y {resto} cambio(s) más."]
    return asuntos


def construir_asunto(latest: dict) -> str:
    return (
        f"ARGA NESTING SUITE — Nueva versión disponible "
        f"{latest.get('version', '')} ({latest.get('commit_short', '')})"
    )


def construir_html(latest: dict, novedades: list[str] | None = None) -> str:
    e = html.escape
    version = e(str(latest.get("version") or ""))
    commit_short = e(str(latest.get("commit_short") or ""))
    url = str(latest.get("url") or "")
    filename = e(str(latest.get("filename") or ""))
    tamano = e(_formato_tamano(int(latest.get("size_bytes") or 0)))
    fecha = e(_fecha_local(str(latest.get("published_at_utc") or "")))
    sha256 = e(str(latest.get("sha256") or ""))
    pagina = release_page_url(latest)
    exe = e(EXE_NAME)
    carpeta = e(CARPETA_SUGERIDA)

    novedades_html = ""
    if novedades:
        items = "".join(f"<li>{e(n)}</li>" for n in novedades)
        novedades_html = (
            '<h3 style="color:#1f3864;margin:22px 0 8px;">Cambios incluidos en esta versión</h3>'
            f'<ul style="margin:0;padding-left:20px;color:#333;">{items}</ul>'
        )

    pagina_html = (
        f'<p style="margin:6px 0 0;font-size:12px;color:#666;">Página del release: '
        f'<a href="{e(pagina)}" style="color:#2e75b6;">{e(pagina)}</a></p>'
        if pagina else ""
    )

    return f"""\
<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222;line-height:1.5;">
<div style="max-width:680px;">
<p>Estimados:</p>
<p>Por medio del presente les informamos que se encuentra disponible una nueva versión de
<b>ARGA NESTING SUITE</b>. A continuación se detallan los datos de la versión, el enlace de
descarga y las instrucciones de instalación.</p>

<table cellpadding="6" cellspacing="0" style="border-collapse:collapse;border:1px solid #d0d7e2;margin:12px 0;">
  <tr style="background:#f2f5fa;"><td style="border:1px solid #d0d7e2;"><b>Versión</b></td><td style="border:1px solid #d0d7e2;">{version}</td></tr>
  <tr><td style="border:1px solid #d0d7e2;"><b>Compilación</b></td><td style="border:1px solid #d0d7e2;">{commit_short}</td></tr>
  <tr style="background:#f2f5fa;"><td style="border:1px solid #d0d7e2;"><b>Fecha de publicación</b></td><td style="border:1px solid #d0d7e2;">{fecha}</td></tr>
  <tr><td style="border:1px solid #d0d7e2;"><b>Archivo</b></td><td style="border:1px solid #d0d7e2;">{filename} ({tamano})</td></tr>
</table>

<p style="margin:18px 0 4px;">
  <a href="{e(url)}" style="background:#1f3864;color:#ffffff;padding:10px 22px;text-decoration:none;border-radius:4px;font-weight:bold;display:inline-block;">
    Descargar ARGA NESTING SUITE {version}
  </a>
</p>
<p style="margin:4px 0 0;font-size:12px;color:#666;">Si el botón no funciona, copie este enlace en su navegador:<br>
<a href="{e(url)}" style="color:#2e75b6;">{e(url)}</a></p>
{pagina_html}

<h3 style="color:#1f3864;margin:22px 0 8px;">Instrucciones de instalación</h3>
<ol style="margin:0;padding-left:20px;">
  <li><b>Descargue</b> el archivo .zip desde el enlace anterior.</li>
  <li><b>Cierre ARGA NESTING SUITE</b> si lo tiene abierto.</li>
  <li><b>Descomprima</b> el .zip (clic derecho &rarr; <i>Extraer todo…</i>) en la carpeta que desee,
      por ejemplo <code>{carpeta}</code>.</li>
  <li><b>Cree un acceso directo en el escritorio:</b> dentro de la carpeta descomprimida, haga clic
      derecho sobre <b>{exe}</b> &rarr; <i>Mostrar más opciones</i> &rarr; <i>Enviar a</i> &rarr;
      <i>Escritorio (crear acceso directo)</i>.</li>
  <li><b>Abra el programa</b> desde el acceso directo del escritorio.</li>
</ol>

<h3 style="color:#1f3864;margin:22px 0 8px;">Consideraciones importantes</h3>
<ul style="margin:0;padding-left:20px;">
  <li>Mantenga <b>toda la carpeta descomprimida junta</b>. No mueva ni copie únicamente el archivo
      {exe}: el programa necesita el resto de los archivos de la carpeta para funcionar.
      Para tenerlo en el escritorio utilice siempre el acceso directo.</li>
  <li><b>Su configuración, historial de trabajos e inventario se conservan</b> al actualizar;
      se guardan fuera de la carpeta del programa.</li>
  <li>Si ya tenía una versión anterior, puede reemplazar la carpeta o descomprimir en una nueva
      y actualizar el acceso directo. Una vez que confirme que la nueva versión abre correctamente,
      puede eliminar la carpeta de la versión anterior.</li>
  <li>Si Windows muestra el aviso <i>"Windows protegió su PC"</i>, seleccione
      <i>Más información</i> &rarr; <i>Ejecutar de todas formas</i>.</li>
  <li>Al abrir una versión anterior, el programa también puede avisar que hay una actualización
      disponible; puede aceptar ese aviso o seguir estas instrucciones manualmente.</li>
</ul>
{novedades_html}

<p style="margin-top:22px;">Ante cualquier duda o incidencia con la instalación, favor de responder a este correo
o reportarlo mediante el buzón de soporte del programa.</p>

<p>Atentamente,<br><b>Equipo de Desarrollo — ARGA NESTING SUITE</b><br>Grupo Arga</p>

<p style="margin-top:18px;font-size:11px;color:#888;">Verificación de integridad (SHA-256): {sha256}</p>
</div>
</body></html>
"""


def enviar_por_outlook(*, to: list[str], subject: str, html_body: str, display_only: bool = False) -> None:
    """Envía con Outlook de escritorio. COM directo; fallback PowerShell STA."""
    destinatarios = "; ".join(to)
    try:
        import win32com.client  # type: ignore

        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)
        mail.To = destinatarios
        mail.Subject = subject
        mail.HTMLBody = html_body
        if display_only:
            mail.Display(False)
        else:
            mail.Send()
        return
    except ImportError:
        pass

    tmp = Path(tempfile.mkdtemp(prefix="ans_notify_"))
    body_path = tmp / "body.html"
    body_path.write_text(html_body, encoding="utf-8")
    meta_path = tmp / "meta.json"
    meta_path.write_text(
        json.dumps({"to": destinatarios, "subject": subject, "body_path": str(body_path)}, ensure_ascii=False),
        encoding="utf-8",
    )
    accion = "$mail.Display($false)" if display_only else "$mail.Send()"
    script = tmp / "send.ps1"
    script.write_text(
        "\n".join([
            "$ErrorActionPreference = 'Stop'",
            f"$meta = Get-Content -LiteralPath '{meta_path}' -Raw -Encoding UTF8 | ConvertFrom-Json",
            "$outlook = New-Object -ComObject Outlook.Application",
            "$mail = $outlook.CreateItem(0)",
            "$mail.To = $meta.to",
            "$mail.Subject = $meta.subject",
            "$mail.HTMLBody = Get-Content -LiteralPath $meta.body_path -Raw -Encoding UTF8",
            accion,
        ]),
        encoding="utf-8",
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        check=True,
        timeout=120,
    )


def notificar_release(
    latest: dict,
    *,
    to: list[str] | None = None,
    preview: Path | None = None,
    display_only: bool = False,
    incluir_novedades: bool = True,
) -> list[str]:
    if not str(latest.get("url") or "").strip():
        raise SystemExit(
            "latest.json no tiene 'url': publica primero con `tools/publish_release.py`."
        )
    destinatarios = [d.strip() for d in (to or DEFAULT_RECIPIENTS) if d.strip()]
    novedades: list[str] = []
    if incluir_novedades:
        notas = [ln.strip(" -*•\t") for ln in str(latest.get("notes") or "").splitlines()]
        novedades = [n for n in notas if n] or novedades_desde_release_anterior(latest)
    asunto = construir_asunto(latest)
    cuerpo = construir_html(latest, novedades)
    if preview is not None:
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(cuerpo, encoding="utf-8")
        print(f"[OK] Vista previa escrita: {preview}")
        return []
    enviar_por_outlook(to=destinatarios, subject=asunto, html_body=cuerpo, display_only=display_only)
    accion = "Borrador abierto para" if display_only else "Aviso enviado a"
    print(f"[OK] {accion}: {', '.join(destinatarios)}")
    return destinatarios


def main() -> int:
    parser = argparse.ArgumentParser(description="Envía el aviso de release del ANS por correo.")
    parser.add_argument("--latest", default=str(DEFAULT_LATEST_JSON), help="Ruta a latest.json publicado.")
    parser.add_argument(
        "--to",
        action="append",
        default=[],
        help="Destinatario (repetible). Por defecto: " + ", ".join(DEFAULT_RECIPIENTS),
    )
    parser.add_argument("--preview", default="", help="Escribe el HTML en esta ruta y no envía.")
    parser.add_argument("--display", action="store_true", help="Abre el borrador en Outlook sin enviarlo.")
    parser.add_argument("--sin-novedades", action="store_true", help="Omite la lista de cambios.")
    parser.add_argument(
        "--nota",
        action="append",
        default=[],
        help="Novedad a listar (repetible). Reemplaza la lista automática de commits.",
    )
    args = parser.parse_args()

    latest_path = Path(args.latest).resolve()
    if not latest_path.is_file():
        raise SystemExit(f"No existe {latest_path}.")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    if args.nota:
        latest["notes"] = "\n".join(args.nota)
    notificar_release(
        latest,
        to=args.to or None,
        preview=Path(args.preview).resolve() if args.preview else None,
        display_only=args.display,
        incluir_novedades=not args.sin_novedades,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
