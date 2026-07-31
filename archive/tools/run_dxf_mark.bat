@echo off
cd /d "%~dp0\.."
echo ===========================================
echo  Marcaje stick DXF - capa MARK (0.16 in)
echo  Texto = codigo del nombre (antes de coma)
echo ===========================================
python "tools\run_dxf_mark.py" --pick
echo.
pause
