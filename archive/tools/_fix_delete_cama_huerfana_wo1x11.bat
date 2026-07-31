@echo off
REM Ejecutar EN EL SERVIDOR 192.168.2.80 (ARGAII) como Administrador local.
REM Borra o repara la carpeta huérfana que bloquea el re-export de nesting.

set "CAMA=C:\Users\Administrator\Desktop\Grupo Arga Metals\ARGA METALS CORPORATE SYSTEM\TANKS\VANTRAN\06_30_2322_TANK_251007\MODEL CORE FILES\W.O. 1 X11\ARGA MODEL CORE\NESTING\CAMA LASER SIN MINI NEST"

echo === Target ===
echo %CAMA%
if not exist "%CAMA%" (
  echo La carpeta ya no existe.
  pause
  exit /b 0
)

echo.
echo [1] takeown...
takeown /F "%CAMA%" /R /A /D S
echo.
echo [2] icacls Full Control Administrators + Users...
icacls "%CAMA%" /grant Administrators:F /T /C
icacls "%CAMA%" /grant Users:F /T /C
icacls "%CAMA%" /grant "%USERNAME%":F /T /C

echo.
echo [3] Borrar carpeta...
rmdir /S /Q "%CAMA%"
if exist "%CAMA%" (
  echo FALLÓ el borrado. Intentando rename a _OLD_...
  ren "%CAMA%" "_OLD_CAMA_LASER_SIN_MINI_NEST"
  if exist "%CAMA%" (
    echo Sigue bloqueada. Revisa handles abiertos (FreeCAD/ANS/Explorer) en el servidor.
    pause
    exit /b 1
  )
  echo Renombrada a _OLD_CAMA_LASER_SIN_MINI_NEST — ya puedes exportar.
) else (
  echo OK: carpeta eliminada. Ya puedes exportar nesting.
)
pause
