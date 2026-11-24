@echo off
rem Vincula WhatsApp Web con el codigo QR (una sola vez).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m asistente conectar-whatsapp
pause
