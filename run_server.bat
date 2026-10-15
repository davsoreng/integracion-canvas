@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================================
echo   Iniciando Servidor Web Asistente Canvas (Local)
echo ============================================================
echo.
.\.venv\Scripts\python.exe src\main.py
pause
