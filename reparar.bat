@echo off
rem Reinstala/actualiza las librerias y descarga los modelos de voz que pide config.toml.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m asistente preparar
pause
