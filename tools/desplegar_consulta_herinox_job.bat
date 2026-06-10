@echo off
REM Copia Consulta_Herinox.py al AutoDXF de un job (para PCs sin unidad Z: mapeada).
REM Uso: desplegar_consulta_herinox_job.bat "C:\ruta\al\job\OTC 62179"
setlocal
if "%~1"=="" (
  echo Uso: %~nx0 "C:\ruta\al\job"
  exit /b 1
)
set "JOB=%~1"
set "DEST=%JOB%\AutoDXF"
if not exist "%DEST%" mkdir "%DEST%"
set "ORIGEN=Z:\♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología\7.- Configuración para equipos de Computo\AutoDXF 2.0\Consulta_Herinox.py"
if not exist "%ORIGEN%" (
  REM Fallback: script autocontenido del repo (no el wrapper tools\Consulta_Herinox.py)
  set "ORIGEN=%~dp0..\modules\consulta_herinox_bridge.py"
)
copy /Y "%ORIGEN%" "%DEST%\Consulta_Herinox.py"
python "%DEST%\Consulta_Herinox.py" "%DEST%\herinox_sync.local.json"
echo Listo: %DEST%\herinox_sync.local.json
endlocal
