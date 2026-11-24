@echo off
rem Prueba combinaciones de reconocimiento de voz y modelo en esta laptop y guarda las mas rapidas.
rem Cierra el Jarvis que este corriendo para medir sin interferencias.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 47321 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
".venv\Scripts\python.exe" -m asistente optimizar
echo.
echo Para volver a usar Jarvis ejecuta iniciar.bat
pause
