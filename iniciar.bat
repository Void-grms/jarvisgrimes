@echo off
rem Arranca el asistente con consola visible (para ver tiempos y errores).
rem Si ya habia un Jarvis corriendo (el que arranca con Windows), lo cierra primero para usar siempre la version nueva.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 47321 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
".venv\Scripts\python.exe" -m asistente run
pause
