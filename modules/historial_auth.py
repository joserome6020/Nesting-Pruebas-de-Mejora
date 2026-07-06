"""Verificación de clave para gestionar historial_jobs.json."""
from __future__ import annotations

import hashlib
import hmac

# SHA-256 de la clave operativa (no almacenar texto plano en repo).
_HISTORIAL_PWD_SHA256 = (
    "2b79e0365307d804c1fe4cadea7983a5786f47ce59d8019783fc5b8717abce2c"
)


def verificar_clave_historial(clave: str) -> bool:
    digest = hashlib.sha256(str(clave or "").encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, _HISTORIAL_PWD_SHA256)
