@echo off
setlocal
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo Python venv is missing. Please double-click setup.bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   kaoyan vocab app
echo   Open this address in your browser:  http://localhost:8000
echo   Keep this window open. Press Ctrl+C to stop the server.
echo   Backend auto-creates tables and imports the word bank on start.
echo ============================================================
echo.

"backend\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 --app-dir backend

echo.
echo Server stopped.
pause
