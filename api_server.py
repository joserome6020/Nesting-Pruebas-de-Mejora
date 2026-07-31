"""Fachada de compatibilidad y punto de entrada de la API FastAPI.

Los consumidores heredados todavía importan helpers de ``api_server``. El
backend modular conserva ese contrato delegando dichos atributos a
``api.legacy_core`` mientras se migran los consumidores a módulos de dominio.
"""

from typing import Any

from api import legacy_core

from api.routers import nesting
from api.routers import work_orders
from api.routers import lista_largos
from api.routers import reportes
from api.routers import produccion

app = legacy_core.app

app.include_router(nesting.router)
app.include_router(work_orders.router)
app.include_router(lista_largos.router)
app.include_router(reportes.router)
app.include_router(produccion.router)

# Replica la semántica de ``from api_server import *`` del servidor monolítico:
# los nombres públicos históricos continúan resolviéndose con __getattr__.
__all__ = [name for name in dir(legacy_core) if not name.startswith("_")]


def __getattr__(name: str) -> Any:
    """Conserva temporalmente los imports de helpers del servidor monolítico."""
    try:
        return getattr(legacy_core, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__() -> list[str]:
    """Expone los atributos delegados a inspectores y herramientas de desarrollo."""
    return sorted(set(globals()) | set(dir(legacy_core)))