@echo off
rem Da permiso a Gmail (solo lectura) y Google Calendar.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m asistente conectar-google
pause
