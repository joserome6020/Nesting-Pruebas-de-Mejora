import multiprocessing
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_IFACE = os.path.join(_ROOT, "interface")

for _p in (_ROOT, _IFACE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Default runtime = algorithm_cpp (AGENTS.md). Core nuevo es opt-in:
    # ARGA_NEST_CORE=1 aún no respeta TABLA GAPS placa→pieza (Galv 4.86 mm).
    os.environ.setdefault("ARGA_NEST_CORE", "0")
    os.environ.setdefault("ARGA_NEST_CUDA", "1")
    os.environ.setdefault("ARGA_NEST_WORKER", "1")
    os.environ.setdefault("ARGA_NEST_MODE", os.environ.get("ARGA_NEST_MODE") or "first")
    os.environ.setdefault("ARGA_NEST_KERF_CONTRACT", "identity")
    os.environ.setdefault("ARGA_NEST_REMNANTS_SOURCE", "auto")
    # Remanentes en nest: OFF por default (opt-in). No inventar placas tipo PL-CARB en UI.
    os.environ.setdefault("ARGA_NEST_REMNANTS_IN_NEST", "0")
    os.environ.setdefault("ARGA_NEST_TELEMETRY", "1")
    os.environ.setdefault("ARGA_NEST_AI", "1")  # L1 ranker ON (opt-out: ARGA_NEST_AI=0)
    # Venom OFF por default (cuello de botella en tanques). Opt-in: ARGA_NEST_VENOM=1
    os.environ.setdefault("ARGA_NEST_VENOM", "0")
    os.environ.setdefault("ARGA_NEST_CORRIDOR_FILL", "0")
    # Compact-lite ON: band-close + backfill (Lite = MC propio, no FORCE)
    os.environ.setdefault("ARGA_NEST_COMPACT", "1")
    os.environ.setdefault("ARGA_NEST_BAND_CLOSE", "0")  # viene vía COMPACT
    # Lite rápido: 1 pase MC + 1 renest placa (1 MC). Más calidad: ARGA_LITE_PASSES=2
    os.environ.setdefault("ARGA_LITE_PASSES", "1")
    os.environ.setdefault("ARGA_LITE_PLATE_RENEST", "1")
    os.environ.setdefault("ARGA_LITE_PLATE_RENEST_TRIES", "1")
    os.environ.setdefault("ARGA_LITE_PLATE_RENEST_MC", "1")
    # Lite: meter piezas chicas en orificios grandes del host (post-pase). Opt-out=0.
    os.environ.setdefault("ARGA_LITE_HOLE_FILL", "1")
    os.environ.setdefault("ARGA_LITE_VOID_FIRST", "1")
    try:
        from modules.win_dll_bootstrap import bootstrap_proceso_nesting

        bootstrap_proceso_nesting()
    except Exception:
        pass
    try:
        from modules.nesting_engine import arga_nest_core_bridge as _ancb

        _st = _ancb.core_status()
        _w = _st.get("worker") or {}
        print(
            f"[ANS-CPP] core_active={_st.get('active')} version={_st.get('version')} "
            f"kerf={_st.get('kerf_contract')} worker_active={_w.get('active')} "
            f"cuda_env={os.environ.get('ARGA_NEST_CUDA')}",
            flush=True,
        )
    except Exception as _exc_core:
        print(f"[ANS-CPP] core status unavailable: {_exc_core}", flush=True)
    # Prepara data_dir persistente (%LOCALAPPDATA%\ArgaNestingSuite\data en frozen).
    # Debe correr ANTES del crash log y del chdir para que los mutables (historial,
    # inventario, _config, _logs) sobrevivan a updates y no vivan dentro del bundle.
    try:
        import config as _cfg_boot

        _DATA_DIR = _cfg_boot.bootstrap_data_dir()
    except Exception:
        _DATA_DIR = None
    try:
        import faulthandler
        from pathlib import Path

        if _DATA_DIR:
            _crash_log = Path(_DATA_DIR) / "_logs" / "crash.log"
        else:
            _crash_log = Path(_ROOT) / "_logs" / "crash.log"
        _crash_log.parent.mkdir(parents=True, exist_ok=True)
        _crash_fh = open(_crash_log, "a", encoding="utf-8")
        faulthandler.enable(file=_crash_fh, all_threads=True)
    except Exception:
        pass
    # Log de export DXF y diagnósticos visibles en terminal sin esperar al final del buffer.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        # CWD estable en data_dir (mutables sobreviven a updates; rutas relativas
        # como "_logs/…" o "TEMP_PROCESSED/…" escriben donde deben).
        try:
            if _DATA_DIR:
                os.chdir(_DATA_DIR)
            else:
                os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
        except Exception:
            try:
                os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
            except Exception:
                pass
    # Pre-cargar stdlib de red ANTES de PySide6: en Python 3.14 + Shiboken,
    # importar urllib.request después de Qt puede colgarse en inspect.getsource
    # (parece freeze; Ctrl+C deja el traceback que veías en herinox_sync).
    import http.client  # noqa: F401
    import urllib.parse  # noqa: F401
    import urllib.request  # noqa: F401

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from interface.qt.theme import apply_theme
    from interface.qt.main_window import SistemaNestingPro

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("ARGA NESTING SUITE")
    qt_app.setApplicationDisplayName("ARGA NESTING SUITE")
    try:
        import config as _cfg

        for _rel in (
            os.path.join("assets", "branding", "arga_nesting_logo.png"),
            os.path.join("assets", "branding", "logo_icon1.png"),
        ):
            _icon = _cfg.ruta_recurso(_rel)
            if os.path.isfile(_icon):
                qt_app.setWindowIcon(QIcon(_icon))
                break
    except Exception:
        pass

    apply_theme(qt_app)

    window = SistemaNestingPro()

    try:
        if len(sys.argv) > 1:
            arg_path = os.path.abspath(str(sys.argv[1]))
            if os.path.isfile(arg_path) and os.path.splitext(arg_path)[1].lower() in {
                ".arganest",
                ".navanest",
            }:
                window.after(250, lambda p=arg_path: window.abrir_workspace_arganest_en_arranque(p))

    except Exception:
        pass

    window.show()

    sys.exit(qt_app.exec())

"""
Codigo para app web
npm run dev

Codigo de uvicorn (si WinError 10013: otro proceso usa el puerto; ver netstat -ano | findstr :8000)
uvicorn api_server:app --reload --host 127.0.0.1 --port 8000

MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMWNNNWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMWXOdc;;;:okKWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMNKxl;.........;kNMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMWXOd:'.........,cx0NMMMMMNKKXWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMNKxl,.........:oO0XWMMMMWXOdc;,;lx0NWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMWXOo:'........,cx0NMMMMMMNKko:,'',,'',ckNMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMWKxl,.........;okXWMMMMWWXOdc;'',,,'';cdOXWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMWN0d:'........,cd0NWMMMMNKkoc:,'''',',;lx0NMMMMMMMMMWNK0XWMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMWKkl;.........;lkKWMMMMWXOdc;'''''''',:oOKWMMMMMMMMMWKko:,,:lxOXWMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMWN0dc'........'cd0NWMMMMNKko:,'''',,',;lx0NWMMMMMMMMWN0xl;,'',,,,',;lxKWMMMMMMMMMMMMM
MMMMMMMMMMMMWKkl;.........;lkKWMMMMWX0dc;,',',,',:lokKWMMMMMMMMMWKko:,'',,,,,,,,,,''';kWMMMMMMMMMMMM
MMMMMMMMMMMXo,........':dONWMMMMWKko:,''',',,,,,:x0XWMMMMMMMMMMMXd;,'''',,,,,,,,,,,',':KMMMMMMMMMMMM
MMMMMMMMMMMx.......,lkKWMMMMWX0xl;,''',,'',,,,,,,,;:okKNMMMMMMMMMWXOdc,'',,,,,,,,,,,,':0MMMMMMMMMMMM
MMMMMMMMMMWx.....'oKWMMMMMMMWOl,'','',,,,,,,,,,,,,''',;lx0XWMMMMMMMMMN0d:,,,,,,,,,,,,':0MMMMMMMMMMMM
MMMMMMMMMMMx.....;0MMMMMMMMMMWN0xl;,',,,,,,,,,,,,,,,,,,'',:ox0XWMMMMMMMWKl,,,,,,,,,,,'cKMMMMMMMMMMMM
MMMMMMMMMMMx......c0NMMMMMMMMMMMMWXOdc,'',,,,,,,,,,,,,,,,,'',,:oONMMMMMMMk;',,,,,,,,,'lXMMMMMMMMMMMM
MMMMMMMMMMMx........:oOXWMMMMMMMMMMMWN0xl;,,,,,,,,,,,,,,,,,',,'',lKMMMMMMO;',,,,,,,,,'lXMMMMMMMMMMMM
MMMMMMMMMMMx...........,lxKNMMMMMMMMMMMMWKko:,'',,,,,,,,,,,,,,,,,,xWMMMMMO:',,,,,,,,,'cKMMMMMMMMMMMM
MMMMMMMMMMMx..............':oOXWMMMMMMMMMMMWX0xl;,''',,,,,,,,,,,''oNMMMMMO:',,,,,,,,,'cKMMMMMMMMMMMM
MMMMMMMMMMMx..................,lxKNMMMMMMMMMMMMWKkxo;,,,,,,,,,,,,,dWMMMMMO:',,,,,,,,,'cKMMMMMMMMMMMM
MMMMMMMMMMMx.....................':oOXWMMMMMMMMMMMMWO:',,,,,,,,,,,xWMMMMMO;',,,,,,,,,'cKMMMMMMMMMMMM
MMMMMMMMMMMx............;oo;.........,lxKNMMMMMMMMMMXl',,,,,,,,,,,xWMMMMMO;',,,,,,,,,'cXMMMMMMMMMMMM
MMMMMMMMMMMx............:XWN0d:.........':oONMMMMMMMXl',,,,,,,,,,,xWMMMMMO;',,,,,,,,,'lXMMMMMMMMMMMM
MMMMMMMMMMMx............:XMMMMNx'...........cKMMMMMMXl',,,,,,,,,,,xWMMMMWk;',',,,,,,',oNMMMMMMMMMMMM
MMMMMMMMMMMx............:XMMMMMK:............dWMMMMMKc',,,,,,,,,,,xWMMN0d:,,,,,,,',:okXMMMMMMMMMMMMM
MMMMMMMMMMMx............:XMMMMMK:............dWMMMMMKc',,,,,,,,,,,dKOdc;'',,'',;cdOXWMMMMMMMMMMMMMMM
MMMMMMMMMMMx............:XMMMMMK:............dWMMMMMKc',,,,,,,,,,,;;,''','',:lkKNMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMx............:KMMMMMK:............dWMMMMMKc',,,,,,,,,,,''','',cdOXWMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMx............'kWMMMMK:............dWMMMMMKc',,,,,,,,,,,,',:lx0NMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMx.............,xNMMMK:............dWMMMMMXl',,,,,,,,,',coOXWMMMMMMMMMMMMMWXOKWMMMMMMMMMMM
MMMMMMMMMMMx...............;oxOk;............dWMMMMMXl',,,,'',;lx0NWMMMMMMMMMMMMMN0xc,.oWMMMMMMMMMMM
MMMMMMMMMMMx.................................dWMMMMMXl','',cokKWMMMMMMMMMMMMMWXOo;'....oWMMMMMMMMMMM
MMMMMMMMMMMk'................................dWMMMMMXl';lx0NWMMMMMMMMMMMMMN0xc,.......'kWMMMMMMMMMMM
MMMMMMMMMMMNOl,.........,odl;................dWMMMMMNOkKWMMMMMMMMMMMMMWXOo:'........,lONMMMMMMMMMMMM
MMMMMMMMMMMMMWXOd:'.....:KMWN0o,.............dWMMMMMMMMMMMMMMMMMMMMN0xl,........':oOXWMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMWKxl,..:KMMMMWx'............dWMMMMMMMMMMMMMMMMWXOo:'........,cx0NMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMWXOoxXMMMMM0;............dWMMMMMMMMMMMMMNKxl,.........;okXWMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMK:............dWMMMMMMMMMWXOd:'........,cd0NMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMXc............lNMMMMMMWKxl;.........;okKWMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMXxc,.........'o0KK0Od:'........'cd0NWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMWXko;'........''..........;lkKWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMN0xc,.............'cd0NMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMWXko;.......;lkKWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMN0xooox0NWMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM
MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM

"""
