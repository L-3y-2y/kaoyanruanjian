@echo off
setlocal
REM Allow LAN access to port 8000 (for phone access to the kaoyan vocab app).
REM Usage: right-click this file -> Run as administrator.

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo [ERROR] Please right-click this file and choose "Run as administrator".
  pause
  exit /b 1
)

echo Adding firewall rule to allow LAN access to port 8000...
netsh advfirewall firewall delete rule name="kaoyan-vocab-8000" >nul 2>&1
netsh advfirewall firewall add rule name="kaoyan-vocab-8000" dir=in action=allow protocol=TCP localport=8000 remoteip=localsubnet

echo.
echo Done! Now connect your phone to the SAME WiFi and open the URL shown in start.bat.
echo.
pause
