"""Smoke test PDF consumo en piso."""
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reportlab.pdfgen import canvas

from reporte_pdf_lista_largos import FONT_REG
from reporte_pdf_nesteo_largos_piso import (
    _wrap_item_lines,
    filas_csv_a_accesorios,
    generar_pdf_nesteo_largos_piso,
)

c = canvas.Canvas(BytesIO())
w = 55
for n in ("62174-1251-P05", "62174-1248-P27", "SP-771_1"):
    print(n, "->", _wrap_item_lines(c, n, w, FONT_REG, 8))

filas = filas_csv_a_accesorios(
    [
        {
            "nombre": "62174-1251-P05.ipt",
            "clasificacion": "ANG022",
            "largo_in": 121,
            "cantidad_base": 1,
        },
        {
            "nombre": "62174-1248-P27_Default_As Machined.ipt",
            "clasificacion": "PTR016",
            "largo_in": 14,
            "cantidad_base": 2,
        },
    ]
)
print("items:", [f["item"] for f in filas])

snap = {
    "filas_accesorios": filas,
    "job": "62174",
    "titulo": "ACCESORIOS LARGOS TANK (62174)",
    "barras_piso": [
        {
            "etiqueta": "#1/1 Angulo ANG022 240 comercial",
            "material": "ANG022 ANGULO perfil A 36 2 X 2 X 0.25 IN",
            "barra": {
                "largo_stock": 240,
                "remanente_show": 63.25,
                "cortes": [
                    {"nombre": "62174-1251-P05", "largo": 121},
                    {"nombre": "62174-1248-P27", "largo": 14},
                    {"nombre": "62174-1248-P27", "largo": 14},
                    {"nombre": "62174-1248-P37", "largo": 8.5},
                ],
            },
        }
    ],
}
out = ROOT / "_test_final.pdf"
generar_pdf_nesteo_largos_piso(snap, ruta_pdf=str(out))
print("PDF ok:", out)
