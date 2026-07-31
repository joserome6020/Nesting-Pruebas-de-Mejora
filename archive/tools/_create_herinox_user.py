"""Crea usuario SUPERUSER en BD Herinox (tabla User)."""
from __future__ import annotations

import sys

import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor

from catalogo_largos import HERINOX_DB_CONFIG


def _new_cuid() -> str:
    try:
        from cuid import cuid as _cuid

        return _cuid()
    except ImportError:
        import secrets
        import string
        import time

        # Fallback compatible con ids existentes (25 chars, empieza con 'c')
        alphabet = string.ascii_lowercase + string.digits
        ts = format(int(time.time() * 1000), "x")[-8:]
        body = "".join(secrets.choice(alphabet) for _ in range(16))
        return f"c{ts}{body}"[:25]


def main() -> int:
    email = "rosa_alvarado@grupoarga.com"
    name = "Rosa Alvarado"
    password = "DyT12345!"
    role = "SUPERUSER"

    if len(sys.argv) >= 2:
        email = sys.argv[1]
    if len(sys.argv) >= 3:
        name = sys.argv[2]
    if len(sys.argv) >= 4:
        password = sys.argv[3]
    if len(sys.argv) >= 5:
        role = sys.argv[4].upper()

    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")

    conn = psycopg2.connect(**HERINOX_DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'User'
                ORDER BY ordinal_position
                """
            )
            cols = cur.fetchall()
            print("Columnas User:", [c["column_name"] for c in cols])

            cur.execute('SELECT id, email, role FROM "User" WHERE lower(email) = lower(%s)', (email,))
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE "User"
                    SET name = %s, "passwordHash" = %s, role = %s, "updatedAt" = NOW()
                    WHERE lower(email) = lower(%s)
                    RETURNING id, name, email, role
                    """,
                    (name, pw_hash, role, email),
                )
                row = cur.fetchone()
                conn.commit()
                print("OK: usuario actualizado", dict(row))
                return 0

            uid = _new_cuid()
            col_names = [c["column_name"] for c in cols]
            if "createdAt" in col_names and "updatedAt" in col_names:
                cur.execute(
                    """
                    INSERT INTO "User" (id, name, email, "passwordHash", role, "createdAt", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING id, name, email, role
                    """,
                    (uid, name, email, pw_hash, role),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO "User" (id, name, email, "passwordHash", role)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, name, email, role
                    """,
                    (uid, name, email, pw_hash, role),
                )
            row = cur.fetchone()
            conn.commit()
            print("OK: usuario creado", dict(row))
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
