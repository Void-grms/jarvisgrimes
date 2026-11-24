@echo off
rem Mide el modelo, la voz y tu microfono (guarda datos\diagnostico.txt).
rem Cierra el Jarvis que este corriendo para medir sin interferencias.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 47321 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
".venv\Scripts\python.exe" -m asistente diagnostico
echo.
echo Para volver a usar Jarvis ejecuta iniciar.bat
pause
