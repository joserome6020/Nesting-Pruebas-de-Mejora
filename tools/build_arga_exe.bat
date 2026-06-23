@echo off
REM Compila ArgaNestingSuite.exe en cualquier PC con el clon del repo.
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creando entorno virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear .venv. Instala Python 3.10+.
        pause
        exit /b 1
    )
)

echo [INFO] Instalando dependencias y compilando EXE (MSVC se instala solo si falta)...
echo [INFO] La primera vez puede tardar 10-30 min si instala Build Tools C++.
echo [INFO] Si winget/setup fallan, ejecuta este .bat como Administrador.
".venv\Scripts\python.exe" tools\build_arga_exe.py %*
set "RC=%ERRORLEVEL%"
if %RC% neq 0 (
    echo.
    echo [ERROR] Build fallo. Revisa el log arriba.
    echo Si winget no pudo instalar MSVC, instala manualmente:
    echo   https://visualstudio.microsoft.com/visual-cpp-build-tools/
    pause
    exit /b %RC%
)

echo.
echo [OK] Revisa dist\ArgaNestingSuite.exe
pause
endlocal
