@echo off
REM Publica Consulta_Herinox.py en AutoDXF 2.0 (Z: y UNC).
python "%~dp0sync_autodxf20_herinox.py"
exit /b %ERRORLEVEL%
