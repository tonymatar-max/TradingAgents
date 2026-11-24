@echo off
REM One setup for the server. Double-click = DRY RUN (no orders).
REM Demo orders:  .\SETUP.bat live      (in PowerShell)   or   SETUP.bat live   (in cmd)
REM Installs/updates everything and starts MT5, the USTEC + gold v2 bots, the ORB dry run and the portal at logon.
cd /d "%~dp0"
if /i "%1"=="live" (
  powershell -NoProfile -ExecutionPolicy Bypass -File setup_server.ps1 -LiveOrders
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File setup_server.ps1
)
pause
