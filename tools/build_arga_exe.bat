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

echo [INFO] Instalando dependencias y compilando EXE...
".venv\Scripts\python.exe" tools\build_arga_exe.py %*
set "RC=%ERRORLEVEL%"
if %RC% neq 0 (
    echo.
    echo [ERROR] Build fallo. Si falta MSVC prueba:
    echo   .venv\Scripts\python.exe tools\build_arga_exe.py --install-msvc
    pause
    exit /b %RC%
)

echo.
echo [OK] Revisa dist\ArgaNestingSuite.exe
pause
endlocal
