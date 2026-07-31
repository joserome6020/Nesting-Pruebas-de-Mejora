from pydantic import BaseModel
from typing import List, Optional

class SolicitudCombinacion(BaseModel):
    work_orders_ids: List[str]

class SesionListaLargosIniciar(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str
    piezas_totales: int = 0

class SesionListaLargosAvance(BaseModel):
    orden_id: str
    tipo_orden: str
    piezas_completadas: int

class SesionListaLargosFinalizar(BaseModel):
    orden_id: str
    tipo_orden: str
    piezas_completadas: Optional[int] = None

class ListaLargosTogglePiezaPayload(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str
    material: str
    barra_index: int
    pieza_index: int
    completada: bool

class ListaLargosIniciarPiezaPayload(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str
    material: str
    barra_index: int
    pieza_index: int

class ListaLargosSobrantePayload(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str
    material: str
    barra_index: int
    sobrante_real: Optional[float] = None

class MaterialRequeridoLdGSincronizar(BaseModel):
    orden_id: str
    tipo_orden: str
    plan: dict

class ListaLargosFinalizarPayload(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str

class MaterialValido(BaseModel):
    largo: float
    ancho: float
    grosor: float

class ScriptCorteSeleccionPayload(BaseModel):
    orden_id: str
    tipo_orden: str
    sheet_code: str
    bahia: str
    cama: str
