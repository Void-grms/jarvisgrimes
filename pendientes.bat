@echo off
rem Revisa Gmail y Clementina ahora y muestra el resumen en texto.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m asistente sync
pause
