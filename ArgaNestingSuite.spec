# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Proyectos\\Arga-Nesting-Suite\\main.py'],
    pathex=['C:\\Proyectos\\Arga-Nesting-Suite', 'C:\\Proyectos\\Arga-Nesting-Suite\\interface', 'C:\\Proyectos\\Arga-Nesting-Suite\\modules'],
    binaries=[],
    datas=[('C:\\Proyectos\\Arga-Nesting-Suite\\grupo_arga_cover.jpeg', '.'), ('C:\\Proyectos\\Arga-Nesting-Suite\\generador_verde.FCMacro', '.'), ('C:\\Proyectos\\Arga-Nesting-Suite\\modules', 'modules'), ('C:\\Proyectos\\Arga-Nesting-Suite\\interface', 'interface'), ('C:\\Proyectos\\Arga-Nesting-Suite\\assets', 'assets')],
    hiddenimports=['config', 'tab_files', 'tab_parts', 'tab_sheets', 'tab_nesting', 'nesting_canvas', 'postgres_connector', 'utils_nesting', 'nesting_modals', 'modules.nesting_engine.manager', 'modules.nesting_engine.algorithm', 'modules.nesting_engine.geometry_parser', 'modules.nesting_engine.exporter', 'modules.sheets_manager', 'modules.processed_layers', 'modules.scanner', 'modules.nest_exporter'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ArgaNestingSuite',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Proyectos\\Arga-Nesting-Suite\\build\\arga_icon.ico'],
)
