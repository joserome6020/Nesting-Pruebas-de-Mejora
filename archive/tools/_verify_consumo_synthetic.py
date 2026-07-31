"""Pruebas sintéticas de consumo MRL (sin BD)."""
import sys

sys.path.insert(0, r"c:\Proyectos\New Arga Nesting Suite")

from tools._verify_consumo_largos import auditar_plan


def caso(nombre, barras):
    plan = {"data": {nombre: barras}}
    return auditar_plan(plan, nombre)


def main():
    ok = True

    # Caso usuario: 480 con 2×127.5
    ok &= caso(
        "Solera 4 x 1/2",
        [
            {
                "largo_stock": 480,
                "source": "STOCK",
                "cortes": [
                    {"largo": 127.5, "nombre": "P02"},
                    {"largo": 127.5, "nombre": "P03"},
                ],
            },
        ],
    )

    ok &= caso(
        "Solera 4 x 1/2",
        [
            {
                "largo_stock": 480,
                "source": "STOCK",
                "cortes": [{"largo": 100 + i, "nombre": f"P{i}"} for i in range(2)],
            }
            for _ in range(7)
        ],
    )

    # Mezcla 480 + 240 (material real angulo si existe en catálogo)
    ok &= caso(
        "Angulo 2 x 1/4",
        [
            {"largo_stock": 480, "source": "STOCK", "cortes": [{"largo": 200, "nombre": "A"}]},
            {"largo_stock": 240, "source": "STOCK", "cortes": [{"largo": 80, "nombre": "B"}]},
        ],
    )

    # Largo no múltiplo exacto
    ok &= caso(
        "Solera 3 x 1/2",
        [{"largo_stock": 350, "source": "STOCK", "cortes": [{"largo": 120, "nombre": "X"}]}],
    )

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
