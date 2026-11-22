@echo off
setlocal
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo Python venv is missing. Please double-click setup.bat first.
  pause
  exit /b 1
)

set "LANIP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "@(Get-NetIPConfiguration).Where({$_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up'}).IPv4Address.IPAddress"`) do set "LANIP=%%i"

echo ============================================================
echo   kaoyan vocab app
echo   On this PC:     http://localhost:8000
if defined LANIP (
  echo   On your phone: http://%LANIP%:8000   (same WiFi)
) else (
  echo   On your phone: run "ipconfig" to find your IPv4 address
)
echo   Keep this window open. Press Ctrl+C to stop.
echo ============================================================
echo.

"backend\.venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir backend

echo.
echo Server stopped.
pause
