"""Re-export rutas AutoDXF 2.0 (UNC + Z:) para scripts del repo."""
from modules.consulta_herinox_bridge import (
    AUTODXF20_REL,
    UNC_SHARE_ROOTS,
    autodxf20_candidate_paths,
    resolve_autodxf20_dir,
)

__all__ = [
    "AUTODXF20_REL",
    "UNC_SHARE_ROOTS",
    "autodxf20_candidate_paths",
    "resolve_autodxf20_dir",
]
