@echo off
cd /d "%~dp0\.."
echo ===========================================
echo  Marcaje AutoDXF - capa IV_MARK_SURFACE_BACK
echo  Altura visible 0.25 in | busca DXF recursivo
echo ===========================================
python "dxf_marcaje\marcar_carpeta_autodxf.py" --pick
echo.
pause
