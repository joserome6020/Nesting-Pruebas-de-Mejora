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
    try:
        from modules.win_dll_bootstrap import bootstrap_proceso_nesting

        bootstrap_proceso_nesting()
    except Exception:
        pass
    try:
        import faulthandler
        from pathlib import Path

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
        # CWD estable junto al .exe (otras PCs, accesos directos, doble clic).
        os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
    # Pre-cargar stdlib de red ANTES de PySide6: en Python 3.14 + Shiboken,
    # importar urllib.request después de Qt puede colgarse en inspect.getsource
    # (parece freeze; Ctrl+C deja el traceback que veías en herinox_sync).
    import http.client  # noqa: F401
    import urllib.parse  # noqa: F401
    import urllib.request  # noqa: F401

    from PySide6.QtWidgets import QApplication
    from interface.qt.theme import apply_theme
    from interface.qt.main_window import SistemaNestingPro

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("ARGA NESTING SUITE")
    qt_app.setApplicationDisplayName("ARGA NESTING SUITE")

    apply_theme(qt_app)

    window = SistemaNestingPro()

    try:
        if len(sys.argv) > 1:
            arg_path = os.path.abspath(str(sys.argv[1]))
            if os.path.isfile(arg_path) and os.path.splitext(arg_path)[1].lower() in {".arganest", ".navanest"}:
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
