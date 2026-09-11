"""Upsert usuario VSM de servicio ANS + siembra auth + cierra job 62248."""
from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.request
from pathlib import Path

import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[2]
EMAIL = "ans_service@grupoarga.com"
FULL_NAME = "ANS Service"
AUTH_NAME = "centralized_auth.local.json"


def _load_or_make_password() -> str:
    env = str(os.environ.get("CENTRALIZED_AUTH_PASSWORD") or "").strip()
    if env:
        return env
    for path in (
        ROOT / AUTH_NAME,
        ROOT / "defaults" / AUTH_NAME,
        Path(os.environ.get("LOCALAPPDATA", "")) / "ArgaNestingSuite" / "data" / AUTH_NAME,
    ):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        email = str(data.get("email") or data.get("usuario") or "").strip()
        password = str(data.get("password") or data.get("clave") or "").strip()
        if password and (not email or email.lower() == EMAIL.lower()):
            return password
    return "AnsVsmSync_" + secrets.token_urlsafe(18)


def _upsert_user(password: str) -> int:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    conn = psycopg2.connect(
        host="192.168.2.80",
        port=5437,
        dbname="foldertree",
        user="user",
        password="password",
        connect_timeout=10,
        cursor_factory=RealDictCursor,
    )
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s)", (EMAIL,))
    row = cur.fetchone()
    if row:
        cur.execute(
            """
            UPDATE users
            SET hashed_password=%s, is_active=true, role=%s, full_name=%s
            WHERE id=%s
            """,
            (hashed, "admin", FULL_NAME, row["id"]),
        )
        uid = int(row["id"])
        action = "UPDATED"
    else:
        cur.execute(
            """
            INSERT INTO users (email, hashed_password, full_name, role, is_active)
            VALUES (%s, %s, %s, %s, true)
            RETURNING id
            """,
            (EMAIL, hashed, FULL_NAME, "admin"),
        )
        uid = int(cur.fetchone()["id"])
        action = "CREATED"
    conn.commit()
    cur.close()
    conn.close()
    print(f"{action} {EMAIL} id={uid}")
    return uid


def _write_auth(path: Path, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"email": EMAIL, "password": password}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"AUTH written {path}")


def _probe_login(password: str) -> bool:
    data = json.dumps({"email": EMAIL, "password": password}).encode()
    req = urllib.request.Request(
        "http://192.168.2.80:8010/auth/login?para_iframe=true",
        data=data,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode())
            token = bool(body.get("access_token"))
            print(f"login HTTP {resp.status} token={token}")
            return token
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = exc.read().decode()[:200]
            except Exception:
                detail = str(exc)
        print(f"login FAIL {getattr(exc, 'code', None)} {detail or exc}")
        return False


def main() -> int:
    password = _load_or_make_password()
    # Preferir email de servicio fijo (no el .arga.com roto).
    os.environ["CENTRALIZED_AUTH_EMAIL"] = EMAIL
    os.environ["CENTRALIZED_AUTH_PASSWORD"] = password
    _upsert_user(password)
    targets = [
        ROOT / AUTH_NAME,
        ROOT / "defaults" / AUTH_NAME,
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "ArgaNestingSuite"
        / "data"
        / AUTH_NAME,
    ]
    for p in targets:
        _write_auth(p, password)
    if not _probe_login(password):
        return 1

    sys.path.insert(0, str(ROOT))
    from modules.nesting_engine import api_client

    api_client._CENTRALIZED_LOGIN_OK = False
    api_client._CENTRALIZED_BEARER = None
    try:
        api_client._CENTRALIZED_COOKIE_JAR.clear()
    except Exception:
        pass
    if not api_client.ensure_centralized_session(force=True):
        print("ensure_centralized_session FAILED")
        return 1
    result = api_client.avanzar_job_centralizado("62248")
    print("avance 62248:", bool(result), result.summary() if result else None)
    if not result:
        return 1

    try:
        from interface.export_checkpoint_service import guardar_checkpoint_export

        guardar_checkpoint_export(
            "62248",
            "JOB",
            "VSM_JOB:62248",
            status="OK",
            detail=result.summary(),
            http_status=getattr(result, "http_status", None),
        )
        print("checkpoint VSM_JOB:62248 -> OK")
    except Exception as exc:
        print("checkpoint warn:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
