@echo off
cd /d "%~dp0"
title Arga Nesting Suite - Launcher Unificado
color 0A

echo.
echo  =============================================
echo  =     ARGA NESTING SUITE - LANZADOR V5     =
echo  =          API + Interfaz Grafica           =
echo  =============================================
echo.

REM -----------------------------------------------
REM 1. Verificar Python
REM -----------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no fue encontrado en el sistema.
    echo         Instala Python 3.10+ y asegurate de que este en el PATH.
    pause
    exit /b 1
)
echo [OK] Python detectado.

REM -----------------------------------------------
REM 2. Buscar o crear entorno virtual
REM -----------------------------------------------
set "VENV_DIR=venv"

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [INFO] Creando entorno virtual en %VENV_DIR%...
    python -m venv %VENV_DIR%
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

call %VENV_DIR%\Scripts\activate.bat
echo [OK] Entorno virtual activado.

REM -----------------------------------------------
REM 3. Instalar dependencias (solo si faltan)
REM -----------------------------------------------
echo [INFO] Verificando dependencias...
pip install fastapi uvicorn psycopg2-binary pydantic ezdxf matplotlib reportlab pandas openpyxl shapely numpy customtkinter >nul 2>&1
echo [OK] Dependencias listas.

REM -----------------------------------------------
REM 4. Levantar API Server en segundo plano
REM -----------------------------------------------
echo.
echo [INFO] Levantando API Server (puerto 8000)...
start "NestingPro API Server" /min cmd /c "%VENV_DIR%\Scripts\python.exe -m uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload"

REM Esperar 3 segundos para que el server arranque
timeout /t 3 /nobreak >nul
echo [OK] API Server corriendo en http://0.0.0.0:8000

REM -----------------------------------------------
REM 5. Abrir Interfaz Grafica
REM -----------------------------------------------
echo.
echo [INFO] Abriendo Arga Nesting Suite...
echo =============================================
echo.
%VENV_DIR%\Scripts\python.exe main.py



REM -----------------------------------------------
REM 6. Al cerrar la interfaz, limpiar el servidor
REM -----------------------------------------------
echo.
echo [INFO] Interfaz cerrada. Deteniendo API Server...
taskkill /fi "WINDOWTITLE eq NestingPro API Server" /f >nul 2>&1
echo [OK] Todo limpio. Hasta pronto!
pause
