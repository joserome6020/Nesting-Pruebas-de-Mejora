@echo off
REM Instala psycopg2 en AutoDXF 2.0\_vendor (una vez; todas las PCs de planta lo usan).
setlocal
set "TARGET=Z:\♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología\7.- Configuración para equipos de Computo\AutoDXF 2.0"
if not "%~1"=="" set "TARGET=%~1"
set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0bootstrap_herinox_vendor.py" "%TARGET%"
endlocal
