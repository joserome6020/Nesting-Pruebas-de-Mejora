# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['config', 'postgres_connector', 'utils_nesting', 'nesting_workspace', 'nesting_modals', 'nesting_canvas', 'responsive_layout', 'reporte_pdf_nesting', 'catalogo_largos', 'lista_largos_material_requerido', 'interface.largos_nesting_service', 'modules.lista_largos_importer', 'interface.qt.main_window', 'interface.qt.theme', 'interface.qt.thread_bridge', 'interface.qt.progress_dialog', 'interface.qt.layout_helpers', 'interface.qt.ui_mixins', 'interface.qt.export_paths', 'interface.qt.nesting_canvas', 'interface.qt.nesting_graphics', 'interface.qt.visualizer', 'interface.qt.cad_graphics_view', 'interface.qt.cad_dimension_items', 'interface.qt.cad_snap', 'interface.qt.dxf_part_loader', 'interface.qt.dxf_part_geometry', 'interface.qt.curve_refine', 'interface.qt.dxf_hifi_path', 'interface.qt.mpl_utils', 'interface.qt.visor_diag', 'interface.qt.tabs.tab_files', 'interface.qt.tabs.tab_parts', 'interface.qt.tabs.tab_sheets', 'interface.qt.tabs.tab_nesting', 'interface.qt.tabs.tab_nesting_ui', 'interface.qt.dialogs.nesting_modals', 'interface.qt.dialogs.lote_editor', 'interface.qt.dialogs.largos_nesting_modal', 'interface.qt.widgets.herinox_switch', 'interface.qt.widgets.largos_tira_canvas', 'interface.qt.widgets.largos_perfil_draw', 'interface.autodxf_metadata', 'interface.material_colors', 'modules.nesting_engine', 'modules.nesting_engine.manager', 'modules.nesting_engine.algorithm_bridge', 'modules.nesting_engine.algorithm_cpp', 'modules.nesting_engine.nest_optimization', 'modules.nesting_engine.cu_largos_nesting', 'modules.nesting_engine.sheet_integrity', 'modules.nesting_engine.efficiency_metrics', 'modules.nesting_engine.rtz_overlays', 'modules.nesting_engine.geometry_parser', 'modules.nesting_engine.exporter', 'modules.nesting_engine.api_client', 'modules.consulta_herinox_bridge', 'modules.sheets_manager', 'modules.processed_layers', 'modules.scanner', 'modules.nest_exporter', 'modules.plates_inventory', 'modules.plasma_compensator', 'modules.herinox_sync', 'pandas', 'openpyxl']
hiddenimports += collect_submodules('PySide6.QtCore')
hiddenimports += collect_submodules('PySide6.QtGui')
hiddenimports += collect_submodules('PySide6.QtWidgets')
hiddenimports += collect_submodules('shapely')
hiddenimports += collect_submodules('matplotlib.backends.backend_qtagg')


a = Analysis(
    ['C:\\Proyectos\\New Arga Nesting Suite\\main.py'],
    pathex=['C:\\Proyectos\\New Arga Nesting Suite', 'C:\\Proyectos\\New Arga Nesting Suite\\interface', 'C:\\Proyectos\\New Arga Nesting Suite\\modules'],
    binaries=[('C:\\Proyectos\\New Arga Nesting Suite\\modules\\nesting_engine\\algorithm_cpp.pyd', 'modules/nesting_engine')],
    datas=[('C:\\Proyectos\\New Arga Nesting Suite\\grupo_arga_cover.jpeg', '.'), ('C:\\Proyectos\\New Arga Nesting Suite\\generador_verde.FCMacro', '.'), ('C:\\Proyectos\\New Arga Nesting Suite\\modules', 'modules'), ('C:\\Proyectos\\New Arga Nesting Suite\\interface', 'interface'), ('C:\\Proyectos\\New Arga Nesting Suite\\assets', 'assets'), ('C:\\Proyectos\\New Arga Nesting Suite\\inventario_remanentes.csv', '.')],
    hiddenimports=hiddenimports,
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
    icon=['C:\\Proyectos\\New Arga Nesting Suite\\build\\arga_icon.ico'],
)
