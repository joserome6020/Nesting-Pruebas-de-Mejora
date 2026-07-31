# Registra el Icon Handler de .arganest (requiere UAC).
# Uso:
#   python tools/register_arganest_icon_handler.py
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

spec = importlib.util.spec_from_file_location("build_arga_exe", ROOT / "tools" / "build_arga_exe.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

dist = ROOT / "dist"
if not (dist / "ARGA NESTING SUITE.exe").exists() and not list(dist.glob("*.exe")):
    print("[WARN] No hay exe en dist/; igual se registran iconos/handler.")
m._copy_file_icos_to_dist(dist)
exe = dist / "ARGA NESTING SUITE.exe"
if not exe.exists():
    # Asocia igual con path placeholder de iconos
    class _Dummy:
        parent = dist
        def resolve(self):
            return dist / "ARGA NESTING SUITE.exe"
    # associate necesita exe real para open command; usa register directo
    ok = m.register_arganest_icon_handler(dist)
else:
    m.associate_extensions(exe)
    ok = True
m.refresh_windows_icon_cache()
print("[OK] listo" if ok else "[WARN] revisa UAC / admin")
