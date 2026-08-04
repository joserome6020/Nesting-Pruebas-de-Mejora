"""
Test aislado: verifica que Venom realmente mueve piezas Shapely.
Simula exactamente lo que manager.py le pasa a venom_ai.
"""
import sys, os
sys.path.insert(0, r"c:\Proyectos\New Arga Nesting Suite")
os.chdir(r"c:\Proyectos\New Arga Nesting Suite")

from shapely.geometry import box as shapely_box
from shapely import affinity

# Importar como lo hace manager.py (paquete)
from modules.nesting_engine import venom_ai

print("=" * 60)
print("TEST 1: Verificar que venom_core se importa correctamente")
print("=" * 60)
try:
    from modules.nesting_engine import venom_core
    print(f"  OK: venom_core importado. Tipo: {type(venom_core)}")
    print(f"  compact_plate disponible: {hasattr(venom_core, 'compact_plate')}")
except ImportError as e:
    print(f"  FALLO: {e}")
    sys.exit(1)

print()
print("=" * 60)
print("TEST 2: Llamar compact_plate directamente con datos simples")
print("=" * 60)
# Pieza 1: en (100, 100) a (200, 200)  -- lejos del origen
# Pieza 2: en (300, 100) a (400, 200)  -- aún más lejos
pieces = [
    (0, 100.0, 100.0, 200.0, 200.0),
    (1, 300.0, 100.0, 400.0, 200.0),
]
results = venom_core.compact_plate(pieces, -1.0, -1.0, 3.0)
print(f"  Resultados del C++: {results}")
for (idx, sx, sy) in results:
    print(f"  Pieza {idx}: shift_x={sx:.2f}, shift_y={sy:.2f}")
    if sx != 0 or sy != 0:
        print(f"    [OK] SE MOVIO!")
    else:
        print(f"    [FAIL] NO se movio")

print()
print("=" * 60)
print("TEST 3: Simular exactamente lo que manager.py hace")
print("=" * 60)

# Crear una hoja con piezas Shapely reales (como manager las crea)
hoja = {
    "placa_w": 1200.0,
    "placa_h": 2400.0,
    "kerf_usado": 0.118,  # pulgadas
    "piezas": []
}

# Pieza 1: rectángulo de 100x100 en posición (200, 200)
p1_poly = shapely_box(200, 200, 300, 300)
hoja["piezas"].append({
    "nombre": "PIEZA-TEST-001",
    "area": p1_poly.area,
    "poly": p1_poly,
    "poly_exact": p1_poly,
})

# Pieza 2: rectángulo de 100x100 en posición (500, 200)
p2_poly = shapely_box(500, 200, 600, 300)
hoja["piezas"].append({
    "nombre": "PIEZA-TEST-002",
    "area": p2_poly.area,
    "poly": p2_poly,
    "poly_exact": p2_poly,
})

print(f"  Pieza 1 ANTES: bounds={hoja['piezas'][0]['poly'].bounds}")
print(f"  Pieza 2 ANTES: bounds={hoja['piezas'][1]['poly'].bounds}")

# Llamar a Venom
venom_ai.apply_smart_polisher(hoja, "test_manual")

print(f"  Pieza 1 DESPUÉS: bounds={hoja['piezas'][0]['poly'].bounds}")
print(f"  Pieza 2 DESPUÉS: bounds={hoja['piezas'][1]['poly'].bounds}")

s1x = hoja["piezas"][0].get("shift_x", 0)
s1y = hoja["piezas"][0].get("shift_y", 0)
s2x = hoja["piezas"][1].get("shift_x", 0)
s2y = hoja["piezas"][1].get("shift_y", 0)

print(f"  Pieza 1 shift: ({s1x:.2f}, {s1y:.2f})")
print(f"  Pieza 2 shift: ({s2x:.2f}, {s2y:.2f})")

if s1x != 0 or s1y != 0 or s2x != 0 or s2y != 0:
    print("\n  [OK OK OK] VENOM ESTA FUNCIONANDO! Las piezas se movieron.")
else:
    print("\n  [FAIL FAIL FAIL] VENOM NO HIZO NADA. Las piezas no se movieron.")

# Verificar archivos de peso
from pathlib import Path
for p in [
    Path("cache/hive_mind_venom"),
    Path("cache/hive_mind_eddie"),
    Path("modules/nesting_engine/venom_cache"),
    Path("modules/nesting_engine/eddie_cache"),
]:
    if p.exists():
        files = list(p.iterdir())
        print(f"\n  Carpeta {p}: {len(files)} archivos")
        for f in files:
            print(f"    - {f.name} ({f.stat().st_size} bytes)")
