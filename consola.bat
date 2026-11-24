@echo off
rem Habla con Jarvis escribiendo (para probar comandos sin micrófono).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m asistente consola
pause
