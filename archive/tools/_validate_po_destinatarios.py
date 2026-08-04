"""Valida lista DESTINATARIOS_PO y prueba SMTP (sin depender del venv remoto)."""
from __future__ import annotations

import ast
import os
import smtplib
import sys
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(r"\\192.168.2.80\Users\Administrator\Music\Desarrollo\InsertaPOContPaq")
REQUIRED = (
    "joel_salcido@herinox.com",
    "jose_rosales@grupoarga.com",
)


def _leer_destinatarios() -> tuple[str, ...]:
    src = (ROOT / "reportePOGAM.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "DESTINATARIOS_PO":
                    return tuple(ast.literal_eval(node.value))
    raise RuntimeError("No se encontro DESTINATARIOS_PO en reportePOGAM.py")


def _limpiar(valor: str) -> str:
    texto = str(valor or "").strip()
    if len(texto) >= 2 and texto[0] == texto[-1] and texto[0] in ("'", '"'):
        texto = texto[1:-1].strip()
    return texto


def _credenciales():
    # Prefer python-dotenv if present; else parse .env simple
    env_path = ROOT / ".env"
    archivo = {}
    try:
        from dotenv import dotenv_values

        archivo = dict(dotenv_values(env_path) or {})
    except Exception:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            archivo[k.strip()] = v.strip()

    remitente = _limpiar(os.environ.get("REMITENTE") or archivo.get("REMITENTE"))
    password = _limpiar(os.environ.get("PASSWORD") or archivo.get("PASSWORD"))
    smtp_server = _limpiar(
        os.environ.get("SMTP_SERVER") or archivo.get("SMTP_SERVER") or "bolon.hosting-mexico.net"
    )
    smtp_port = int(_limpiar(os.environ.get("SMTP_PORT") or archivo.get("SMTP_PORT") or "465"))
    if not remitente or "@" not in remitente or not password:
        raise RuntimeError("Credenciales SMTP incompletas en .env InsertaPO")
    return remitente, password, smtp_server, smtp_port


def _mini_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        b"4 0 obj<< /Length 55 >>stream\n"
        b"BT /F1 12 Tf 20 100 Td (InsertaPO email test) Tj ET\n"
        b"endstream endobj\n"
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"trailer<< /Size 6 /Root 1 0 R >>\n"
        b"startxref\n0\n%%EOF\n"
    )


def _enviar(destinatarios, asunto, cuerpo, pdf_bytes, nombre_pdf):
    remitente, password, smtp_server, smtp_port = _credenciales()
    msg = MIMEMultipart()
    msg["From"] = remitente
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
    adj = MIMEBase("application", "octet-stream")
    adj.set_payload(pdf_bytes)
    encoders.encode_base64(adj)
    adj.add_header("Content-Disposition", f"attachment; filename={nombre_pdf}")
    msg.attach(adj)
    print(
        f"[CORREO] {smtp_server}:{smtp_port} from {remitente} -> {', '.join(destinatarios)}"
    )
    with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=45) as servidor:
        servidor.login(remitente, password)
        rechazados = servidor.sendmail(remitente, list(destinatarios), msg.as_string())
    if rechazados:
        raise RuntimeError(f"SMTP rechazo parcial: {rechazados}")
    return True


def main() -> int:
    dest = _leer_destinatarios()
    print(f"DESTINATARIOS_PO ({len(dest)}):")
    for d in dest:
        mark = "NEW" if d.lower() in {x.lower() for x in REQUIRED} else "   "
        print(f"  [{mark}] {d}")

    missing = [e for e in REQUIRED if e.lower() not in {x.lower() for x in dest}]
    if missing:
        print("FAIL faltan:", missing)
        return 2
    if len(dest) < 8:
        print("FAIL: se esperaban >= 8 destinatarios, hay", len(dest))
        return 2
    print("OK: lista en disco correcta (incluye joel + jose)")

    # Contenedor: el codigo vive en la imagen; avisamos si no podemos inspeccionarlo
    print("InsertaPO API: comprobar en paralelo (servicio en :8006)")

    if "--send" not in sys.argv:
        print("Dry-run OK. Use --send para prueba SMTP a los 2 nuevos.")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _enviar(
        REQUIRED,
        f"[TEST] Destinatarios PO InsertaPO — {stamp}",
        (
            "Prueba InsertaPOContPaq.\n\n"
            "Nuevos destinatarios PO validados:\n"
            f"- {REQUIRED[0]}\n- {REQUIRED[1]}\n\n"
            f"Fecha: {stamp}\n"
            "Si recibe este correo, SMTP y destinatarios OK.\n"
        ),
        _mini_pdf(),
        "TEST_PO_DESTINATARIOS.pdf",
    )
    print("SEND OK: SMTP acepto envio a joel_salcido + jose_rosales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
