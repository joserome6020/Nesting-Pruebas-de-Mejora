# Checklist migración Qt nativa — paridad 1:1

**Referencia oficial:** `_ref_oficial/` (clon de https://github.com/GACesarRuiz/Arga-Nesting-Suite.git)

**Objetivo:** Misma funcionalidad que el repo oficial; solo cambia el framework UI (CustomTkinter → PySide6 nativo).

## Cómo comparar

```powershell
# Diff de un módulo contra oficial
fc /n interface\qt\tabs\tab_files.py _ref_oficial\interface\tab_files.py

# Actualizar referencia oficial
cd _ref_oficial && git pull
```

## Estado por módulo

| Módulo oficial | Archivo Qt nativo | Estado | Notas |
|---|---|---|---|
| `main.py` | `main.py` | 🟢 Portado | Usa `interface.qt.main_window` |
| `interface/main_window.py` | `interface/qt/main_window.py` | 🟢 Portado | 4 pestañas Qt nativas |
| `interface/tab_files.py` | `interface/qt/tabs/tab_files.py` | 🟢 Portado | Validar en operación |
| `tab_parts.py` | `interface/qt/tabs/tab_parts.py` | 🟢 Portado | Visor DXF + lista largos |
| `tab_sheets.py` | `interface/qt/tabs/tab_sheets.py` | 🟢 Portado | Filtros QComboBox + Herinox |
| `interface/tab_nesting.py` | `interface/qt/tabs/tab_nesting.py` | 🟢 Portado | Lógica 1:1, UI Qt |
| `interface/nesting_canvas.py` | `interface/qt/nesting_canvas.py` | 🟢 Portado | Matplotlib QtAgg |
| `modules/visualizer.py` | `interface/qt/visualizer.py` | 🟢 Portado | DXF + cotas |
| `interface/nesting_modals.py` | `interface/qt/dialogs/nesting_modals.py` | 🟢 Portado | QDialog |
| `interface/nesting_lote_editor.py` | `interface/qt/dialogs/lote_editor.py` | 🟢 Portado | QTableWidget |
| `interface/responsive_layout.py` | (layouts Qt inline) | 🟢 N/A | QLayout nativo |

## Flujos críticos a validar 1:1

- [ ] Arranque app + sync Herinox
- [ ] FILES: importar job individual
- [ ] FILES: importar SWO desde PostgreSQL
- [ ] PARTS: tabla + visor DXF + lista de largos
- [ ] SHEETS: filtros + sync Herinox + remanentes
- [ ] NESTING: ejecutar motor + canvas interactivo
- [ ] NESTING: export DXF/PDF + workspace .arganest
- [ ] NESTING: renest, transferencias, plasma compensate
- [ ] Popup progreso + cancelación
- [ ] Abrir .arganest por doble clic

## Archivos shim — eliminados

- ~~`interface/qt_bootstrap.py`~~
- ~~`interface/ctk_qt/`~~
- ~~`interface/tk_shim.py`~~
- ~~`interface/responsive_layout_qt.py`~~

## Paridad funcional (auditoría 2026-06-10)

Corregido vs original:
- `messagebox.askyesno` → `QMessageBox.question` (renest calibre, export 3D)
- `nesting_workspace.py` compatible Qt/Tk (`cmb_opt`, `cmb_lotes`, `lbl_cantidad`)
- `ProgressDialog.force_close()` — ya no pide cancelar al terminar nesting/export
- Pan DXF en PARTS (`width()`/`height()` en lugar de `winfo_*`)
- Filtros `QFileDialog` para workspace/PDF
- Visor Herinox inline restaurado (tabla Antes/Después)

## Pendiente

1. Validar flujos críticos en operación real (Python 3.13/3.14 + PySide6)
2. Archivar copias Tk legacy en `interface/` (referencia en `_ref_oficial/`)
3. Actualizar `tools/build_arga_exe.py` para empaquetar Qt
4. Acoplar minimizar ventana ↔ popup progreso (cosmético, original Windows)
