@echo off
rem Read-only graphical view of the committee bots. Opens http://127.0.0.1:8780
cd /d "%~dp0.."
start "" http://127.0.0.1:8780
python agents_portal\server.py
pause
