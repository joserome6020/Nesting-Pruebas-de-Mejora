"""Verifica que el worker de nesting no importe psycopg2."""
import concurrent.futures
import os
import sys
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IFACE = os.path.join(_ROOT, "interface")
for _p in (_ROOT, _IFACE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def worker():
    import sys as _sys

    if "psycopg2" in _sys.modules or "psycopg2._psycopg" in _sys.modules:
        return "FAIL: psycopg2 cargado en worker"
    from modules.nesting_engine.manager import _procesar_grupo_parallel_worker

    job = (
        "0.0747_A 36",
        [],
        [],
        0.3,
        0.15,
        "OPTIMIZAR LARGO Y ANCHO",
        "INFERIOR IZQUIERDA",
        "WO1",
        "arga_base",
    )
    _procesar_grupo_parallel_worker(job)
    if "psycopg2" in _sys.modules:
        return "FAIL: psycopg2 cargado tras worker"
    return "ok"


def run():
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as ex:
        return [f.result() for f in [ex.submit(worker), ex.submit(worker)]]


if __name__ == "__main__":
    err = []
    done = threading.Event()

    def bg():
        try:
            print(run())
        except Exception as exc:
            err.append(exc)
        finally:
            done.set()

    threading.Thread(target=bg, daemon=True).start()
    done.wait(timeout=120)
    if err:
        raise err[0]
