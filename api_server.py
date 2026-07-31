from fastapi import FastAPI
from api.legacy_core import app as legacy_app

from api.routers import nesting
from api.routers import work_orders
from api.routers import lista_largos
from api.routers import reportes
from api.routers import produccion

app = legacy_app

app.include_router(nesting.router)
app.include_router(work_orders.router)
app.include_router(lista_largos.router)
app.include_router(reportes.router)
app.include_router(produccion.router)