# Levanta api_server.py de ARGA Nesting Suite (misma BD que el export a servidor).
# Uso: .\tools\run_api_server.ps1
# Para la web: en NESTING-APP-WEB ejecutar npm run dev:arga

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not $env:NESTING_DB_HOST) { $env:NESTING_DB_HOST = "192.168.2.80" }
if (-not $env:NESTING_DB_NAME) { $env:NESTING_DB_NAME = "nestingpro_db" }
if (-not $env:NESTING_DB_USER) { $env:NESTING_DB_USER = "postgres" }
if (-not $env:NESTING_DB_PASSWORD) { $env:NESTING_DB_PASSWORD = "nesting123" }
if (-not $env:NESTING_DB_PORT) { $env:NESTING_DB_PORT = "5433" }

Write-Host "API ARGA -> $($env:NESTING_DB_HOST):$($env:NESTING_DB_PORT)/$($env:NESTING_DB_NAME)"
Write-Host "Escuchando en http://127.0.0.1:8000"

python -X utf8 -m uvicorn api_server:app --reload --host 127.0.0.1 --port 8000
