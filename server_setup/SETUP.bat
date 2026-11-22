@echo off
REM Double-click: installs everything and starts the bots in DRY RUN (no orders).
REM For demo orders run:  SETUP.bat live
cd /d "%~dp0"
if /i "%1"=="live" (
  powershell -NoProfile -ExecutionPolicy Bypass -File setup_server.ps1 -LiveOrders
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File setup_server.ps1
)
pause
