@echo off
setlocal
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo Python venv is missing. Please double-click setup.bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   Create administrator account (OPTIONAL)
echo.
echo   You do NOT need this on this PC: Local Mode already unlocks
echo   the Word Bank tab without any login.
echo.
echo   Only run this if you want a real admin login, for example
echo   to access the app from another device or with LOCAL_MODE=0.
echo.
echo   Username: press Enter to use "admin"
echo   Password: YOU type it twice, at least 8 characters.
echo             It is not shown, not saved anywhere in plain text,
echo             and nobody else can see it.
echo ============================================================
echo.

"backend\.venv\Scripts\python.exe" scripts\create_admin.py

echo.
pause
