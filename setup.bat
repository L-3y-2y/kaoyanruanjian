@echo off
setlocal
cd /d "%~dp0"

set "PYEXE="
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
  if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)
if "%PYEXE%"=="" set "PYEXE=python"

echo [1/4] Creating Python venv in backend\.venv ...
if not exist "backend\.venv\Scripts\python.exe" (
  "%PYEXE%" -m venv "backend\.venv"
  if errorlevel 1 goto :fail
)

echo [2/4] Installing FastAPI / uvicorn / PyMySQL / python-dotenv ...
"backend\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r "backend\requirements.txt"
if errorlevel 1 goto :fail

echo [3/4] Installing Vue 3 / Vite ...
pushd frontend
call npm install
if errorlevel 1 goto :fail

echo [4/4] Building frontend dist ...
call npm run build
if errorlevel 1 goto :fail
popd

echo.
echo ===== SETUP DONE =====
echo Now double-click start.bat, then open http://localhost:8000
echo.
pause
exit /b 0

:fail
echo.
echo ===== SETUP FAILED =====
echo Read the error above. Common causes: no internet, or Python not found.
echo.
pause
exit /b 1
